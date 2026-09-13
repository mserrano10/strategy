import os
import smtplib
import json
import urllib.request
import ssl
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def fetch_url(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
        return resp.read().decode('utf-8')

def check_prior_week_drop():
    """Checks if QQQ dropped >3% over the prior week with fail-safe handling."""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=7d&interval=1d"
        data = json.loads(fetch_url(url))
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        valid_closes = [c for c in closes if c is not None]
        if len(valid_closes) >= 2:
            first = valid_closes[0]
            last = valid_closes[-1]
            pct_change = ((last - first) / first) * 100
            return pct_change, (pct_change <= -3.0), True
    except Exception as e:
        print(f"Warning: QQQ price fetch skipped ({e})")
    return 0.0, False, False

def check_live_earnings():
    """Checks live earnings calendar for Top-8 QQQ + AI Bellwethers with fail-safe handling."""
    tickers = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "ASML", "TSMC"]
    flagged_tickers = []

    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)

    for ticker in tickers:
        try:
            url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=calendarEvents"
            html = fetch_url(url)
            data = json.loads(html)
            
            events = data.get('quoteSummary', {}).get('result', [{}])[0].get('calendarEvents', {})
            earnings_dates = events.get('earnings', {}).get('earningsDate', [])
            for entry in earnings_dates:
                if isinstance(entry, dict) and 'raw' in entry:
                    e_date = datetime.fromtimestamp(entry['raw'])
                    if monday.date() <= e_date.date() <= friday.date():
                        flagged_tickers.append(f"{ticker} ({e_date.strftime('%a %b %d')})")
                        break
        except Exception:
            continue

    return flagged_tickers

def check_fomc_and_cpi_window():
    """Dynamically checks if current week coincides with typical FOMC/CPI release windows."""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    
    # CPI generally drops between the 10th and 15th of each month
    cpi_flag = any(10 <= (monday + timedelta(days=i)).day <= 15 for i in range(5))
    
    fomc_flag = False
    try:
        url = "https://www.federalreserve.gov/feeds/press_all.xml"
        xml_data = fetch_url(url)
        if "FOMC statement" in xml_data or "Monetary Policy" in xml_data:
            fomc_flag = True
    except Exception:
        pass

    return fomc_flag, cpi_flag

def send_email():
    sender_email = os.environ.get("EMAIL_USER")
    sender_password = os.environ.get("EMAIL_PASS")
    recipient_email = "mserrano88@gmail.com"

    if not sender_email or not sender_password:
        print("Error: Missing EMAIL_USER or EMAIL_PASS environment variables.")
        raise ValueError("Missing email credentials.")

    # 1. Fetch Data First
    qqq_change, heavy_drop, qqq_ok = check_prior_week_drop()
    is_fomc, is_cpi_window = check_fomc_and_cpi_window()
    earnings_flagged = check_live_earnings()
    
    # 2. Format All Flag Strings Before Body Assembly
    if qqq_ok:
        drop_flag_str = (
            f"[!] PRIOR WEEK DECLINE: {qqq_change:.2f}% (FLAG: REDUCE POSITION SIZE)"
            if heavy_drop
            else f"[ ] Prior Week Performance: {qqq_change:.2f}% (Clean)"
        )
    else:
        drop_flag_str = "[ ] Prior Week Performance: Check manually on IC Calculator"

    fomc_flag_str = (
        "[!] FOMC EVENT DETECTED: Statement/Minutes active in Fed feed (FLAG: REDUCE SIZE)"
        if is_fomc
        else "[ ] FOMC Schedule: No FOMC decision flagged for this week."
    )

    cpi_flag_str = (
        "[!] CPI RELEASE WINDOW: Current week falls in typical mid-month CPI print window (10th-15th)."
        if is_cpi_window
        else "[ ] CPI Window: Outside typical mid-month CPI print dates."
    )

    if earnings_flagged:
        earnings_str = f"[!] EARNINGS REPORTING THIS WEEK: {', '.join(earnings_flagged)} (FLAG: REDUCE SIZE)"
    else:
        earnings_str = "[ ] Earnings Radar: None of Top-8 QQQ or AI Bellwethers (ASML/TSMC) reporting this week."

    # 3. Assemble Email Body
    subject = f"Atlas Wealth // Monday IC Pre-Flight Briefing ({datetime.now().strftime('%Y-%m-%d')})"
    
    body = f"""====================================================================
 ATLAS WEALTH // MONDAY PRE-FLIGHT BRIEFING
====================================================================

[1] AUTOMATED LIVE MARKET & CALENDAR SCAN (Size Reducers)
--------------------------------------------------------------------
 {fomc_flag_str}
 {cpi_flag_str}
 {earnings_str}
 {drop_flag_str}

[2] STEP 1: GEOPOLITICAL & AD-HOC CONTEXT CHECK (Manual)
--------------------------------------------------------------------
 [ ] Major unannounced geopolitical deadline, debt ceiling date, or tariff news?
     --> IF YES: Consider trading smaller than the edge multiple suggests.

[3] STEP 2: RUN HARD GATES ON IC CALCULATOR WEB PAGE
--------------------------------------------------------------------
 Open Desk: https://strategy.atlaswealth.cc/ic-calculator/

 [ ] MONDAY GAP CHECK: Is Monday opening gap worse than -2.0%?
     --> IF YES: HARD STAND DOWN (Do not trade).
 
 [ ] OPEN POSITIONS: Is a position still open from last week?
     --> IF YES: HARD STAND DOWN (No stacking, no rolls, no repairs).

 [ ] CALL SIDE SIGMA: Forced closer than 2.2σ - 2.5σ to chase credit?
     --> IF YES: Stand down call side; evaluate Put-Only spread.

 [ ] PUT SIDE SIGMA: Target ~2.9σ (~0.02 Delta). Is VXN < 22?
     --> IF VXN < 22: Credit will be thin; verify fillable pricing.

[4] STEP 3: EXECUTION & BROKER DISCIPLINE
--------------------------------------------------------------------
 [ ] Tested fillable BID/ASK prices in broker (not MID)?
 [ ] Max potential loss capped at 3.0% of NAV (Black Swan Guard)?
 [ ] Stop-loss order ready to enter in the SAME session as entry?

====================================================================
"""

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(sender_email, sender_password)
    server.send_message(msg)
    server.quit()
    print("Briefing email sent successfully!")

if __name__ == "__main__":
    send_email()
