"""Refresh heat-index/data.json.

The page scores heat = clamp(50 + 20 * z, 0, 100). This script only replaces
the inputs that feed that formula.

Buffett score: the timely estimate, not the official Current Market Valuation
print. The estimate takes CMV's stated total market value on its official
date, scales it by the Wilshire 5000 Total Market Index move since that
date, and divides by the latest GDP (FRED series GDP, or the BEA table FRED
republishes). On 30 Jun 2026 that Wilshire close implied about $1.04 billion
of market value per index point, in line with Wilshire's own scaling, so the
index is the right one to carry CMV's market value forward. The official
percentage stays in the file and on the page, and does not enter the score,
because it is still the 30 Jun GDPNow vintage.

MS Index: the msindex.net reading. Its mean and sample standard deviation are
the post-1950 annual observations (1 January), the same shape of norm the
other pillars use. There is no separate 0-100 mapping.
"""

import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTHS_SHORT = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

SSL_CTX = ssl.create_default_context()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}
ET_ZONE = ZoneInfo("America/New_York")

# Wilshire's own note, cited by Current Market Valuation: by 2020 one index
# point was about $1.05 billion of market cap, after drifting off the original
# $1 billion. The band rejects a total-return index or a different base.
WILSHIRE_DOLLARS_PER_POINT = (0.80e9, 1.30e9)
NCB_SERIES = "FL103164106.Q"
NCB_STALE_AFTER_RELEASE_DAYS = 7
NCB_MAX_AGE_DAYS = 200
MS_STALE_DAYS = 60
PRICE_STALE_DAYS = 10
MULTPL_STALE_DAYS = 14
SCALE_ANCHOR_STALE_DAYS = 45

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def field(value, as_of, source, **extra):
    item = {"value": value, "as_of": as_of, "source": source}
    item.update(extra)
    return item


def blank_file():
    return {
        "cape": field(None, None, "Not fetched yet.", cadence="daily"),
        "pe": field(None, None, "Not fetched yet.", cadence="daily"),
        "buffett": field(None, None, "Not fetched yet.", cadence="quarterly", feeds_score=False),
        "buffett_estimate": field(None, None, "Not fetched yet.", cadence="daily", feeds_score=False),
        "ncb": field(None, None, "Not fetched yet.", cadence="quarterly"),
        "ms": field(None, None, "Not fetched yet.", cadence="daily"),
        "ms_raw": field(None, None, "msindex.net", cadence="daily"),
    }


def migrate(raw):
    """Accept the older flat file, which stamped one date on every series."""
    if not raw or isinstance(raw.get("cape"), dict):
        data = blank_file()
        if raw:
            data.update(raw)
        return data
    shared = raw.get("date")
    data = blank_file()
    for key in ("cape", "pe", "buffett", "ncb", "ms"):
        if raw.get(key) is not None:
            data[key] = field(
                raw[key], None,
                "Carried over from the old file. The old single date was not a per-series date.",
            )
    if shared and isinstance(data.get("cape"), dict):
        data["cape"]["source"] += " Old file date was " + str(shared) + "."
    return data


def parse_long_date(text):
    parts = text.replace(",", "").split()
    if len(parts) != 3:
        return None
    month = MONTHS.get(parts[0].lower())
    if not month:
        return None
    return date(int(parts[2]), month, int(parts[1])).isoformat()


def parse_month_day(text, today):
    """A month and day with no year, placed next to today."""
    parts = text.replace(",", "").split()
    if len(parts) != 2:
        return None
    month = MONTHS_SHORT.get(parts[0].lower()[:3])
    if not month:
        return None
    day = int(parts[1])
    candidate = date(today.year, month, day)
    if candidate - today > timedelta(days=200):
        candidate = date(today.year - 1, month, day)
    elif today - candidate > timedelta(days=200):
        candidate = date(today.year + 1, month, day)
    return candidate


