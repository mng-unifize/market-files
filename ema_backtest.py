#!/usr/bin/env python3
# EMA 9/21 stop-and-reverse backtest on daily and hourly candles (standard library only).
# Usage: python3 ema_backtest.py data/index results.json
# Rules fixed in advance: signal at the close of the crossover candle, fill at that close, 0.02% cost per completed trade.
import sys, os, csv, json, collections, datetime as dt
COST = 0.0002; LOT = 65; OOS_SHARE = 1 / 3      # last third of the history is held out as the out-of-sample check

def load(p):
    return [(r["ts_ist"], float(r["open"]), float(r["close"])) for r in csv.DictReader(open(p))]

def ema(x, n):
    a = 2 / (n + 1); o = [x[0]]
    for v in x[1:]: o.append(a * v + (1 - a) * o[-1])
    return o

def trades(bars, flat_daily=False):
    cl = [b[2] for b in bars]; e9 = ema(cl, 9); e21 = ema(cl, 21); out = []; pos = 0; ent = None
    for i in range(1, len(bars)):
        last_of_day = flat_daily and (i + 1 == len(bars) or bars[i + 1][0][:10] != bars[i][0][:10])
        up = e9[i] > e21[i] and e9[i - 1] <= e21[i - 1]; dn = e9[i] < e21[i] and e9[i - 1] >= e21[i - 1]
        if flat_daily and pos and bars[i][0][:10] != ent[0][:10]: pos = 0              # safety: never carry overnight
        if (up or dn) and not last_of_day:
            if pos: out.append((ent[0], bars[i][0], pos, ent[1], cl[i]))
            pos = 1 if up else -1; ent = (bars[i][0], cl[i])
        elif flat_daily and last_of_day and pos:
            out.append((ent[0], bars[i][0], pos, ent[1], cl[i])); pos = 0
    if pos and not flat_daily: out.append((ent[0], bars[-1][0], pos, ent[1], cl[-1]))
    return out

def stats(tr):
    if not tr: return dict(trades=0)
    pts = [s * (b - a) - COST * a for _, _, s, a, b in tr]; pct = [x / a * 100 for x, (_, _, s, a, b) in zip(pts, tr)]
    w = [x for x in pts if x > 0]; l = [-x for x in pts if x <= 0]; cum = 0; peak = 0; dd = 0
    for x in pts: cum += x; peak = max(peak, cum); dd = max(dd, peak - cum)
    return dict(trades=len(tr), win_pct=round(len(w) / len(pts) * 100), net_points=round(sum(pts)), net_rupees_one_lot=round(sum(pts) * LOT),
                net_pct=round(sum(pct), 1), avg_win=round(sum(w) / len(w)) if w else 0, avg_loss=round(-sum(l) / len(l)) if l else 0,
                profit_factor=round(sum(w) / sum(l), 2) if l and w else None, max_drawdown_points=round(dd),
                long_points=round(sum(x for x, t in zip(pts, tr) if t[2] > 0)), short_points=round(sum(x for x, t in zip(pts, tr) if t[2] < 0)))

def split(tr, cut):
    return [t for t in tr if t[0][:10] < cut], [t for t in tr if t[0][:10] >= cut]

def by_year(tr):
    d = collections.defaultdict(list)
    for t in tr: d[t[1][:4]].append(t)
    return {y: stats(v) for y, v in sorted(d.items())}

def by_weekday(tr):
    d = collections.defaultdict(list)
    for t in tr: d[dt.date.fromisoformat(t[0][:10]).strftime("%a")].append(t)
    return {k: dict(trades=len(v), avg_points=round(stats(v)["net_points"] / len(v), 1)) for k, v in d.items()}

if __name__ == "__main__":
    res = {}
    for nm in ("NSEI", "BSESN"):
        for tag, flat in (("daily", False), ("hourly", False), ("hourly", True)):
            p = os.path.join(sys.argv[1], f"{nm}_{tag}.csv")
            if not os.path.exists(p): continue
            bars = load(p); tr = trades(bars, flat); key = f"{nm} {tag}" + (" intraday-only" if flat else " hold-overnight")
            first, last = bars[0][0][:10], bars[-1][0][:10]; days = sorted({b[0][:10] for b in bars}); cut = days[int(len(days) * (1 - OOS_SHARE))]
            ins, oos = split(tr, cut)
            res[key] = dict(first=first, last=last, split_date=cut, all=stats(tr), in_sample=stats(ins), out_of_sample=stats(oos),
                            by_year=by_year(tr) if tag == "daily" else None, by_entry_weekday=by_weekday(tr))
    json.dump(res, open(sys.argv[2], "w"), indent=1)
    for k, v in res.items():
        print(k, v["first"], "to", v["last"], "split", v["split_date"])
        for part in ("all", "in_sample", "out_of_sample"): print("  ", part, v[part])
