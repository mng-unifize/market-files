"""Recent vs long history, trade lengths, biggest winners, rolling 3-year results. Read only. Uses functions from 2026-11-21-trend_daily.py.
Usage: python3 2026-11-21-trend_recent.py   (needs data/kite-daily-futures). 1 ATR = Rs 10,000 per market; double RISK to see double the rupees and drawdown."""
import sys, os, csv, glob, datetime as dt, statistics as S, collections
src = open("2026-11-21-trend_daily.py").read().split("markets = {")[0]
sys.argv = ["x"]; exec(src)
EXC = {"GOLD", "SILVER", "CRUDEOILM"}
markets = {os.path.basename(p).replace("_day.csv", ""): load(p) for p in sorted(glob.glob(folder + "/*_day.csv")) if "_instruments" not in p and os.path.basename(p).replace("_day.csv", "") not in EXC}
def run2(nm, rows, rule):
    pos = positions(rows, rule); A = atr(rows); lot = lots.get(nm, 1.0); pnl = {}; size = 0.0; prev = 0; tr = []; cur = 0.0; din = None
    for i in range(1, len(rows)):
        d = rows[i][0]; p = (rows[i][4] - rows[i - 1][4]) * lot * size * prev
        if pos[i] != prev:
            if prev != 0: p -= COST * rows[i][4] * lot * size; cur += p; tr.append((nm, prev, din, d, (dt.date.fromisoformat(d) - dt.date.fromisoformat(din)).days, cur)); cur = 0.0
            if pos[i] != 0: size = RISK / (A[i] * lot); c = COST * rows[i][4] * lot * size; p -= c; cur = -c; din = d
            pnl[d] = pnl.get(d, 0) + p; prev = pos[i]; continue
        if prev != 0: cur += p
        pnl[d] = pnl.get(d, 0) + p
    return pnl, tr
last = max(r[-1][0] for r in markets.values()); LD = dt.date.fromisoformat(last)
for rule, lab in (("A", "Breakout 55/20"), ("B", "Crossover 20/100")):
    P = {}; T = []
    for nm, rows in markets.items(): P[nm], t = run2(nm, rows, rule); T += t
    port = collections.defaultdict(float)
    for nm in P:
        for d, v in P[nm].items(): port[d] += v
    print("\n===== %s (9 markets, data to %s) =====" % (lab, last))
    print("Window       portfolio net  drawdown  net/DD |   " + "  ".join("%-9s" % n[:9] for n in sorted(P)))
    for yrs in (1, 2, 3, 5, 10, 99):
        cut = (LD - dt.timedelta(days=365 * yrs)).isoformat() if yrs < 99 else "0000"
        ds = sorted(d for d in port if d >= cut); tot = sum(port[d] for d in ds); eq = pk = m = 0
        for d in ds: eq += port[d]; pk = max(pk, eq); m = min(m, eq - pk)
        print("last %-2s yrs   %+8.1fL  %7.1fL  %5.2f |   %s" % (yrs if yrs < 99 else "all", tot / 1e5, m / 1e5, tot / -m if m else 0, "  ".join("%+8.1fL" % (sum(v for d, v in P[n].items() if d >= cut) / 1e5) for n in sorted(P))))
    print("Rolling 3-year net (start year: net): " + "  ".join("%d: %+.1fL" % (y, sum(v for d, v in port.items() if "%d" % y <= d[:4] <= "%d" % (y + 2)) / 1e5) for y in range(2016, 2025)))
    w = [t for t in T if t[5] > 0]; l = [t for t in T if t[5] <= 0]
    print("Days held: winners avg %d (median %d), losers avg %d (median %d)" % (S.mean(t[4] for t in w), S.median(t[4] for t in w), S.mean(t[4] for t in l), S.median(t[4] for t in l)))
    print("Biggest loss %.0f (%.1fx the Rs 10,000 risk unit), biggest win %.0f (%.1fx)" % (min(t[5] for t in T), min(t[5] for t in T) / RISK, max(t[5] for t in T), max(t[5] for t in T) / RISK))
    tot = sum(t[5] for t in T); top = sorted(T, key=lambda t: -t[5])
    print("Top 10 trades = %.0f%% of net profit. Wins above 3x risk unit: %d of %d trades (%.0f%%)" % (100 * sum(t[5] for t in top[:10]) / tot, sum(1 for t in T if t[5] > 3 * RISK), len(T), 100 * sum(1 for t in T if t[5] > 3 * RISK) / len(T)))
    for t in top[:10]: print("   %-10s %-5s %s to %s  %3d days  %+7.2fL" % (t[0], "long" if t[1] > 0 else "short", t[2], t[3], t[4], t[5] / 1e5))
    yy = collections.Counter(t[3][:4] for t in top[:20]); print("Top 20 trades by exit year:", dict(sorted(yy.items())))