def quarter_end(year, quarter):
    month_end = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    month, day = month_end[quarter]
    return date(year, month, day)


def fred_quarter(iso):
    """FRED stamps a quarter on its first day. Return the quarter-end date."""
    year, month, _day = (int(part) for part in iso.split("-"))
    quarter = (month - 1) // 3 + 1
    return quarter_end(year, quarter), f"{year}Q{quarter}"


def z1_quarter(label):
    """'2026:Q2' -> (date(2026, 6, 30), '2026Q2')."""
    year_text, quarter_text = label.split(":Q")
    year, quarter = int(year_text), int(quarter_text)
    return quarter_end(year, quarter), f"{year}Q{quarter}"


def bea_quarter(label):
    """'2026Q3' -> quarter-end date."""
    year, quarter = int(label[:4]), int(label[-1])
    return quarter_end(year, quarter), f"{year}Q{quarter}"


def age_days(as_of, today):
    if not as_of:
        return None
    return (today - date.fromisoformat(as_of)).days


def round_places(value, places):
    return round(float(value), places)


def fetch_bytes(url, attempts=4, timeout=20):
    last = None
    delay = 1.0
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, context=SSL_CTX, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(delay)
                delay *= 2
    raise last


def fetch_text(url, attempts=4, timeout=20):
    return fetch_bytes(url, attempts=attempts, timeout=timeout).decode("utf-8", "replace")


def shorten(exc):
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:180]


