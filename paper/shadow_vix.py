#!/usr/bin/env python3
"""Shadow rule (does not change any trade): would skipping entries after a VIX jump have helped?
Rule: skip an entry when the last VIX close before the entry day is more than 5% above the mean of the 5 closes before that day.
Writes data/live/shadow_vix.json. Env: PAPER_SHADOW_FROM (first day to score, default 2026-10-26)."""
import csv, json, os, sys, urllib.request, datetime as dt
FROM = os.environ.get("PAPER_SHADOW_FROM", "2026-10-26"); VAR = "Hold overnight with skip rules"
SEED, STORE, OUT = "data/kite/VIX_daily.csv", "data/live/vix_daily.csv", "data/live/shadow_vix.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
def load():
    v = {}
    for p in (SEED, STORE):
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                k = r.get("ts_ist") or r.get("date"); c = r.get("close")
                if k and c: v[k[:10]] = float(c)
    return v
def fetch(v):
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EINDIAVIX?interval=1d&range=1mo"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        res = json.load(urllib.request.urlopen(req, timeout=60))["chart"]["result"][0]; n = 0
        for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
            d = dt.datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d")
            if c is not None and d not in v: v[d] = round(c, 2); n += 1
        return n
    except Exception as e:
        print("vix fetch failed:", e, file=sys.stderr); return -1
def flag(v, day):
    prior = [d for d in sorted(v) if d < day][-5:]
    if len(prior) < 5 or (dt.date.fromisoformat(day) - dt.date.fromisoformat(prior[-1])).days > 5: return None   # stale VIX data: do not guess
    last = v[prior[-1]]; mean = sum(v[d] for d in prior) / 5
    return last > mean * 1.05
def main():
    v = load(); added = fetch(v)
    os.makedirs("data/live", exist_ok=True)
    with open(STORE, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "close"])
        for d in sorted(v):
            if d > "2026-10-23": w.writerow([d, v[d]])
    T = [t for t in json.load(open("data/mock/dash/trades.json")) if t.get("variant") == VAR and t["day"] >= FROM and t.get("status") != "Skipped"]
    res = {}; allr = dict(trades=0, flagged=0, net_all=0, net_flagged=0)
    for t in T:
        fl = flag(v, t["day"]); r = res.setdefault(t["index"], dict(trades=0, flagged=0, unknown=0, net_all=0, net_flagged=0, flagged_days=[]))
        r["trades"] += 1; r["net_all"] += t["net"]
        if fl is None: r["unknown"] += 1
        elif fl: r["flagged"] += 1; r["net_flagged"] += t["net"]; r["flagged_days"].append(t["day"])
    for r in res.values():
        r["net_if_skipped"] = r["net_all"] - r["net_flagged"]
        for k in ("trades", "flagged"): allr[k] += r[k]
        allr["net_all"] += r["net_all"]; allr["net_flagged"] += r["net_flagged"]
    allr["net_if_skipped"] = allr["net_all"] - allr["net_flagged"]
    allr["verdict"] = "too early" if allr["trades"] < 40 or allr["flagged"] < 8 else ("rule helped" if allr["net_flagged"] < 0 else "rule did not help")
    latest = max(v) if v else None
    json.dump(dict(rule="skip entry if last VIX close is more than 5% above the mean of the previous 5 closes", scored_from=FROM, latest_vix_day=latest, vix_rows_added=added, by_index=res, total=allr,
                   note="Shadow only. Nothing is skipped in the live ledger. Backtest basis: Nifty + Bank Nifty ratio 1.47 to 1.72, profit down 7%, picked from 43 tests so unproven."), open(OUT, "w"), indent=1)
    print("shadow_vix:", allr)
if __name__ == "__main__": main()
