"""Futures-realistic paper model, run ALONGSIDE the dashboard. Reads the dashboard's trades, writes new files only, overwrites nothing.

What the dashboard does: P&L on the index price, no futures premium, no slippage, no roll.
What this model adds, per trade:
  1. Futures price = index close x (1 + CARRY x days_to_expiry / 365). Premium shrinks toward expiry, so a long pays it away and a short earns it.
  2. Every fill is slipped against you by SLIP of the price (buy higher, sell lower). Marketable limit orders.
  3. A trade carried to the session before expiry is rolled: sold on the near contract and re-opened on the next, both slipped, both charged.
  4. Second view with the expiry rule: no new entries in the last 2 sessions up to and including expiry.
CARRY (6.5 percent a year, about 0.5 percent a month) and SLIP (0.01 percent a side: half the spread plus the price drift in the 20 to 60 seconds between candle close and your order) are ASSUMPTIONS until the fill logger (step 4) measures real fills.

  python futures_paper.py                    live window (trades since 2026-10-01 in data/mock/dash/trades.json), writes data/mock/dash/futures_model*.{json,csv}
  python futures_paper.py --history          the same model on the 5-year backtest trades, printed only
  python futures_paper.py --carry 0.09 --slip 0.0005      change the assumptions"""
import sys, os, csv, json, collections, datetime as dt, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, os.path.join(HERE, "paper")); sys.path.insert(0, HERE)
import mock_detail as D
from mock_trades import tcost, combine, LOTS
CARRY, SLIP, NEXT_DAYS, BLOCK = 0.065, 0.0001, 4, 2
HOL = set()
if os.path.exists("holidays.txt"):
    for ln in open("holidays.txt"):
        s = ln.split("#")[0].strip()
        if s: HOL.add(s)
CODE = {v: k for k, v in D.NAMES.items()}

class Cal:
    """Trading days and expiry dates for one index. days = sorted list of days that have data."""
    def __init__(self, nm, days):
        self.nm = nm; self.days = days; self.set = set(days); self.last = days[-1]
    def trading(self, d):
        d = d if isinstance(d, str) else d.isoformat()
        return dt.date.fromisoformat(d).weekday() < 5 and d not in HOL and (d > self.last or d in self.set)
    def expiry(self, y, m):
        e = D.last_weekday(y, m, D.EXP_WD[self.nm])
        while not self.trading(e): e -= dt.timedelta(1)
        return e.isoformat()
    def expiries_from(self, day, n=3):
        d = dt.date.fromisoformat(day); y, m = d.year, d.month; out = []
        if m == 1: y, m = y - 1, 12
        else: m -= 1
        for _ in range(n + 2):
            e = self.expiry(y, m)
            if e >= day: out.append(e)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out[:n]
    def contract(self, day):
        ex = self.expiries_from(day)
        return ex[1] if (dt.date.fromisoformat(ex[0]) - dt.date.fromisoformat(day)).days <= NEXT_DAYS else ex[0]
    def roll_day(self, e):
        d = dt.date.fromisoformat(e) - dt.timedelta(1)
        while not self.trading(d): d -= dt.timedelta(1)
        return d.isoformat()
    def blocked(self, day):
        e = self.expiries_from(day)[0]; n = 0; d = dt.date.fromisoformat(day)
        while d.isoformat() <= e:
            n += self.trading(d); d += dt.timedelta(1)
        return n <= BLOCK
    def after(self, e):
        return [x for x in self.expiries_from(e, 3) if x > e][0]

def fut(S, day, e, carry): return S * (1 + carry * (dt.date.fromisoformat(e) - dt.date.fromisoformat(day)).days / 365)

def model_trade(cal, dclose, side_dir, d_in, p_in, d_out, p_out, u, bse, carry=CARRY, slip=SLIP):
    """Returns dict(pnl, cost, rolled, premium_pnl). Index prices in, futures economics out."""
    c = cal.contract(d_in); segs = []; px_in = fut(p_in, d_in, c, carry) * (1 + slip * side_dir); d0 = d_in; rolled = 0
    rd = cal.roll_day(c)
    if d_out > rd and d_in <= rd:
        S = dclose.get(rd, p_in); near = fut(S, rd, c, carry) * (1 - slip * side_dir)
        segs.append((px_in, near, d0, rd)); c2 = cal.after(c); px_in = fut(S, rd, c2, carry) * (1 + slip * side_dir); d0 = rd; c = c2; rolled = 1
    px_out = fut(p_out, d_out, c, carry) * (1 - slip * side_dir); segs.append((px_in, px_out, d0, d_out))
    gross = sum(side_dir * (b - a) * u for a, b, _, _ in segs); cost = sum(tcost(a, b, u, side_dir, x, y, bse) for a, b, x, y in segs)
    return dict(pnl=gross - cost, gross=gross, cost=cost, rolled=rolled, contract=c)

def daily_closes(nm, work):
    combine(nm, work); out = {}
    for r in csv.DictReader(open("%s/%s_5m.csv" % (work, nm))):
        t = r["ts_ist"]
        if "09:15" <= t[11:16] <= "15:30": out[t[:10]] = float(r["close"])
    return out