def yahoo_closes(symbol, start):
    """Daily closes as (YYYY-MM-DD, close), using the New York session date."""
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=ET_ZONE).timestamp()) - 10 * 86400
    period2 = int(datetime.now(ET_ZONE).timestamp()) + 2 * 86400
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?period1={period1}&period2={period2}&interval=1d"
    )
    payload = json.loads(fetch_text(url, attempts=3, timeout=20))
    result = payload["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0]["close"]
    pairs = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        session = datetime.fromtimestamp(stamp, ET_ZONE).date().isoformat()
        pairs.append((session, float(close)))
    return drop_open_session(pairs)


def drop_open_session(pairs, now=None):
    """Ignore today's bar until 16:15 New York time, when the close is in."""
    if not pairs:
        return pairs
    now = now or datetime.now(ET_ZONE)
    close_at = now.replace(hour=16, minute=15, second=0, microsecond=0)
    if pairs[-1][0] == now.date().isoformat() and now < close_at:
        return pairs[:-1]
    return pairs


def close_on_or_before(pairs, iso):
    chosen = None
    for session, close in pairs:
        if session <= iso:
            chosen = (session, close)
        else:
            break
    return chosen


def latest_close(pairs):
    return pairs[-1] if pairs else None


def dollars_per_point(market_cap_trillions, wilshire_close):
    return market_cap_trillions * 1e12 / wilshire_close


def wilshire_scale_ok(market_cap_trillions, wilshire_close):
    low, high = WILSHIRE_DOLLARS_PER_POINT
    per_point = dollars_per_point(market_cap_trillions, wilshire_close)
    return low <= per_point <= high


def buffett_estimate(market_cap_trillions, wilshire_base, wilshire_now, gdp_billions):
    """CMV market value, scaled by the Wilshire move, over GDP in billions."""
    market_cap_now = market_cap_trillions * (wilshire_now / wilshire_base)
    return market_cap_now / (gdp_billions / 1000.0) * 100.0


def mean_and_sample_sd(values):
    count = len(values)
    if count < 2:
        raise ValueError("need at least two observations")
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    return mean, variance ** 0.5


def annual_ms_baseline(points):
    """Post-1950 1 January observations. The intra-year point is not in the norm."""
    annual = [
        point for point in points
        if point["date"] >= "1950-01-01" and point["date"].endswith("-01-01")
    ]
    if len(annual) < 40:
        raise ValueError(f"only {len(annual)} annual MS observations since 1950")
    mean, sd = mean_and_sample_sd([point["index"] for point in annual])
    return {
        "start": annual[0]["date"],
        "end": annual[-1]["date"],
        "n": len(annual),
        "mean": round_places(mean, 8),
        "sd": round_places(sd, 8),
        "rule": (
            "Sample standard deviation of msindex.net observations dated 1 January, "
            "from 1950 through the latest 1 January. The intra-year mark-to-market "
            "point is the current reading and is not part of the norm."
        ),
    }


def parse_fred_csv(text):
    rows = []
    for line in text.splitlines():
        if not line or line.lower().startswith("date") or line.lower().startswith("observation"):
            continue
        parts = line.split(",")
        if len(parts) < 2 or parts[1] in ("", "."):
            continue
        rows.append((parts[0].strip(), float(parts[1])))
    if not rows:
        raise ValueError("FRED file had no observations")
    return rows


def parse_z1_ncb(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        name = None
        for info in archive.namelist():
            if not info.lower().endswith(".csv"):
                continue
            with archive.open(info) as handle:
                header = handle.readline().decode("utf-8", "replace")
            if NCB_SERIES in header:
                name = info
                break
        if not name:
            raise ValueError(f"{NCB_SERIES} was not in the Z.1 CSV package")
        text = archive.read(name).decode("utf-8", "replace")
    lines = [line for line in text.splitlines() if line.strip()]
    header = next(line.split(",") for line in lines if NCB_SERIES in line)
    column = header.index(NCB_SERIES)
    data_rows = []
    for line in lines:
        cells = line.split(",")
        if cells and re.fullmatch(r"\d{4}:Q[1-4]", cells[0]):
            data_rows.append(cells)
    if not data_rows:
        raise ValueError("Z.1 file had no quarterly rows")
    last = data_rows[-1]
    as_of, quarter = z1_quarter(last[0])
    return float(last[column]), as_of, quarter


def parse_next_z1_release(html):
    match = re.search(
        r"next Z\.1 release will be [A-Za-z]+, ([A-Za-z]+ \d{1,2}, \d{4})",
        html,
    )
    if not match:
        return None
    return parse_long_date(match.group(1))


def parse_cmv_buffett(html):
    match = re.search(
        r"As of ([A-Za-z]+ \d{1,2}, \d{4}).{0,80}?Buffett Indicator as ([0-9.]+)%",
        html,
        re.S,
    )
    if not match:
        match = re.search(
            r"As of ([A-Za-z]+ \d{1,2}, \d{4}) we calculate the Buffett Indicator as ([0-9.]+)%",
            html,
        )
    if not match:
        raise ValueError("Buffett sentence not found")
    market = re.search(r"Total US Stock Market Value = \$([0-9.]+)T", html)
    gdp = re.search(r"Annualized GDP = \$([0-9.]+)T", html)
    if not market or not gdp:
        raise ValueError("Buffett market-cap or GDP line not found")
    return {
        "as_of": parse_long_date(match.group(1)),
        "value": float(match.group(2)),
        "market_cap_trillions": float(market.group(1)),
        "gdp_trillions": float(gdp.group(1)),
    }


def parse_cmv_public_next(html, today):
    match = re.search(r"On ([A-Z][a-z]{2,8}) (\d{1,2}), with", html)
    if not match:
        return None
    return parse_month_day(f"{match.group(1)} {match.group(2)}", today)


def classify_scheduled_date(scheduled, today, grace_days):
    if scheduled is None:
        return None, None
    if scheduled + timedelta(days=grace_days) < today:
        return scheduled.isoformat(), "overdue"
    return scheduled.isoformat(), "release"


def column_letters(cell_ref):
    letters = []
    for char in cell_ref:
        if char.isalpha():
            letters.append(char)
        else:
            break
    return "".join(letters)


def read_shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.itertext()) for node in root.findall(NS_MAIN + "si")]


