"""Monthly loss stop backtest. Read only. Rule (same code as the engine, mock_detail.loss_stop_flags): once the trades closed in a calendar month are down
by the limit, no NEW trade is entered in that index for the rest of that month; signals resume the next month. Open trades are not closed by it.
Compares: no stop, the limits below, on the 5 year trades, priced with the futures model. Shows what it saves (worst month, max drop) and what it costs (profit, big winners blocked).
  python monthly_stop_backtest.py NSEI|NSEBANK|BSESN"""
import sys, os, datetime as dt, collections
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, os.path.join(HERE, "paper")); sys.path.insert(0, HERE)
import mock_detail as D, futures_paper as F
from mock_trades import M, ema, rules_, combine
LIMITS = [None, 30000, 40000, 50000, 63000, 80000]

def trades_before_stop(nm):
    tf, fa, sl, rule_names = D.CFG[nm]; work = "/tmp/ms_work"; combine(nm, work); m = M(nm, D.resample(nm, work, tf)); B = m.B
    c = [b["c"] for b in B]; m.E9 = ema(c, fa); m.E21 = ema(c, sl); m.start = m.S + dt.timedelta(days=60)
    T = D.fix_prevlen(m, [t for t in m.gen("hold") if dt.date.fromisoformat(t["d"]) >= m.start])
    R = rules_(m, [t for t in T if m.year(t["d"]) < 3]); R = {k: R[k] for k in rule_names}
    return m, [t for t in T if not any(f(t) for f in R.values())]

def main():
    nm = [a for a in sys.argv[1:] if a in D.CFG][0]; m, kept = trades_before_stop(nm)
    dc = {d: m.B[m.dlast[d]]["c"] for d in m.days}; cal = F.Cal(nm, m.days); bse = nm == "BSESN"
    fut = {id(t): F.model_trade(cal, dc, t["dir"], t["d"], t["p0"], t["d_ex"], t["p1"], m.u, bse)["pnl"] for t in kept}
    big = sorted(fut.values(), reverse=True)[:int(len(kept) * 0.05)][-1]
    print("\n%s: %d trades before any monthly stop. Big winner = top 5%% of trades (Rs %s or more). Futures model Rs." % (D.NAMES[nm], len(kept), format(round(big), ",")))
    print("  %-8s %6s %9s %6s %5s %10s %12s %9s %12s %14s" % ("limit", "trades", "net Rs", "win%", "PF", "max drop", "worst month", "months<-50k", "blocked", "big winners blocked"))
    for lim in LIMITS:
        bl = D.loss_stop_flags(kept, lim) if lim else set(); K = [t for t in kept if id(t) not in bl]
        v = [(t["d_ex"], fut[id(t)]) for t in K]; w = sum(p for _, p in v if p > 0); l = -sum(p for _, p in v if p <= 0); cum = peak = dd = 0
        for _, p in sorted(v): cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
        mo = collections.defaultdict(float)
        for d, p in v: mo[d[:7]] += p
        print("  %-8s %6d %9s %5.0f%% %5.2f %10s %12s %9d %12d %14d" % ("none" if not lim else "Rs %dk" % (lim // 1000), len(K), format(round(sum(p for _, p in v)), ","), 100 * sum(1 for _, p in v if p > 0) / len(v), w / l,
              format(round(dd), ","), format(round(min(mo.values())), ","), sum(1 for x in mo.values() if x <= -50000), len(bl), sum(1 for t in kept if id(t) in bl and fut[id(t)] >= big))); sys.stdout.flush()
main()
