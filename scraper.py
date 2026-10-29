import os
import json
import re
import urllib.request
import ssl
from datetime import datetime

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch_html(url):
    ctx = ssl.create_default_context()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def field(value, as_of, source):
    return {"value": value, "as_of": as_of, "source": source}


def blank_file():
    return {
        "cape": field(None, None, "Not fetched yet."),
        "pe": field(None, None, "Not fetched yet."),
        "buffett": field(None, None, "Not fetched yet."),
        "ncb": field(None, None, "Typed figure. Not fetched by this script."),
        "ms": field(None, None, "Typed 0-100 state. Not fetched by this script."),
        "ms_raw": field(None, None, "msindex.net"),
    }


def migrate(raw):
    """Accept the older flat file, which stamped one date on every series."""
    if not raw or isinstance(raw.get("cape"), dict):
        return raw or blank_file()
    shared = raw.get("date")
    data = blank_file()
    for key in ("cape", "pe", "buffett", "ncb", "ms"):
        if raw.get(key) is not None:
            data[key] = field(raw[key], None, "Carried over from the old file. The old single date was not a per-series date.")
    if shared:
        data["cape"]["source"] += " Old file date was " + str(shared) + "."
    return data


def parse_long_date(text):
    parts = text.replace(",", "").split()
    if len(parts) != 3:
        return None
    month = MONTHS.get(parts[0].lower())
    if not month:
        return None
    return f"{int(parts[2]):04d}-{month:02d}-{int(parts[1]):02d}"


def update_cape_and_pe(data, today):
    pages = (
        ("cape", "https://www.multpl.com/shiller-pe", r"Current Shiller PE Ratio is\s*([0-9.]+)", "multpl.com/shiller-pe"),
        ("pe", "https://www.multpl.com/s-p-500-pe-ratio", r"Current S&P 500 PE Ratio is\s*([0-9.]+)", "multpl.com/s-p-500-pe-ratio"),
    )
    for key, url, pattern, source in pages:
        try:
            html = fetch_html(url)
            match = re.search(pattern, html)
            if not match:
                print(f"{key} left unchanged: pattern not found")
                continue
            data[key] = field(float(match.group(1)), today, source)
            print(f"Fetched {key}: {data[key]['value']}")
        except Exception as exc:
            print(f"{key} left unchanged: {exc}")


def update_buffett(data):
    try:
        html = fetch_html("https://www.currentmarketvaluation.com/models/buffett-indicator.php")
        match = re.search(
            r"As of ([A-Za-z]+ \d{1,2}, \d{4}) we calculate the Buffett Indicator as ([0-9.]+)%",
            html,
        )
        if not match:
            print("buffett left unchanged: pattern not found")
            return
        as_of = parse_long_date(match.group(1))
        data["buffett"] = field(float(match.group(2)), as_of, "currentmarketvaluation.com")
        print(f"Fetched buffett: {data['buffett']['value']} as of {as_of}")
    except Exception as exc:
        print(f"buffett left unchanged: {exc}")


def update_ms_raw(data):
    try:
        raw = json.loads(fetch_html("https://msindex.net/json/oneYear.json"))
        last = raw[-1]
        data["ms_raw"] = field(round(float(last["index"]), 2), last["date"], "msindex.net")
        print(f"Fetched ms_raw: {data['ms_raw']['value']} as of {last['date']}")
    except Exception as exc:
        print(f"ms_raw left unchanged: {exc}")


def get_macro_data():
    target_dir = "heat-index" if os.path.exists("heat-index") else "."
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, "data.json")

    data = blank_file()
    if os.path.exists(file_path):
        with open(file_path) as handle:
            data = migrate(json.load(handle))

    today = datetime.now().strftime("%Y-%m-%d")
    update_cape_and_pe(data, today)
    update_buffett(data)
    update_ms_raw(data)

    with open(file_path, "w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    print(f"SUCCESS: Written macro data to {file_path}")


if __name__ == "__main__":
    get_macro_data()