def sheet_target(archive, sheet_name):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    for sheet in workbook.iter(NS_MAIN + "sheet"):
        if sheet.attrib.get("name") != sheet_name:
            continue
        target = targets[sheet.attrib[NS_REL + "id"]]
        if not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        return target
    raise ValueError(f"workbook has no sheet {sheet_name}")


def sheet_rows(archive, sheet_name):
    strings = read_shared_strings(archive)
    root = ET.fromstring(archive.read(sheet_target(archive, sheet_name)))
    rows = []
    for row in root.iter(NS_MAIN + "row"):
        values = {}
        for cell in row.findall(NS_MAIN + "c"):
            ref = cell.attrib.get("r", "")
            node = cell.find(NS_MAIN + "v")
            if node is None or node.text is None:
                continue
            raw = node.text
            if cell.attrib.get("t") == "s":
                raw = strings[int(raw)]
            values[column_letters(ref)] = raw.strip() if isinstance(raw, str) else raw
        if values:
            rows.append(values)
    return rows


def parse_bea_gdp(xlsx_bytes):
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as archive:
        rows = sheet_rows(archive, "T10105-Q")
    published = None
    header = None
    gdp_row = None
    for row in rows:
        label = row.get("A", "")
        if label.startswith("Data published"):
            published = parse_long_date(label.replace("Data published", "").strip())
        if label == "Line":
            header = row
        if label == "1" and "gross domestic product" in row.get("B", "").lower():
            gdp_row = row
    if not header or not gdp_row:
        raise ValueError("BEA table 1.1.5 had no GDP row")
    quarter_label = None
    raw_value = None
    for column, text in header.items():
        if re.fullmatch(r"\d{4}Q[1-4]", text):
            quarter_label = text
            raw_value = gdp_row.get(column)
    if quarter_label is None or raw_value is None:
        raise ValueError("BEA GDP row had no latest quarter")
    as_of, quarter = bea_quarter(quarter_label)
    # Table 1.1.5 is in millions of dollars. FRED's GDP series is billions.
    billions = float(raw_value) / 1000.0
    return {
        "billions": billions,
        "as_of": as_of.isoformat(),
        "quarter": quarter,
        "published": published,
        "source": "BEA NIPA table 1.1.5 (the series FRED publishes as GDP)",
    }


def parse_multpl(html, pattern):
    match = re.search(pattern, html)
    if not match:
        raise ValueError("pattern not found")
    return float(match.group(1))


def multpl_anchor(item):
    if not isinstance(item, dict) or item.get("value") is None or not item.get("as_of"):
        return None
    method = item.get("method")
    source = item.get("source") or ""
    if method == "spx_scaled":
        if item.get("anchor_value") is None or not item.get("anchor_as_of"):
            return None
        return float(item["anchor_value"]), item["anchor_as_of"]
    if method == "multpl" or source.startswith("multpl.com"):
        return float(item["value"]), item["as_of"]
    return None


def scale_from_anchor(anchor_value, anchor_as_of, closes):
    base = close_on_or_before(closes, anchor_as_of)
    latest = latest_close(closes)
    if not base or not latest:
        raise ValueError("S&P history did not cover the anchor")
    if (date.fromisoformat(anchor_as_of) - date.fromisoformat(base[0])).days > 5:
        raise ValueError(f"no S&P close within 5 days of {anchor_as_of}")
    scaled = anchor_value * (latest[1] / base[1])
    return scaled, latest[0], base[0], base[1], latest[1]


def load_data(file_path):
    data = blank_file()
    if os.path.exists(file_path):
        with open(file_path) as handle:
            data = migrate(json.load(handle))
    for key, template in blank_file().items():
        data.setdefault(key, template)
    return data


def data_path():
    target_dir = "heat-index" if os.path.exists("heat-index") else "."
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, "data.json")


def add_issue(issues, series, kind, detail):
    issues.append({"series": series, "kind": kind, "detail": detail})
    print(f"{series} {kind}: {detail}")


