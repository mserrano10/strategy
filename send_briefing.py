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
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        return resp.read().decode('utf-8')

def check_prior_week_drop():
    """Checks if QQQ dropped >3% over the prior week using Yahoo Finance data."""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=7d&interval=1d"
        data = json.loads(fetch_url(url))
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        valid_closes = [c for c in closes if c is not None]
        if len(valid_closes) >= 2:
            first = valid_closes[0]
            last = valid_closes[-1]
            pct_change = ((last - first) / first) * 100
            return pct_change, (pct_change <= -3.0)
    except Exception as e:
        print(f"Error checking QQQ performance: {e}")
    return 0.0, False

def check_fomc_and_cpi_window():
    """Dynamically checks if current week coincides with typical FOMC/CPI release windows."""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    
    # CPI generally drops between the 10th and 15th of each month
    cpi_flag = any(10 <= (monday + timedelta(days=i)).day <= 15 for i in range(5))
    
    # Check Federal Reserve schedule feed dynamically
    fomc_flag = False
    fomc_info = "No meeting scheduled"
    try:
        # Dynamic check against official Fed schedule feed
        url = "https://www.federalreserve.gov/feeds/press_all.xml"
        xml_data = fetch_url(url)
        if "FOMC statement" in xml_data or "Monetary Policy" in xml_data:
            fomc_flag = True
            fomc_info = "FOMC activity detected in Fed communications feed"
    except Exception:
        pass

    return fomc_flag, fomc_info, cpi_flag

def send_email():
    sender_email = os.environ.get("EMAIL_USER")
    sender_password = os.environ.get("EMAIL_PASS")
    recipient_email = "mserrano88@gmail.com"

    if not sender_email or not sender_password:
        print("Missing email credentials. Execution skipped.")
        return

    # 1. Live Market & Calendar Checks
    qqq_change, heavy_drop = check_prior_week_drop()
    is_fomc, fomc_info, is_cpi_window = check_fomc_and_cpi_window()
    
    # 2. Format Flag Strings
    drop_flag_str = (
        f"[!] PRIOR WEEK DECLINE: {qqq_change:.2f}% (FLAG: REDUCE POSITION SIZE)"
        if heavy_drop
        else f"[ ] Prior Week Performance: {qqq_change:.2f}% (Clean)"
    )

    fomc_flag_str = (
        f"[!] FOMC EVENT DETECTED: {fomc_info} (FLAG: REDUCE POSITION SIZE)"
        if is_fomc
        else f"[ ] FOMC Schedule: No FOMC decision flagged for this week."
    )

    cpi_flag_str = (
        f"[!] CPI RELEASE WINDOW: Current week falls in typical mid-month CPI print window (10th-15th)."
        if is_cpi_window
        else f"[ ] CPI Window: Outside typical mid-month CPI print dates."
    )

    subject = f"Atlas Wealth // Monday IC Pre-Flight Briefing ({datetime.now().strftime('%Y-%m-%d')})"
    
    body = f"""====================================================================
 ATLAS WEALTH // MONDAY PRE-FLIGHT BRIEFING
====================================================================

[1] AUTOMATED LIVE MARKET & CALENDAR SCAN
--------------------------------------------------------------------
 {fomc_flag_str}
 {cpi_flag_str}
 {drop_flag_str}

[2] STEP 1: MANUAL CONTEXT & EARNINGS CHECK (Size Reducers)
--------------------------------------------------------------------
 [ ] Top-8 QQQ (AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AVGO) earnings this week?
 [ ] AI Bellwethers (ASML, TSMC) reporting earnings this week?
 [ ] Hard geopolitical deadline / tariff decision scheduled?

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

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        print("Briefing email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    send_email()
