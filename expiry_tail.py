"""Did the expiry-week entries produce big losses? Read only. Compares trades entered in the last 2 sessions up to expiry (the blocked set)
with all other trades, on the futures model (premium, slippage, roll) and on index P&L. Also prints the worst trades and the drawdown with and without the block.
  python expiry_tail.py NSEI|NSEBANK|BSESN [--shift N]      (shift moves the assumed expiry weekday by N days, a sensitivity check)"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, os.path.join(HERE, "paper")); sys.path.insert(0, HERE)
import mock_detail as D, expiry_backtest as X, futures_paper as F
def dd(rows):
    cum = peak = worst = 0
    for d, p in sorted(rows): cum += p; peak = max(peak, cum); worst = max(worst, peak - cum)
    return worst
def main():
    nm = [a for a in sys.argv[1:] if a in D.CFG][0]; sh = int(sys.argv[sys.argv.index("--shift") + 1]) if "--shift" in sys.argv else 0
    if sh: D.EXP_WD = {k: (v + sh) % 5 for k, v in D.EXP_WD.items()}
    m, kept = X.build_kept(nm); dc = {d: m.B[m.dlast[d]]["c"] for d in m.days}; cal = F.Cal(nm, m.days); rows = []
    for t in kept:
        r = F.model_trade(cal, dc, t["dir"], t["d"], t["p0"], t["d_ex"], t["p1"], m.u, nm == "BSESN")
        rows.append(dict(d=t["d"], ex=t["d_ex"], fut=r["pnl"], idx=t["pnl"], blk=cal.blocked(t["d"])))
    B = [r for r in rows if r["blk"]]; O = [r for r in rows if not r["blk"]]
    def line(name, R):
        L = [r["fut"] for r in R]; w = [x for x in L if x > 0]
        return "  %-22s %4d trades  net %10s  win %3.0f%%  avg %7s  worst %8s  worst 3 sum %9s  best %9s" % (name, len(R), format(round(sum(L)), ","), 100 * len(w) / max(len(L), 1), format(round(sum(L) / max(len(L), 1)), ","), format(round(min(L)), ","), format(round(sum(sorted(L)[:3])), ","), format(round(max(L)), ","))
    print("\n%s (expiry weekday shift %d), futures model Rs" % (D.NAMES[nm], sh)); print(line("expiry-week entries", B)); print(line("all other entries", O))
    big = -0.5 * abs(min(r["fut"] for r in rows)) if False else None
    thr = sorted(r["fut"] for r in rows)[int(len(rows) * 0.05)]
    print("  worst 5%% of all trades start at a loss of Rs %s or worse; %d of those %d are expiry-week entries (expiry-week share of all trades: %.0f%%)" % (format(round(-thr), ","), sum(1 for r in rows if r["fut"] <= thr and r["blk"]), sum(1 for r in rows if r["fut"] <= thr), 100 * len(B) / len(rows)))
    print("  max drop with expiry-week entries %s, without %s" % (format(round(dd([(r["ex"], r["fut"]) for r in rows])), ","), format(round(dd([(r["ex"], r["fut"]) for r in O])), ",")))
    print("  worst 3 expiry-week entries:", [(r["d"], round(r["fut"])) for r in sorted(B, key=lambda r: r["fut"])[:3]])
    print("  best 3 expiry-week entries: ", [(r["d"], round(r["fut"])) for r in sorted(B, key=lambda r: -r["fut"])[:3]])
main()
