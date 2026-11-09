"""Per-trade loss cap backtest. Read only. Takes the same 5 year trades the dashboard uses and adds a stop: if price moves against the entry by X percent
the trade is closed at the stop (or at the open if the market gapped through it). Priced with the futures model (premium, slippage, roll, charges).
After a stop the position stays flat until the next crossover signal, which is the next trade in the list.
Reports what the stop saves (the worst losses, the max drop) and what it costs (winners it cut, big winners lost, total profit).
  python stop_backtest.py NSEI|NSEBANK|BSESN"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, os.path.join(HERE, "paper")); sys.path.insert(0, HERE)
import mock_detail as D, expiry_backtest as X, futures_paper as F
LEVELS = [None, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03]

def apply_stop(m, t, x):
    """Returns (d_out, p_out, stopped). Bars between entry and exit are checked from the bar after entry."""
    if x is None: return t["d_ex"], t["p1"], False
    B = m.B; p0 = t["p0"]; lvl = p0 * (1 - x * t["dir"])
    for i in range(t["ent"] + 1, t["ex"] + 1):
        b = B[i]
        if t["dir"] == 1 and b["l"] <= lvl: return b["d"], min(lvl, b["o"]) if b["o"] < lvl else lvl, True
        if t["dir"] == -1 and b["h"] >= lvl: return b["d"], max(lvl, b["o"]) if b["o"] > lvl else lvl, True
    return t["d_ex"], t["p1"], False

def main():
    nm = [a for a in sys.argv[1:] if a in D.CFG][0]
    m, kept = X.build_kept(nm); dc = {d: m.B[m.dlast[d]]["c"] for d in m.days}; cal = F.Cal(nm, m.days); bse = nm == "BSESN"
    base = [F.model_trade(cal, dc, t["dir"], t["d"], t["p0"], t["d_ex"], t["p1"], m.u, bse)["pnl"] for t in kept]
    top = sorted(base, reverse=True)[:int(len(base) * 0.05)]; bigcut = min(top)
    print("\n%s: %d trades, futures model Rs. Big winner = top 5%% of baseline trades (Rs %s or more)." % (D.NAMES[nm], len(kept), format(round(bigcut), ",")))
    print("  %-7s %9s %6s %5s %10s %10s %8s %9s %12s %12s" % ("stop", "net Rs", "win%", "PF", "max drop", "worst", "stopped", "cut wins", "big wins cut", "top5% share"))
    worse = []
    for x in LEVELS:
        P = []; stopped = cutwin = bigwin = 0
        for t, b in zip(kept, base):
            d_out, p_out, st = apply_stop(m, t, x)
            p = F.model_trade(cal, dc, t["dir"], t["d"], t["p0"], d_out, p_out, m.u, bse)["pnl"] if st else b
            P.append((d_out, p))
            if st and x == 0.02 and p < b - 5000: worse.append((t["d"], t["dir"], round(t["p0"]), d_out, round(p_out), t["d_ex"], round(t["p1"]), round(b), round(p)))
            if st:
                stopped += 1
                if b > 0: cutwin += 1
                if b >= bigcut: bigwin += 1
        v = [p for _, p in P]; w = sum(p for p in v if p > 0); l = -sum(p for p in v if p <= 0); cum = peak = dd = 0
        for _, p in sorted(P): cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
        t5 = sorted(v, reverse=True)[:int(len(v) * 0.05)]
        if x == 0.03 and worse: print("  at 2.00%, stops that ended worse than just holding (entry day, dir, entry px, stop day, stop px, normal exit day, normal exit px, normal Rs, stopped Rs):", worse)
        print("  %-7s %9s %5.0f%% %5.2f %10s %10s %8d %9d %12d %11.0f%%" % ("none" if x is None else "%.2f%%" % (x * 100), format(round(sum(v)), ","), 100 * sum(1 for p in v if p > 0) / len(v), w / l if l else 0,
              format(round(dd), ","), format(round(min(v)), ","), stopped, cutwin, bigwin, 100 * sum(t5) / w if w else 0)); sys.stdout.flush()
main()