def live_window(carry, slip):
    T = json.load(open("data/mock/dash/trades.json")); G = collections.defaultdict(list)
    for r in T:
        if r["status"] != "Skipped": G[(r["index"], r["trade_no"])].append(r)
    res = []; work = tempfile.mkdtemp(prefix="fpaper_"); cache = {}
    for (name, no), rows in sorted(G.items()):
        nm = CODE[name]
        if nm not in cache: dc = daily_closes(nm, work); cache[nm] = (dc, Cal(nm, sorted(dc)))
        dc, cal = cache[nm]; rows.sort(key=lambda r: r["day"]); f = rows[0]; u = f["qty"]; d = 1 if f["side"] == "BUY" else -1
        closed = [r for r in rows if r["status"].startswith("Closed") and r.get("exit_price")]
        if closed: x = closed[-1]; d_out, p_out = x["day"], float(x["exit_price"])
        else: d_out = rows[-1]["day"]; p_out = float(rows[-1].get("mark_price") or dc.get(d_out) or f["entry_price"])
        m = model_trade(cal, dc, d, f["day"], float(f["entry_price"]), d_out, p_out, u, nm == "BSESN", carry, slip)
        old = sum(r["net"] for r in rows)
        res.append(dict(index=name, trade_no=no, side=f["side"], entry_day=f["day"], exit_day=d_out, state="closed" if closed else "open", dashboard_net=round(old),
                        futures_net=round(m["pnl"]), rolled=m["rolled"], contract=m["contract"], blocked_by_expiry_rule=cal.blocked(f["day"])))
    return res

def summarize(res):
    out = []
    for name in sorted({r["index"] for r in res}) + ["ALL"]:
        R = [r for r in res if name == "ALL" or r["index"] == name]; K = [r for r in R if not r["blocked_by_expiry_rule"]]
        cl = [r for r in R if r["state"] == "closed"]
        out.append(dict(index=name, trades=len(R), closed=len(cl), dashboard_net=sum(r["dashboard_net"] for r in R), futures_net=sum(r["futures_net"] for r in R),
                        futures_net_with_expiry_rule=sum(r["futures_net"] for r in K), trades_blocked=len(R) - len(K), rolled=sum(r["rolled"] for r in R)))
    return out

def show(rows, title):
    print("\n" + title); print("  %-11s %6s %7s %14s %14s %16s %8s %6s" % ("index", "trades", "closed", "dashboard Rs", "futures Rs", "futures+rule Rs", "blocked", "rolled"))
    for s in rows: print("  %-11s %6d %7d %14s %14s %16s %8d %6d" % (s["index"], s["trades"], s["closed"], format(s["dashboard_net"], ","), format(s["futures_net"], ","), format(s["futures_net_with_expiry_rule"], ","), s["trades_blocked"], s["rolled"]))

def history(carry, slip):
    import expiry_backtest as X
    print("\n5-year backtest trades, index P&L (dashboard basis) vs futures model. Carry %.1f%%/yr, slip %.2f%%/side." % (carry * 100, slip * 100))
    print("  %-11s %6s %14s %14s %10s %16s %8s" % ("index", "trades", "index basis Rs", "futures Rs", "haircut", "futures+rule Rs", "rolled"))
    only = [x for x in sys.argv[1:] if x in D.CFG]
    for nm in (only or D.CFG):
        m, kept = X.build_kept(nm); dc = {d: m.B[m.dlast[d]]["c"] for d in m.days}; cal = Cal(nm, m.days)
        base = fut_all = fut_rule = rolled = 0
        for t in kept:
            r = model_trade(cal, dc, t["dir"], t["d"], t["p0"], t["d_ex"], t["p1"], m.u, nm == "BSESN", carry, slip)
            base += t["pnl"]; fut_all += r["pnl"]; rolled += r["rolled"]
            if not cal.blocked(t["d"]): fut_rule += r["pnl"]
        print("  %-11s %6d %14s %14s %9.0f%% %16s %8d" % (D.NAMES[nm], len(kept), format(round(base), ","), format(round(fut_all), ","), 100 * (fut_all - base) / base, format(round(fut_rule), ","), rolled)); sys.stdout.flush()

def main(a):
    carry, slip = CARRY, SLIP
    if "--carry" in a: carry = float(a[a.index("--carry") + 1])
    if "--slip" in a: slip = float(a[a.index("--slip") + 1])
    if "--history" in a: return history(carry, slip)
    res = live_window(carry, slip); sm = summarize(res)
    show(sm, "Live window since %s. Carry %.1f%%/yr, slip %.2f%%/side (assumptions)." % (D.START_LOG, carry * 100, slip * 100))
    os.makedirs("data/mock/dash", exist_ok=True)
    json.dump(dict(assumptions=dict(carry_per_year=carry, slip_per_side=slip, note="assumed until step 4 measures real fills"), summary=sm, trades=res), open("data/mock/dash/futures_model.json", "w"), indent=1)
    with open("data/mock/dash/futures_model_trades.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(res[0])); w.writeheader(); w.writerows(res)
    print("\nWrote data/mock/dash/futures_model.json and futures_model_trades.csv (new files; the dashboard files are untouched).")

if __name__ == "__main__": main(sys.argv[1:])
