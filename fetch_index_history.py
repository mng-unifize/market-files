#!/usr/bin/env python3
# One-time download of Nifty 50 and Sensex daily (all history) and hourly (about 2 years) candles
# into data/index/*.csv (standard library only). Run from a GitHub Actions job.
import csv, json, os, sys, time, urllib.request, datetime as dt
OUT = "data/index"; IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
JOBS = [("NSEI", "%5ENSEI", "1d", "max", "daily"), ("BSESN", "%5EBSESN", "1d", "max", "daily"),
        ("NSEI", "%5ENSEI", "60m", "730d", "hourly"), ("BSESN", "%5EBSESN", "60m", "730d", "hourly")]
def get(sym, iv, rg):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={iv}&range={rg}"
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
                return json.load(r)["chart"]["result"][0]
        except Exception as e:
            print("retry", sym, iv, e, file=sys.stderr); time.sleep(5 * (i + 1))
    raise SystemExit(f"could not fetch {sym} {iv}")
os.makedirs(OUT, exist_ok=True)
for name, sym, iv, rg, tag in JOBS:
    res = get(sym, iv, rg); q = res["indicators"]["quote"][0]; rows = []
    for i, t in enumerate(res["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        ts = dt.datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d %H:%M")
        rows.append([ts, round(q["open"][i], 2), round(q["high"][i], 2), round(q["low"][i], 2), round(q["close"][i], 2)])
    with open(f"{OUT}/{name}_{tag}.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close"]); w.writerows(rows)
    print(name, tag, len(rows), rows[0][0], "to", rows[-1][0])
