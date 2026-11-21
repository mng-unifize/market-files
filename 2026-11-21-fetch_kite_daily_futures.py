"""Daily continuous futures history from Zerodha Kite Connect for gold, silver, crude oil, natural gas, copper, USD/INR, EUR/INR and Nifty/Bank Nifty futures.
Read only: logs in, downloads prices, writes NEW files under data/kite-daily-futures/. Places no orders, touches no other folder.
Env: KITE_API_KEY, KITE_API_SECRET, KITE_REQUEST_TOKEN (token or whole redirected address), optional NAMES (comma list), START_YEAR (default 2016)."""
import csv, os, sys, time, datetime as dt
OUT = os.environ.get("OUTDIR", "data/kite-daily-futures")
START = dt.date(int(os.environ.get("START_YEAR", "2016")), 1, 1)
WANT = [("MCX", "GOLDM"), ("MCX", "GOLD"), ("MCX", "SILVERM"), ("MCX", "SILVER"), ("MCX", "CRUDEOIL"), ("MCX", "CRUDEOILM"),
        ("MCX", "NATURALGAS"), ("MCX", "COPPER"), ("CDS", "USDINR"), ("CDS", "EURINR"), ("NFO", "NIFTY"), ("NFO", "BANKNIFTY")]
if os.environ.get("NAMES"):
    keep = [n.strip() for n in os.environ["NAMES"].split(",")]; WANT = [w for w in WANT if w[1] in keep]

def token_from(raw):
    raw = raw.strip().strip('"').strip("'")
    if "request_token=" in raw: raw = raw.split("request_token=", 1)[1]
    return raw.split("&", 1)[0].strip()

def pick(instruments, name, today):
    """Nearest-expiry futures contract of this name (the continuous series is built from it)."""
    c = [i for i in instruments if i.get("instrument_type") == "FUT" and i.get("name") == name and i["expiry"] and i["expiry"] >= today]
    return min(c, key=lambda i: i["expiry"]) if c else None

def main():
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=os.environ["KITE_API_KEY"].strip())
    s = kite.generate_session(token_from(os.environ["KITE_REQUEST_TOKEN"]), api_secret=os.environ["KITE_API_SECRET"].strip())
    kite.set_access_token(s["access_token"]); print("login ok")
    os.makedirs(OUT, exist_ok=True); today = dt.date.today(); cache = {}; meta = []
    for ex, name in WANT:
        if ex not in cache: cache[ex] = kite.instruments(ex)
        ins = pick(cache[ex], name, today)
        if not ins: print("NOT FOUND", ex, name); continue
        rows = {}; a = START
        while a <= today:                                   # day candles: up to 2000 days per request
            b = min(a + dt.timedelta(days=1800), today)
            for attempt in range(3):
                try:
                    for r in kite.historical_data(ins["instrument_token"], a, b, "day", continuous=True, oi=True):
                        k = r["date"].strftime("%Y-%m-%d"); rows[k] = [k, r["open"], r["high"], r["low"], r["close"], r.get("volume", 0), r.get("oi", 0)]
                    break
                except Exception as e:
                    print("retry", name, a, str(e)[:120]); time.sleep(2 + 3 * attempt)
            a = b + dt.timedelta(days=1); time.sleep(0.4)
        with open("%s/%s_day.csv" % (OUT, name), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["date", "open", "high", "low", "close", "volume", "oi"])
            for k in sorted(rows): w.writerow(rows[k])
        meta.append([name, ex, ins["tradingsymbol"], ins["lot_size"], ins["expiry"], len(rows), min(rows) if rows else "", max(rows) if rows else ""])
        print(name, ins["tradingsymbol"], "lot", ins["lot_size"], "days", len(rows), flush=True)
    with open("%s/_instruments.csv" % OUT, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["name", "exchange", "contract", "lot_size", "expiry", "days", "first", "last"]); w.writerows(meta)

if __name__ == "__main__": main()
