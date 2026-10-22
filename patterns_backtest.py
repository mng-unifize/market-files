#!/usr/bin/env python3
# Intraday pattern backtest on 5-minute index candles (standard library only).
# Usage: python3 patterns_backtest.py data/intraday results.json
# Rules are fixed here, in advance. Do not tune them after looking at results.
import sys, os, csv, json, math, collections
LAST_ENTRY = "14:30"; FLAT_AT = "15:15"; TARGET_R = 2.0; COST = 0.0002   # cost: 0.02% of price per round trip
MINR, MAXR = 0.001, 0.010          # risk per trade must be 0.1% to 1.0% of price

def load(p):
    days = collections.OrderedDict()
    for r in csv.DictReader(open(p)):
        d, t = r["ts_ist"].split(" ")
        days.setdefault(d, []).append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    return {d: b for d, b in days.items() if len(b) >= 60}      # skip half days and gaps

def reg(y):
    n = len(y); xm = (n - 1) / 2; ym = sum(y) / n
    sxx = sum((i - xm) ** 2 for i in range(n)); b = sum((i - xm) * (v - ym) for i, v in enumerate(y)) / sxx
    return b, ym - b * xm   # slope per bar, intercept at bar 0

def sim(bars, i, side, stop, tag):
    t, o, h, l, c = bars[i]; R = abs(c - stop)
    if R < MINR * c or R > MAXR * c: return None
    tgt = c + side * TARGET_R * R; mfe = 0.0; mae = 0.0; res = None
    for j in range(i + 1, len(bars)):
        tj, oj, hj, lj, cj = bars[j]
        fav = (hj - c) if side > 0 else (c - lj); adv = (c - lj) if side > 0 else (hj - c)
        mfe = max(mfe, fav / R); mae = max(mae, adv / R)
        if adv >= R: res = (-1.0, tj, stop, j); break                       # stop checked first (conservative)
        if fav >= TARGET_R * R: res = (TARGET_R, tj, tgt, j); break
        if tj >= FLAT_AT: res = (side * (cj - c) / R, tj, cj, j); break
    if res is None: j = len(bars) - 1; res = (side * (bars[j][4] - c) / R, bars[j][0], bars[j][4], j)
    net = res[0] - COST * c / R
    return dict(entry_time=t, side="long" if side > 0 else "short", entry=round(c, 2), stop=round(stop, 2), target=round(tgt, 2),
                exit_time=res[1], exit=round(res[2], 2), r=round(net, 3), mfe_r=round(mfe, 2), mae_r=round(mae, 2), end=res[3], pattern=tag)

def orb(bars, prev):
    hi = max(b[2] for b in bars[:6]); lo = min(b[3] for b in bars[:6])
    for i in range(6, len(bars)):
        if bars[i][0] > LAST_ENTRY: break
        c = bars[i][4]
        if c > hi: return [sim(bars, i, 1, lo, "orb30")]
        if c < lo: return [sim(bars, i, -1, hi, "orb30")]
    return []

def pdhl(bars, prev):
    if not prev: return []
    ph = max(b[2] for b in prev); pl = min(b[3] for b in prev); rng = ph - pl
    for i in range(6, len(bars)):
        if bars[i][0] > LAST_ENTRY: break
        c = bars[i][4]
        if c > ph: return [sim(bars, i, 1, c - 0.25 * rng, "prev_day_break")]
        if c < pl: return [sim(bars, i, -1, c + 0.25 * rng, "prev_day_break")]
    return []

def channel(bars, prev, N=24, maxtrades=2):
    out = []; i = N
    while i < len(bars) and len(out) < maxtrades:
        if bars[i][0] > LAST_ENTRY: break
        w = bars[i - N:i]; cl = [b[4] for b in w]; b_, a_ = reg(cl); mid = lambda k: a_ + b_ * k
        up = max(w[k][2] - mid(k) for k in range(N)); dn = max(mid(k) - w[k][3] for k in range(N)); px = cl[-1]
        width = (up + dn) / px; tilt = abs(b_ * N) / px
        if 0.0015 <= width <= 0.006 and tilt >= 0.001:                     # tilted, tight, parallel channel
            c = bars[i][4]; m = mid(N)
            side = 1 if c > m + up else (-1 if c < m - dn else 0)
            if side:
                tr = sim(bars, i, side, m, "parallel_channel_break")
                if tr: out.append(tr); i = tr["end"] + 1; continue
        i += 1
    return out

def triangle(bars, prev, N=36):
    for i in range(N, len(bars)):
        if bars[i][0] > LAST_ENTRY: break
        w = bars[i - N:i]; hs = [b[2] for b in w]; b_, _ = reg(hs)
        if b_ * N / w[-1][4] > -0.0015: continue                           # highs must fall at least 0.15%
        sup = min(b[3] for b in w); near = [k for k, b in enumerate(w) if b[3] <= sup * 1.001]
        if len(near) < 2 or near[-1] - near[0] < 6: continue               # flat support touched twice, 6+ bars apart
        c = bars[i][4]
        if c < sup: return [sim(bars, i, -1, max(b[2] for b in bars[i - 12:i]), "descending_triangle")]
    return []

PATS = dict(orb30=orb, prev_day_break=pdhl, parallel_channel_break=channel, descending_triangle=triangle)

def run(path, name):
    days = load(path); keys = list(days); trades = []
    for n, d in enumerate(keys):
        prev = days[keys[n - 1]] if n else None
        for pn, fn in PATS.items():
            for tr in fn(days[d], prev):
                if tr: tr.update(date=d, index=name); trades.append(tr)
    return keys, trades

def summarize(keys, trades):
    out = {}
    for pn in PATS:
        t = [x for x in trades if x["pattern"] == pn]; rs = [x["r"] for x in t]; wins = [r for r in rs if r > 0]; loss = [-r for r in rs if r <= 0]
        out[pn] = dict(days=len(keys), trigger_days=len({x["date"] for x in t}), trades=len(t),
                       win_rate=round(len(wins) / len(rs) * 100, 1) if rs else None, avg_r=round(sum(rs) / len(rs), 3) if rs else None,
                       total_r=round(sum(rs), 2), profit_factor=round(sum(wins) / sum(loss), 2) if loss and wins else None,
                       best_r=max(rs) if rs else None, worst_r=min(rs) if rs else None,
                       avg_best_possible_r=round(sum(x["mfe_r"] for x in t) / len(t), 2) if t else None)
    return out

if __name__ == "__main__":
    d = sys.argv[1]; res = {"summary": {}, "trades": []}
    for nm in ("NSEI", "BSESN"):
        p = os.path.join(d, f"{nm}_5m.csv")
        if not os.path.exists(p): continue
        keys, tr = run(p, nm); res["summary"][nm] = dict(first=keys[0], last=keys[-1], patterns=summarize(keys, tr)); res["trades"] += tr
    json.dump(res, open(sys.argv[2], "w"), separators=(",", ":"))
    for nm, s in res["summary"].items():
        print(nm, s["first"], "to", s["last"])
        for pn, v in s["patterns"].items(): print(" ", pn, v)
