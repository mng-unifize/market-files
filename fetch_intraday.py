#!/usr/bin/env python3
# Saves 5-minute candles for Nifty 50 and Sensex into data/intraday/*.csv (standard library only).
# Yahoo keeps only about 60 days of 5-minute history, so this runs daily and appends; old rows are kept.
import csv, json, os, sys, time, urllib.request, datetime as dt
OUT = "data/intraday"
SYMS = {"NSEI": "%5ENSEI", "BSESN": "%5EBSESN"}
HDR = ["ts_ist", "open", "high", "low", "close", "volume"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

def get(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=60d"
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["chart"]["result"][0]
        except Exception as e:
            print("retry", sym, e, file=sys.stderr); time.sleep(5 * (i + 1))
    raise SystemExit(f"could not fetch {sym}")

def main():
    os.makedirs(OUT, exist_ok=True)
    for name, sym in SYMS.items():
        res = get(sym); q = res["indicators"]["quote"][0]; rows = {}
        for i, t in enumerate(res["timestamp"]):
            if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
            ts = dt.datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d %H:%M")
            rows[ts] = [ts, round(q["open"][i], 2), round(q["high"][i], 2), round(q["low"][i], 2), round(q["close"][i], 2), int(q["volume"][i] or 0)]
        p = f"{OUT}/{name}_5m.csv"
        if os.path.exists(p):
            for r in csv.reader(open(p)):
                if r and r[0] != "ts_ist": rows.setdefault(r[0], r)   # new download wins, old history kept
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(HDR)
            for k in sorted(rows): w.writerow(rows[k])
        print(name, len(rows), "rows, last", max(rows))

main()