def update_multpl_series(data, key, url, pattern, source, today, issues, notes, spx):
    item = data.get(key) if isinstance(data.get(key), dict) else field(None, None, "")
    try:
        html = fetch_text(url, attempts=4, timeout=20)
        value = parse_multpl(html, pattern)
        data[key] = field(
            round_places(value, 2), today.isoformat(), source,
            cadence="daily", method="multpl", stale=False,
        )
        print(f"Fetched {key}: {data[key]['value']}")
        return
    except Exception as exc:
        detail = shorten(exc)
        print(f"{key} multpl failed: {detail}")
    anchor = multpl_anchor(item)
    if anchor and spx:
        try:
            scaled, price_date, base_date, base_px, latest_px = scale_from_anchor(anchor[0], anchor[1], spx)
            anchor_age = age_days(anchor[1], date.fromisoformat(price_date))
            stale = anchor_age is not None and anchor_age > SCALE_ANCHOR_STALE_DAYS
            data[key] = field(
                round_places(scaled, 2), price_date,
                f"multpl.com reading of {anchor[1]} scaled by the S&P 500 move",
                cadence="daily", method="spx_scaled",
                anchor_value=round_places(anchor[0], 2), anchor_as_of=anchor[1],
                spx_anchor=round_places(base_px, 2), spx_anchor_date=base_date,
                spx_latest=round_places(latest_px, 2),
                stale=stale,
            )
            notes.append(f"{key} used the S&P scale because multpl did not respond.")
            print(f"Scaled {key}: {data[key]['value']} as of {price_date}")
            if stale:
                add_issue(issues, key, "stale", f"multpl anchor {anchor[1]} is {anchor_age} days behind the S&P close")
            return
        except Exception as exc:
            detail = detail + "; scale failed: " + shorten(exc)
    data[key] = item
    kept = item.get("as_of")
    kept_age = age_days(kept, today)
    if kept_age is None or kept_age > MULTPL_STALE_DAYS:
        item["stale"] = True
        add_issue(issues, key, "stale", f"multpl failed ({detail}) and the value on file is {kept or 'undated'}")
    else:
        item["stale"] = False
        add_issue(issues, key, "error", f"multpl failed ({detail}); kept {kept}")


def update_cape_and_pe(data, today, issues, notes, spx):
    pages = (
        ("cape", "https://www.multpl.com/shiller-pe", r"Current Shiller PE Ratio is\s*([0-9.]+)", "multpl.com/shiller-pe"),
        ("pe", "https://www.multpl.com/s-p-500-pe-ratio", r"Current S&P 500 PE Ratio is\s*([0-9.]+)", "multpl.com/s-p-500-pe-ratio"),
    )
    for key, url, pattern, source in pages:
        update_multpl_series(data, key, url, pattern, source, today, issues, notes, spx)


