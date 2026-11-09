"""Backtest of expiry-week rules on the same trades the dashboard uses (5 years of candles, same skip rules). Read only.

Variants, per index:
  BASE   the recipe as it runs today
  NOENT2 no new entries on the last 2 trading sessions up to and including expiry day (T-1 and T)
  NOENT3 same, last 3 sessions
  ROLL2  NOENT2, plus a roll (close near month, open next month) on every trade carried over the expiry, costs only
  FORCE2 close every open trade at the close of the session 2 sessions before expiry; no new entries from that session until after expiry;
         no re-entry until the next crossover signal
  FORCE3 same, 3 sessions before expiry
Prices are index prices, as in the dashboard, so the futures premium that decays toward expiry is NOT in these numbers.
Expiry = last Tuesday (Nifty, Bank Nifty) or last Thursday (Sensex) of the month, moved back to the previous session on a holiday.
That is today's NSE and BSE rule applied to all 5 years, so older expiry dates are approximate."""
import sys, os, datetime as dt, collections
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import mock_detail as D, mock_trades as MT
from mock_trades import M, ema, rules_, combine, tcost

def expiries(m, nm):
    days = m.days; out = []
    d0 = dt.date.fromisoformat(days[0]); d1 = dt.date.fromisoformat(days[-1]); y, mo = d0.year, d0.month
    while (y, mo) <= (d1.year, d1.month):
        e = D.last_weekday(y, mo, D.EXP_WD[nm]).isoformat()
        prior = [d for d in days if d <= e]
        if prior and e <= days[-1]: out.append(prior[-1])
        elif prior and e > days[-1]: out.append(None)      # expiry still ahead of the data
        y, mo = (y + (mo == 12), mo % 12 + 1)
    return [e for e in out if e], days

def build_kept(nm):
    tf, fa, sl, rule_names = D.CFG[nm]; work = "/tmp/bt_work"
    combine(nm, work); m = M(nm, D.resample(nm, work, tf)); B = m.B
    c = [b["c"] for b in B]; m.E9 = ema(c, fa); m.E21 = ema(c, sl); m.start = m.S + dt.timedelta(days=60)
    T = D.fix_prevlen(m, [t for t in m.gen("hold") if dt.date.fromisoformat(t["d"]) >= m.start])
    R = rules_(m, [t for t in T if m.year(t["d"]) < 3]); R = {k: R[k] for k in rule_names}
    kept = [t for t in T if not any(f(t) for f in R.values())]
    if nm in D.STOP_LOSS: bl = D.loss_stop_flags(kept, D.STOP_LOSS[nm]); kept = [t for t in kept if id(t) not in bl]
    return m, kept

def stats(m, T):
    if not T: return None
    pnl = [t["pnl"] for t in T]; w = [p for p in pnl if p > 0]; l = [-p for p in pnl if p <= 0]
    cum = peak = dd = 0
    for t in sorted(T, key=lambda t: (t["d_ex"], t["ex"])):
        cum += t["pnl"]; peak = max(peak, cum); dd = max(dd, peak - cum)
    top = sorted(pnl, reverse=True)[:10]
    yrs = collections.defaultdict(float)
    for t in T: yrs[m.year(t["d"])] += t["pnl"]
    return dict(n=len(T), net=sum(pnl), win=100 * len(w) / len(T), pf=(sum(w) / sum(l)) if l else 0, dd=dd,
                top10=100 * sum(top) / sum(w) if w else 0, years=[round(yrs[k]) for k in range(m.nyears)])

def variants(nm, m, kept):
    exps, days = expiries(m, nm); B = m.B; u = m.u; bse = nm == "BSESN"; idx = {d: i for i, d in enumerate(days)}
    out = {"BASE": kept}
    def window(n):   # entry days blocked: last n sessions up to and including expiry
        s = set()
        for e in exps:
            i = idx[e]
            for k in range(n): s.add(days[i - k])
        return s
    for n in (2, 3):
        w = window(n); out["NOENT%d" % n] = [t for t in kept if t["d"] not in w]
    # roll costs on trades carried over the expiry (close at T-1, open next month)
    base2 = out["NOENT2"]; rolled = []; nroll = 0
    for t in base2:
        extra = 0
        for e in exps:
            i = idx[e]; x = days[i - 1] if i else e
            if t["d"] <= x < t["d_ex"]:
                p = B[m.dlast[x]]["c"]; extra += tcost(p, p, u, t["dir"], x, x, bse); nroll += 1
        rolled.append(dict(t, pnl=t["pnl"] - extra, cost=t["cost"] + extra))
    out["ROLL2"] = rolled
    for k in (2, 3):
        block = set(); xs = {}
        for e in exps:
            i = idx[e]; x = days[i - k]; xs[x] = e
            for j in range(i - k, i + 1): block.add(days[j])
        res = []
        for t in kept:
            if t["d"] in block: continue
            span = [x for x in xs if t["d"] <= x and t["d_ex"] > x]
            if span:
                x = min(span); p1 = B[m.dlast[x]]["c"]
                gross = t["dir"] * (p1 - t["p0"]) * u; cst = tcost(t["p0"], p1, u, t["dir"], t["d"], x, bse)
                t = dict(t, p1=p1, gross=gross, cost=cst, pnl=gross - cst, d_ex=x, ex=m.dlast[x])
            res.append(t)
        out["FORCE%d" % k] = res
    return out, nroll, len(exps)

def main():
    sh=int(os.environ.get('EXPSHIFT','0'))
    if sh: D.EXP_WD={k:(v+sh)%5 for k,v in D.EXP_WD.items()}
    names = sys.argv[1:] or list(D.CFG)
    for nm in names:
        m, kept = build_kept(nm); out, nroll, ne = variants(nm, m, kept)
        print("\n%s: %d trades over %.1f years, %d expiries, %d trades carried over an expiry (after NOENT2)" % (D.NAMES[nm], len(kept), (dt.date.fromisoformat(m.days[-1]) - dt.date.fromisoformat(m.days[0])).days / 365.25, ne, nroll))
        print("  %-7s %5s %11s %6s %5s %10s %8s  %s" % ("variant", "trades", "net Rs", "win%", "PF", "max drop", "top10%", "net by year"))
        for k, T in out.items():
            s = stats(m, T)
            print("  %-7s %5d %11s %5.0f%% %5.2f %10s %7.0f%%  %s" % (k, s["n"], format(round(s["net"]), ","), s["win"], s["pf"], format(round(s["dd"]), ","), s["top10"], s["years"]))
        sys.stdout.flush()

if __name__ == "__main__": main()
