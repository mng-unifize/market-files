"""Downloads 5-minute index candles from Upstox's public history endpoint (no login, no keys).
Output: data/upstox/{NSEI,BSESN,NSEBANK}_5m.csv  (ts_ist,open,high,low,close,volume). Merges with any existing file."""
import csv, json, os, sys, time, datetime as dt, urllib.request, urllib.parse
START = dt.date(int(os.environ.get("START_YEAR", "2024")), int(os.environ.get("START_MONTH", "10")), 1)
SYMS = {"NSEI": "NSE_INDEX|Nifty 50", "BSESN": "BSE_INDEX|SENSEX", "NSEBANK": "NSE_INDEX|Nifty Bank"}
OUT = "data/upstox"; CHUNK_DAYS = 24  # the endpoint allows about one month per request for 5-minute candles

def fetch_chunk(key, a, b):
    url = "https://api.upstox.com/v3/historical-candle/%s/minutes/5/%s/%s" % (urllib.parse.quote(key, safe=""), b.isoformat(), a.isoformat())
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=60) as r:
                return json.load(r).get("data", {}).get("candles", [])
        except Exception as e:
            print("  retry", attempt + 1, a, b, str(e)[:80]); time.sleep(2 + 3 * attempt)
    print("  GAVE UP", key, a, b); return []

def to_row(c):
    t = c[0][:16].replace("T", " ")            # 2025-02-10T15:25:00+05:30 -> 2025-02-10 15:25
    if t[11:] >= "15:30": return None            # drop closing stub bars
    return [t, c[1], c[2], c[3], c[4], c[5]]

def main():
    os.makedirs(OUT, exist_ok=True); today = dt.date.today()
    for nm, key in SYMS.items():
        path = "%s/%s_5m.csv" % (OUT, nm); rows = {}
        if os.path.exists(path):
            for r in csv.DictReader(open(path)): rows[r["ts_ist"]] = [r["ts_ist"], r["open"], r["high"], r["low"], r["close"], r["volume"]]
        a = START
        while a <= today:
            b = min(a + dt.timedelta(days=CHUNK_DAYS), today)
            got = 0
            for c in fetch_chunk(key, a, b):
                row = to_row(c)
                if row: rows[row[0]] = row; got += 1
            print(nm, a, b, got, "candles"); a = b + dt.timedelta(days=1); time.sleep(0.4)
        with open(path, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"])
            for k in sorted(rows): w.writerow(rows[k])
        ks = sorted(rows); print("SAVED", nm, len(ks), "rows", ks[0] if ks else "-", ks[-1] if ks else "-")
if __name__ == "__main__": main()
