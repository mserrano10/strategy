import os
import json
import re
import urllib.request
from datetime import datetime

def fetch_html(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read().decode('utf-8')

def get_macro_data():
    # Baseline Fallback Values
    data = {
        "cape": 42.4,
        "buffett": 244.0,
        "ncb": 224.9,
        "pe": 26.2,
        "ms": 78,
        "date": datetime.now().strftime("%Y-%m-%d")
    }

    # 1. Fetch Shiller PE Ratio (Multpl)
    try:
        html = fetch_html("https://www.multpl.com/shiller-pe")
        match = re.search(r'Current Shiller PE Ratio:\s*([0-9\.]+)', html)
        if match:
            data["cape"] = float(match.group(1))
            print(f"Fetched CAPE: {data['cape']}")
    except Exception as e:
        print(f"Shiller PE fetch error: {e}")

    # 2. Fetch S&P 500 Trailing PE Ratio (Multpl)
    try:
        html = fetch_html("https://www.multpl.com/s-p-500-pe-ratio")
        match = re.search(r'Current S&P 500 PE Ratio:\s*([0-9\.]+)', html)
        if match:
            data["pe"] = float(match.group(1))
            print(f"Fetched Trailing PE: {data['pe']}")
    except Exception as e:
        print(f"Trailing PE fetch error: {e}")

    # Explicitly target heat-index directory
    target_dir = "heat-index" if os.path.exists("heat-index") else "."
    os.makedirs(target_dir, exist_ok=True)
    output_path = os.path.join(target_dir, "data.json")

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"Successfully updated {output_path}")

if __name__ == "__main__":
    get_macro_data()
