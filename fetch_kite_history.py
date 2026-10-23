"""One-time index history (5-minute and daily; optional 1-minute with FETCH_1M=1) from Zerodha Kite Connect (official API, needs the paid Connect plan).
Output: data/kite/{NSEI,BSESN,NSEBANK}_5m.csv  (ts_ist,open,high,low,close,volume). Merges with any existing file.
Env: KITE_API_KEY, KITE_API_SECRET, KITE_REQUEST_TOKEN (the request_token, or the whole redirected address), START_YEAR, START_MONTH."""
import csv, os, sys, time, datetime as dt
from kiteconnect import KiteConnect
START = dt.date(int(os.environ.get("START_YEAR", "2024")), int(os.environ.get("START_MONTH", "10")), 1)
WANT = {"NSEI": ("NSE", "NIFTY 50"), "NSEBANK": ("NSE", "NIFTY BANK"), "BSESN": ("BSE", "SENSEX"), "VIX": ("NSE", "INDIA VIX")}
if os.environ.get("ONLY"): WANT = {k: v for k, v in WANT.items() if k in os.environ["ONLY"].replace(" ", "").split(",")}   # e.g. ONLY=VIX
OUT = os.environ.get("OUTDIR", "data/kite"); CHUNK_DAYS = 95   # the 5-minute limit is 100 days per request

STOCKS = [s.strip() for s in os.environ.get("STOCKS", "").split(",") if s.strip()]
if STOCKS: WANT = {s: ("NSE", s) for s in STOCKS}   # company shares instead of indexes

def fname(nm): return nm.replace("&", "AND").replace(" ", "_")

def token_from(raw):
    raw = raw.strip().strip('"').strip("'")
    if "request_token=" in raw: raw = raw.split("request_token=", 1)[1]
    return raw.split("&", 1)[0].strip()

def save(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"])
        for k in sorted(rows): w.writerow(rows[k])

def main():
    kite = KiteConnect(api_key=os.environ["KITE_API_KEY"].strip())
    s = kite.generate_session(token_from(os.environ["KITE_REQUEST_TOKEN"]), api_secret=os.environ["KITE_API_SECRET"].strip())
    kite.set_access_token(s["access_token"]); print("login ok")
    os.makedirs(OUT, exist_ok=True); today = dt.date.today()
    tokens = {}
    for ex in ("NSE", "BSE"):
        for ins in kite.instruments(ex):
            for nm, (e, sym) in WANT.items():
                if e == ex and ins["tradingsymbol"] == sym and (not STOCKS or ins.get("instrument_type") == "EQ"): tokens[nm] = ins["instrument_token"]
    print("instrument tokens", tokens)
    for nm in WANT:
        if nm not in tokens: print("NOT FOUND", nm); continue
        path = "%s/%s_5m.csv" % (OUT, fname(nm)); rows = {}
        if os.path.exists(path):
            for r in csv.DictReader(open(path)): rows[r["ts_ist"]] = [r["ts_ist"], r["open"], r["high"], r["low"], r["close"], r["volume"]]
        a = START
        while a <= today:
            b = min(a + dt.timedelta(days=CHUNK_DAYS), today); got = 0
            for attempt in range(3):
                try:
                    c = kite.historical_data(tokens[nm], dt.datetime.combine(a, dt.time(9, 0)), dt.datetime.combine(b, dt.time(15, 59)), "5minute")
                    for x in c:
                        t = x["date"].strftime("%Y-%m-%d %H:%M")
                        if t[11:] < "15:30": rows[t] = [t, x["open"], x["high"], x["low"], x["close"], x["volume"]]; got += 1
                    break
                except Exception as e: print("  retry", attempt + 1, a, b, str(e)[:100]); time.sleep(2 + 2 * attempt)
            print(nm, a, b, got, "candles"); a = b + dt.timedelta(days=1); time.sleep(0.4)
        save(path, rows); ks = sorted(rows); print("SAVED", nm, len(ks), "rows", ks[0] if ks else "-", ks[-1] if ks else "-")
        # daily candles (up to 2000 days per request)
        dpath = "%s/%s_daily.csv" % (OUT, fname(nm)); drows = {}
        if os.path.exists(dpath):
            for r in csv.DictReader(open(dpath)): drows[r["ts_ist"]] = [r["ts_ist"], r["open"], r["high"], r["low"], r["close"], r["volume"]]
        a = START
        while a <= today:
            b = min(a + dt.timedelta(days=1500), today)
            for attempt in range(3):
                try:
                    for x in kite.historical_data(tokens[nm], dt.datetime.combine(a, dt.time(0, 0)), dt.datetime.combine(b, dt.time(23, 59)), "day"):
                        t = x["date"].strftime("%Y-%m-%d"); drows[t] = [t, x["open"], x["high"], x["low"], x["close"], x["volume"]]
                    break
                except Exception as e: print("  daily retry", attempt + 1, a, b, str(e)[:100]); time.sleep(2 + 2 * attempt)
            a = b + dt.timedelta(days=1)
        save(dpath, drows); ks = sorted(drows); print("SAVED daily", nm, len(ks), "rows", ks[0] if ks else "-", ks[-1] if ks else "-")
        if os.environ.get("FETCH_1M") == "1":
            mpath = "%s/%s_1m.csv" % (OUT, fname(nm)); mrows = {}; a = START
            while a <= today:
                b = min(a + dt.timedelta(days=55), today)
                for attempt in range(3):
                    try:
                        for x in kite.historical_data(tokens[nm], dt.datetime.combine(a, dt.time(9, 0)), dt.datetime.combine(b, dt.time(15, 59)), "minute"):
                            t = x["date"].strftime("%Y-%m-%d %H:%M")
                            if t[11:] < "15:30": mrows[t] = [t, x["open"], x["high"], x["low"], x["close"], x["volume"]]
                        break
                    except Exception as e: print("  1m retry", attempt + 1, a, b, str(e)[:100]); time.sleep(2 + 2 * attempt)
                a = b + dt.timedelta(days=1); time.sleep(0.4)
            save(mpath, mrows); print("SAVED 1m", nm, len(mrows), "rows")
if __name__ == "__main__": main()
