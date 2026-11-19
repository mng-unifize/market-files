#!/usr/bin/env python3
"""Exact test of 'enter just before the candle closes' using 1-minute index candles (data/kite-1m/*_1m.csv, or DATADIR=...).
Read only. For every real crossover (decided on the closed candle) it asks: would the crossover already have shown ~30 s before the close,
how much cheaper was the entry price, and how many false early signals would there be (crossed at -30 s, cancelled at the close).
The price 30 s before the close is not in 1-minute data; two stand-ins are used: the middle of the last minute (open+close)/2 and the close of the minute before (60 s early)."""
import csv, os, sys, collections, statistics as S
DATA = os.environ.get("DATADIR", "data/kite-1m"); FALSE_ROUND_TRIP_PCT = 0.10   # two crossings of the spread (0.044) + charges (0.053), rounded
SETUP = {"NSEI": (15, 9, 21), "BSESN": (15, 9, 21), "NSEBANK": (5, 20, 50)}
def load(nm): return [(r["ts_ist"], float(r["open"]), float(r["close"])) for r in csv.DictReader(open("%s/%s_1m.csv" % (DATA, nm)))]
def buckets(rows, tf):
    day = collections.defaultdict(list)
    for t, o, c in rows: day[t[:10]].append((t[11:], o, c))
    out = []
    for d in sorted(day):
        g = collections.defaultdict(list)
        for hm, o, c in day[d]:
            m = (int(hm[:2]) * 60 + int(hm[3:5])) - (9 * 60 + 15)
            if m >= 0: g[m // tf].append((o, c))
        for k in sorted(g):
            L = g[k]
            if len(L) < tf: continue                       # partial candle (missing minutes): skip
            out.append(dict(close=L[-1][1], p30=(L[-1][0] + L[-1][1]) / 2, p60=L[-2][1]))
    return out
def run(nm):
    tf, fa, sl = SETUP[nm]; B = buckets(load(nm), tf); n = len(B)
    kf, ks = 2 / (fa + 1), 2 / (sl + 1); ef = [B[0]["close"]]; es = [B[0]["close"]]
    for b in B[1:]: ef.append(ef[-1] + kf * (b["close"] - ef[-1])); es.append(es[-1] + ks * (b["close"] - es[-1]))
    res = {}
    for key in ("p30", "p60"):
        real = kept = false = 0; gains = []
        for i in range(1, n):
            prev = ef[i - 1] > es[i - 1]; cur = ef[i] > es[i]
            early = (ef[i - 1] + kf * (B[i][key] - ef[i - 1])) > (es[i - 1] + ks * (B[i][key] - es[i - 1]))
            if prev != cur:
                real += 1
                if early == cur: kept += 1; gains.append((1 if cur else -1) * (B[i]["close"] - B[i][key]) / B[i]["close"] * 100)
            elif early != prev: false += 1
        g = S.mean(gains) if gains else 0
        net = (kept * g - false * FALSE_ROUND_TRIP_PCT) / real if real else 0
        res[key] = (real, 100 * kept / real, 100 * false / real, g, net)
    print(nm, "%d candles" % n)
    for key, lab in (("p30", "about 30 s early"), ("p60", "60 s early")):
        real, kp, fp, g, net = res[key]
        print("  %-17s real crossovers %d | still show early %.1f%% | false early signals %.1f per 100 real | entry %.4f%% cheaper on those that show | net per real signal %+.4f%% (about Rs %+d on a Rs 17 lakh lot)" % (lab, real, kp, fp, g, net, round(net / 100 * 1700000)))
if __name__ == "__main__":
    for nm in SETUP:
        try: run(nm)
        except FileNotFoundError: print(nm, "no 1-minute file in", DATA)
