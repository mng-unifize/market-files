"""Rule-fixed daily trend test across markets (no tuning). Read only: reads CSVs, prints tables, writes nothing.
Usage: python3 2026-11-21-trend_daily.py [folder]   (default data/kite-daily-futures; needs _instruments.csv for lot sizes, else lot=1)
Rules: A = Donchian breakout (enter 55-day high/low close, exit 20-day), B = EMA 20/100 crossover. Long and short. Signal and fill at the close.
Size: fractional lots so a 1-ATR(20) move = Rs 10,000 per market. Cost: 0.05% of contract value per side (fees + slippage)."""
import csv, os, sys, glob, statistics as S, collections
RISK = 10000.0; COST = 0.0005
folder = sys.argv[1] if len(sys.argv) > 1 else "data/kite-daily-futures"
lots = {}
if os.path.exists(folder + "/_instruments.csv"):
    for r in csv.DictReader(open(folder + "/_instruments.csv")): lots[r["name"]] = float(r["lot_size"])
def load(p):
    rows = list(csv.DictReader(open(p))); k = "date" if "date" in rows[0] else "ts_ist"
    return [(r[k][:10], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rows]
def ema(x, n):
    a = 2 / (n + 1); out = [x[0]]
    for v in x[1:]: out.append(out[-1] + a * (v - out[-1]))
    return out
def atr(rows, n=20):
    tr = [rows[0][2] - rows[0][3]] + [max(rows[i][2] - rows[i][3], abs(rows[i][2] - rows[i - 1][4]), abs(rows[i][3] - rows[i - 1][4])) for i in range(1, len(rows))]
    return [S.mean(tr[max(0, i - n + 1):i + 1]) for i in range(len(tr))]
def positions(rows, rule):
    c = [r[4] for r in rows]; n = len(c); pos = [0] * n; cur = 0
    if rule == "B":
        e1, e2 = ema(c, 20), ema(c, 100)
        for i in range(100, n): pos[i] = 1 if e1[i] > e2[i] else -1
        return pos
    for i in range(55, n):
        hi55 = max(r[2] for r in rows[i - 55:i]); lo55 = min(r[3] for r in rows[i - 55:i]); hi20 = max(r[2] for r in rows[i - 20:i]); lo20 = min(r[3] for r in rows[i - 20:i])
        if c[i] > hi55: cur = 1
        elif c[i] < lo55: cur = -1
        elif cur == 1 and c[i] < lo20: cur = 0
        elif cur == -1 and c[i] > hi20: cur = 0
        pos[i] = cur
    return pos
def run(nm, rows, rule):
    pos = positions(rows, rule); A = atr(rows); lot = lots.get(nm, 1.0); pnl = {}; size = 0.0; prev = 0; tl = []; cur = 0.0
    for i in range(1, len(rows)):
        d = rows[i][0]; move = (rows[i][4] - rows[i - 1][4]) * lot
        p = move * size * prev
        if pos[i] != prev:
            if prev != 0: p -= COST * rows[i][4] * lot * size; cur += p; tl.append(cur); cur = 0.0; p = 0.0 if False else p
            if pos[i] != 0:
                size = RISK / (A[i] * lot); c = COST * rows[i][4] * lot * size; p -= c; cur = -c
            pnl[d] = pnl.get(d, 0) + p; prev = pos[i]; continue
        if prev != 0: cur += p
        pnl[d] = pnl.get(d, 0) + p
    return pnl, tl
def dd(series):
    eq = pk = m = 0
    for v in series: eq += v; pk = max(pk, eq); m = min(m, eq - pk)
    return m
markets = {os.path.basename(p).replace("_day.csv", "").replace("_daily.csv", ""): load(p) for p in sorted(glob.glob(folder + "/*_day.csv") + glob.glob(folder + "/*_daily.csv")) if "_instruments" not in p}
print("markets:", {k: "%s to %s (%d days)" % (v[0][0], v[-1][0], len(v)) for k, v in markets.items()})
for rule, lab in (("A", "Donchian 55/20"), ("B", "EMA 20/100")):
    print("\n== %s | 1 ATR move = Rs 10,000 per market, cost 0.05%%/side" % lab)
    alltl = []; allp = collections.defaultdict(float); yrs = collections.defaultdict(lambda: collections.defaultdict(float))
    for nm, rows in markets.items():
        pnl, tl = run(nm, rows, rule); tr = len(tl); ds = sorted(pnl); tot = sum(pnl.values())
        alltl += tl
        for d in ds: allp[d] += pnl[d]; yrs[nm][d[:4]] += pnl[d]
        print("%-10s trades %3d net %+8.2fL drawdown %6.2fL net/DD %5.2f | by year: %s" % (nm, tr, tot / 1e5, dd([pnl[d] for d in ds]) / 1e5, tot / -dd([pnl[d] for d in ds]) if dd([pnl[d] for d in ds]) else 0, " ".join("%s %+.1f" % (y[2:], v / 1e5) for y, v in sorted(yrs[nm].items()))))
    ds = sorted(allp); tot = sum(allp.values()); py = collections.defaultdict(float)
    for d in ds: py[d[:4]] += allp[d]
    print("PORTFOLIO  net %+8.2fL drawdown %6.2fL net/DD %5.2f | by year: %s" % (tot / 1e5, dd([allp[d] for d in ds]) / 1e5, tot / -dd([allp[d] for d in ds]) if dd([allp[d] for d in ds]) else 0, " ".join("%s %+.1f" % (y[2:], v / 1e5) for y, v in sorted(py.items()))))
    # how often, how concentrated, how likely
    import random; random.seed(7)
    w = [t for t in alltl if t > 0]; l = [t for t in alltl if t <= 0]; top = sorted(alltl, reverse=True)[:5]
    print("TRADES: %d, hit rate %.0f%%, avg win %+.0f avg loss %+.0f, top 5 trades = %.0f%% of net profit" % (len(alltl), 100 * len(w) / len(alltl), S.mean(w), S.mean(l), 100 * sum(top) / sum(alltl) if sum(alltl) > 0 else 0))
    mo = collections.defaultdict(float)
    for d in ds: mo[d[:7]] += allp[d]
    m = [mo[k] for k in sorted(mo)]
    print("MONTHS: %d, profitable %.0f%%, years profitable %d of %d" % (len(m), 100 * sum(1 for v in m if v > 0) / len(m), sum(1 for v in py.values() if v > 0), len(py)))
    for h in (6, 12):
        roll = [sum(m[i:i + h]) for i in range(len(m) - h + 1)]
        sims = [sum(random.choice(m) for _ in range(h)) for _ in range(5000)]; sims.sort()
        print("%2d-MONTH: history positive %.0f%% of windows, worst %+.1fL | shuffled-months odds of a loss %.0f%%, 5th percentile %+.1fL, median %+.1fL" % (h, 100 * sum(1 for v in roll if v > 0) / len(roll), min(roll) / 1e5, 100 * sum(1 for v in sims if v < 0) / 5000, sims[250] / 1e5, sims[2500] / 1e5))