def update_buffett_official(data, today, issues, notes):
    try:
        html = fetch_text("https://www.currentmarketvaluation.com/models/buffett-indicator.php", attempts=3, timeout=25)
        parsed = parse_cmv_buffett(html)
    except Exception as exc:
        add_issue(issues, "buffett", "error", "official page failed: " + shorten(exc))
        return
    scheduled = None
    try:
        member = fetch_text("https://www.currentmarketvaluation.com/membership.php", attempts=2, timeout=20)
        scheduled = parse_cmv_public_next(member, today)
    except Exception as exc:
        notes.append("CMV public schedule was not read: " + shorten(exc))
    next_date, kind = classify_scheduled_date(scheduled, today, grace_days=3)
    if kind is None:
        # Their public page is refreshed about six weeks after quarter-end.
        anchor = date.fromisoformat(parsed["as_of"])
        guess = anchor
        while guess <= today:
            month = guess.month + 3
            year = guess.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            guess = quarter_end(year, (month - 1) // 3 + 1) + timedelta(days=45)
        next_date, kind = guess.isoformat(), "estimate"
    detail = None
    if kind == "overdue":
        detail = (
            f"CMV's public schedule said {scheduled.isoformat()}. "
            "That date has passed and the page still shows the older print."
        )
        notes.append("Official Buffett print is overdue against CMV's own public schedule.")
    elif kind == "estimate":
        detail = "CMV did not publish a next public date. Estimated at about six weeks after the next quarter-end."
    data["buffett"] = field(
        round_places(parsed["value"], 1), parsed["as_of"], "currentmarketvaluation.com",
        cadence="quarterly", feeds_score=False, method="official",
        market_cap_trillions=parsed["market_cap_trillions"],
        gdp_trillions=parsed["gdp_trillions"],
        next_update=next_date, next_update_kind=kind,
        stale=kind == "overdue",
    )
    if detail:
        data["buffett"]["next_update_detail"] = detail
    print(f"Fetched buffett official: {data['buffett']['value']} as of {parsed['as_of']} next {kind} {next_date}")


def fetch_ncb_value():
    errors = []
    try:
        text = fetch_text(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NCBCEPNW",
            attempts=2, timeout=8,
        )
        rows = parse_fred_csv(text)
        as_of, quarter = fred_quarter(rows[-1][0])
        return rows[-1][1], as_of, quarter, "FRED NCBCEPNW", None
    except Exception as exc:
        errors.append("FRED: " + shorten(exc))
    try:
        blob = fetch_bytes(
            "https://www.federalreserve.gov/releases/z1/current/z1_csv_files.zip",
            attempts=2, timeout=40,
        )
        value, as_of, quarter = parse_z1_ncb(blob)
        return value, as_of, quarter, f"Z.1 {NCB_SERIES} (FRED series NCBCEPNW)", errors
    except Exception as exc:
        errors.append("Z.1: " + shorten(exc))
    raise RuntimeError("; ".join(errors))


def update_ncb(data, today, issues, notes):
    try:
        value, as_of, quarter, source, prior_errors = fetch_ncb_value()
    except Exception as exc:
        add_issue(issues, "ncb", "error", shorten(exc))
        return
    if prior_errors:
        notes.append("NCB used the Z.1 file because FRED did not respond.")
    next_update = None
    try:
        html = fetch_text("https://www.federalreserve.gov/releases/z1/", attempts=2, timeout=20)
        next_update = parse_next_z1_release(html)
    except Exception as exc:
        notes.append("Z.1 next-release line was not read: " + shorten(exc))
    stale = False
    if next_update and today > date.fromisoformat(next_update) + timedelta(days=NCB_STALE_AFTER_RELEASE_DAYS):
        stale = True
        add_issue(issues, "ncb", "stale", f"quarter {quarter} is still on file after the {next_update} release")
    elif age_days(as_of.isoformat(), today) > NCB_MAX_AGE_DAYS:
        stale = True
        add_issue(issues, "ncb", "stale", f"{as_of.isoformat()} is more than {NCB_MAX_AGE_DAYS} days old")
    data["ncb"] = field(
        round_places(value, 1), as_of.isoformat(), source,
        cadence="quarterly", quarter=quarter, method="fred_ncbcepnw",
        next_update=next_update, next_update_kind="release" if next_update else None,
        stale=stale,
    )
    print(f"Fetched ncb: {data['ncb']['value']} {quarter} next {next_update}")


def fetch_gdp():
    errors = []
    try:
        text = fetch_text("https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDP", attempts=2, timeout=8)
        rows = parse_fred_csv(text)
        as_of, quarter = fred_quarter(rows[-1][0])
        return {
            "billions": rows[-1][1],
            "as_of": as_of.isoformat(),
            "quarter": quarter,
            "published": None,
            "source": "FRED GDP",
        }
    except Exception as exc:
        errors.append("FRED GDP: " + shorten(exc))
    try:
        blob = fetch_bytes(
            "https://apps.bea.gov/national/Release/XLS/Survey/Section1All_xls.xlsx",
            attempts=2, timeout=40,
        )
        parsed = parse_bea_gdp(blob)
        parsed["fallback_notes"] = errors
        return parsed
    except Exception as exc:
        errors.append("BEA: " + shorten(exc))
    raise RuntimeError("; ".join(errors))


def update_buffett_estimate(data, today, issues, notes, wilshire):
    official = data.get("buffett") if isinstance(data.get("buffett"), dict) else {}
    market_cap = official.get("market_cap_trillions")
    official_date = official.get("as_of")
    if not market_cap or not official_date:
        add_issue(issues, "buffett_estimate", "error", "official market cap and date are not on file")
        return
    try:
        gdp = fetch_gdp()
    except Exception as exc:
        add_issue(issues, "buffett_estimate", "error", "GDP failed: " + shorten(exc))
        return
    if gdp.get("fallback_notes"):
        notes.append("GDP came from BEA because FRED did not respond.")
    if not wilshire:
        add_issue(issues, "buffett_estimate", "error", "Wilshire 5000 history was not available")
        return
    base = close_on_or_before(wilshire, official_date)
    latest = latest_close(wilshire)
    if not base or not latest:
        add_issue(issues, "buffett_estimate", "error", "Wilshire history did not cover the official date")
        return
    if (date.fromisoformat(official_date) - date.fromisoformat(base[0])).days > 5:
        add_issue(issues, "buffett_estimate", "error", f"no Wilshire close within 5 days of {official_date}")
        return
    ratio = latest[1] / base[1]
    if not 0.5 <= ratio <= 1.5:
        add_issue(issues, "buffett_estimate", "error", f"Wilshire move of {ratio:.3f} failed the sanity band")
        return
    if not wilshire_scale_ok(market_cap, base[1]):
        per_point = dollars_per_point(market_cap, base[1]) / 1e9
        add_issue(
            issues, "buffett_estimate", "error",
            f"Wilshire close implied ${per_point:.2f}B per point, outside the $0.80-1.30B band, so it does not feed the score",
        )
        return
    estimate = buffett_estimate(market_cap, base[1], latest[1], gdp["billions"])
    price_age = age_days(latest[0], today)
    stale = price_age is not None and price_age > PRICE_STALE_DAYS
    per_point = dollars_per_point(market_cap, base[1])
    data["buffett_estimate"] = field(
        round_places(estimate, 1), latest[0],
        "CMV total market value scaled by the Wilshire 5000, divided by the latest GDP",
        cadence="daily", feeds_score=True, method="wilshire_scaled",
        market_cap_trillions_base=market_cap,
        market_cap_trillions=round_places(market_cap * ratio, 4),
        official_as_of=official_date,
        wilshire_base=round_places(base[1], 2),
        wilshire_base_date=base[0],
        wilshire_latest=round_places(latest[1], 2),
        wilshire_source="Yahoo Finance ^W5000, Wilshire 5000 Total Market Index",
        dollars_per_point_billions=round_places(per_point / 1e9, 4),
        gdp_billions=round_places(gdp["billions"], 3),
        gdp_as_of=gdp["as_of"],
        gdp_quarter=gdp["quarter"],
        gdp_published=gdp.get("published"),
        gdp_source=gdp["source"],
        stale=stale,
        score_reason=(
            "This is the official market-cap-to-GDP ratio brought forward. "
            "The numerator is Current Market Valuation's total market value on its "
            "official date, scaled by the Wilshire 5000 Total Market Index, which "
            "matched that market value at about $1 billion per point. The denominator "
            "is the latest GDP. The official percentage is a separate, older print "
            "and is not averaged in."
        ),
    )
    if stale:
        add_issue(issues, "buffett_estimate", "stale", f"Wilshire close {latest[0]} is {price_age} days old")
    print(
        f"Buffett estimate: {data['buffett_estimate']['value']} as of {latest[0]} "
        f"from {market_cap}T * {latest[1]:.2f}/{base[1]:.2f} / {gdp['billions']:.3f}B"
    )


def update_ms(data, today, issues, notes):
    try:
        payload = json.loads(fetch_text("https://msindex.net/json/max.json", attempts=3, timeout=20))
        points = [
            {"date": row["date"], "index": float(row["index"])}
            for row in payload
            if row.get("date") and row.get("index") is not None
        ]
        points.sort(key=lambda row: row["date"])
        baseline = annual_ms_baseline(points)
        latest = points[-1]
    except Exception as exc:
        add_issue(issues, "ms", "error", shorten(exc))
        return
    stale = (age_days(latest["date"], today) or 0) > MS_STALE_DAYS
    reading = round_places(latest["index"], 4)
    shared = field(
        reading, latest["date"], "msindex.net",
        cadence="daily", method="msindex",
        mean=baseline["mean"], sd=baseline["sd"], baseline=baseline,
        stale=stale,
    )
    data["ms"] = shared
    data["ms_raw"] = field(reading, latest["date"], "msindex.net", cadence="daily", stale=stale)
    if stale:
        add_issue(issues, "ms", "stale", f"{latest['date']} is more than {MS_STALE_DAYS} days old")
    print(f"Fetched ms: {reading} as of {latest['date']} mean {baseline['mean']} sd {baseline['sd']} n {baseline['n']}")


def build_status(issues, notes, started):
    failing = [issue for issue in issues if issue["kind"] in ("error", "stale")]
    return {
        "generated_at": started.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "ok": not failing,
        "issues": issues,
        "notes": notes,
    }


def write_data(file_path, data):
    with open(file_path, "w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def get_macro_data():
    file_path = data_path()
    data = load_data(file_path)
    today = datetime.now(ET_ZONE).date()
    started = datetime.now(ET_ZONE)
    issues = []
    notes = []
    try:
        spx = yahoo_closes("^GSPC", today - timedelta(days=120))
    except Exception as exc:
        spx = None
        notes.append("S&P 500 history failed: " + shorten(exc))
    update_cape_and_pe(data, today, issues, notes, spx)
    update_buffett_official(data, today, issues, notes)
    official = data.get("buffett") if isinstance(data.get("buffett"), dict) else {}
    anchor = official.get("as_of")
    wilshire_start = date.fromisoformat(anchor) if anchor else today - timedelta(days=400)
    try:
        wilshire = yahoo_closes("^W5000", wilshire_start)
    except Exception as exc:
        wilshire = None
        notes.append("Wilshire 5000 history failed: " + shorten(exc))
    update_ncb(data, today, issues, notes)
    update_buffett_estimate(data, today, issues, notes, wilshire)
    update_ms(data, today, issues, notes)
    # The estimate is the scored Buffett input when it cleared the checks.
    estimate = data.get("buffett_estimate") if isinstance(data.get("buffett_estimate"), dict) else {}
    official = data.get("buffett") if isinstance(data.get("buffett"), dict) else {}
    if estimate.get("feeds_score"):
        official["feeds_score"] = False
    data["status"] = build_status(issues, notes, started)
    write_data(file_path, data)
    print(f"SUCCESS: Written macro data to {file_path}")
    if data["status"]["issues"]:
        print("ISSUES: " + json.dumps(data["status"]["issues"]))
    return data


def check(file_path=None):
    file_path = file_path or data_path()
    with open(file_path) as handle:
        data = json.load(handle)
    issues = data.get("status", {}).get("issues", [])
    failing = [issue for issue in issues if issue.get("kind") in ("error", "stale")]
    if not failing:
        print("Freshness check passed.")
        return 0
    for issue in failing:
        print(f"FAIL {issue.get('series')}: {issue.get('kind')}: {issue.get('detail')}")
    return 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    get_macro_data()
