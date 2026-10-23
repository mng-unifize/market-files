"""Dashboard data for the chosen strategies, one per index.
Nifty and Sensex: 15-minute candles, 9/21 averages. Bank Nifty: 5-minute candles, 20/50 averages.
Run from the repo main folder:  python3 mock_detail2.py
Writes data/mock/dash/{trades,summary,bars,meta,strategies}.json"""
import json, os, csv, collections, datetime as dt
import mock_trades as MT
from mock_trades import M, NAMES, leg, tcost, rules_, combine, OUTD, ema, by_year
START_LOG = "2026-10-01"  # mock begins flat on this day

SYM = {"NSEI": "NIFTY", "NSEBANK": "BANKNIFTY", "BSESN": "SENSEX"}
EXP_WD = {"NSEI": 1, "NSEBANK": 1, "BSESN": 3}
MARGIN = 0.11
KEEP_BAR_DAYS = 16
# index: (candle minutes, fast, slow, skip rules for the recipe)
CFG = {"NSEI": (15, 9, 21, ["low volatility", "entries on Monday"]),
       "BSESN": (15, 9, 21, ["entries on Monday", "low volatility", "inside bar"]),
       "NSEBANK": (5, 20, 50, ["entries on Monday", "doji candle", "choppy (last trade under 30 min)"])}
RULE_WORDS = {"monthly loss stop": "monthly loss stop (this month is already down Rs 63,000 on paper, so no new trades until next month)", "low volatility": "low volatility", "entries on Monday": "Monday entries", "inside bar": "inside bar", "doji candle": "doji candle",
              "choppy (last trade under 30 min)": "choppy market (previous trade closed within 30 minutes on the same day)"}
APPROACH = "Hold overnight with skip rules"
STOP_LOSS = {"NSEBANK": 63000}   # Bank Nifty only: no new trades for the rest of a month once the month is down this much on paper
CAPITAL_START = 500000
VAR = [(APPROACH, None)]

def last_weekday(y, mth, wd):
    d = dt.date(y + (mth == 12), mth % 12 + 1, 1) - dt.timedelta(days=1)
    while d.weekday() != wd: d -= dt.timedelta(days=1)
    return d

def contract(nm, day):
    d = dt.date.fromisoformat(day); exp = last_weekday(d.year, d.month, EXP_WD[nm]); y, mth = d.year, d.month
    if d >= exp - dt.timedelta(days=4):
        mth += 1
        if mth == 13: y, mth = y + 1, 1
    return f"{SYM[nm]}{str(y)[2:]}{dt.date(y, mth, 1).strftime('%b').upper()}FUT"

def resample(nm, src_dir, tf):
    if tf == 5: return src_dir
    out = f"{src_dir}_tf{tf}"; os.makedirs(out, exist_ok=True); g = collections.OrderedDict()
    for r in csv.DictReader(open(f"{src_dir}/{nm}_5m.csv")):
        t = r["ts_ist"]; k = (t[:10], (int(t[11:13]) * 60 + int(t[14:16]) - 555) // tf)
        o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        if k not in g: g[k] = [t, o, h, l, c]
        else: g[k][2] = max(g[k][2], h); g[k][3] = min(g[k][3], l); g[k][4] = c
    with open(f"{out}/{nm}_5m.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"])
        for v in g.values(): w.writerow([v[0], v[1], v[2], v[3], v[4], 0])
    return out

def fill_time(ts, tf):
    mins = int(ts[11:13]) * 60 + int(ts[14:16]) + tf
    mins = min(mins, 15 * 60 + 30)
    return f"{mins // 60:02d}:{mins % 60:02d}"

def r0(x): return int(round(x))

def fix_prevlen(m, T):
    """The 'previous trade under 30 minutes' rule counts candles. A trade carried over a night spans only 1-2 candles in the data,
    so it was wrongly called short. Treat any previous trade that crossed a night as long."""
    B = m.B
    for k in range(1, len(T)):
        p = T[k - 1]
        if B[p["ent"]]["d"] != B[p["ex"]]["d"]: T[k]["prevlen"] = 99
    return T

def loss_stop_flags(rec, limit):
    """rec = the recipe's trades in order. Returns the set of ids of trades blocked by the monthly loss stop.
    Paper P&L counts every recipe trade closed this calendar month, whether or not it was blocked."""
    blocked = set()
    for k, t in enumerate(rec):
        mt = sum(x["pnl"] for x in rec[:k] if x["d_ex"][:7] == t["d"][:7] and not x.get("open"))
        if mt <= -limit: blocked.add(id(t))
    return blocked

def build():
    work = "/tmp/mock_work"; trades = []; summary = []; bars = []; strategies = []
    last_day = None; data_through = ""; all_days = set()
    for nm in ("NSEI", "NSEBANK", "BSESN"):
        tf, fa, sl, rule_names = CFG[nm]
        lastts = combine(nm, work); data_through = max(data_through, lastts)
        m = M(nm, resample(nm, work, tf)); B = m.B; u = m.u; bse = nm == "BSESN"; last_day = m.days[-1]
        c = [b["c"] for b in B]; m.E9 = ema(c, fa); m.E21 = ema(c, sl); m.start = m.S + dt.timedelta(days=60)
        days_all = [d for d in m.days if d >= START_LOG]; all_days.update(days_all)
        T0 = fix_prevlen(m, [t for t in m.gen("hold") if dt.date.fromisoformat(t["d"]) >= m.start])
        R0 = rules_(m, [t for t in T0 if m.year(t["d"]) < 3]); R0 = {k: R0[k] for k in rule_names}
        kept = [t for t in T0 if not any(f(t) for f in R0.values())]
        if nm in STOP_LOSS:
            bl = loss_stop_flags(kept, STOP_LOSS[nm]); kept = [t for t in kept if id(t) not in bl]
        yrs = [x[2] for x in by_year(m, kept)]
        strategies.append(dict(index=NAMES[nm], approach=APPROACH, candle_minutes=tf, fast=fa, slow=sl, rules="; ".join([RULE_WORDS[r] for r in rule_names] + (["monthly loss stop: no new trades for the rest of a month once that month is down Rs %s on paper" % format(STOP_LOSS[nm], ",")] if nm in STOP_LOSS else [])),
            test_profit=r0(sum(yrs)), test_last2=r0(sum(yrs[3:])), test_worst_drop=m.drop(kept)[0], test_trades=len(kept),
            test_win_rate=round(100 * sum(1 for t in kept if t["pnl"] > 0) / len(kept)), test_charges=r0(sum(t["cost"] for t in kept)),
            test_years_positive=sum(1 for y in yrs if y > 0), test_years=len(yrs), year_profits=", ".join(str(r0(y)) for y in yrs),
            start_capital=CAPITAL_START))
        for lab, mode in VAR:
            mode_ = mode or "hold"
            T = fix_prevlen(m, [t for t in m.gen(mode_) if dt.date.fromisoformat(t["d"]) >= m.start])
            rules = None
            if mode is None:
                tr = [t for t in T if m.year(t["d"]) < 3]; R = rules_(m, tr); rules = {k: R[k] for k in rule_names}
            tid = 0
            stopped = set()
            if nm in STOP_LOSS and rules:
                stopped = loss_stop_flags([t for t in T if not any(f(t) for f in rules.values())], STOP_LOSS[nm])
            for t in T:
                ent_d = B[t["ent"]]["d"]; ex_d = B[t["ex"]]["d"]; isopen = bool(t.get("open"))
                why = [k for k, f in rules.items() if f(t)] if rules else []
                if not why and id(t) in stopped: why = ["monthly loss stop"]
                if ent_d < START_LOG: continue  # start flat: only signals from the start day on
                side = "BUY" if t["dir"] == 1 else "SELL"; opp = "sell" if t["dir"] == 1 else "buy"
                p0 = B[t["ent"]]["c"]; p1 = t["p1"]; e_bar = B[t["ent"]]["ts"][11:16]; x_bar = B[t["ex"]]["ts"][11:16]
                e_fill = fill_time(B[t["ent"]]["ts"], tf); x_fill = fill_time(B[t["ex"]]["ts"], tf)
                tid += 1
                full_gross = t["dir"] * (p1 - p0) * u; full_cost = tcost(p0, p1, u, t["dir"], ent_d, ex_d, bse)
                if why:
                    trades.append(dict(day=ent_d, index=NAMES[nm], variant=lab, trade_no=tid, symbol=contract(nm, ent_d), side=side, status="Skipped",
                        entry_time=e_fill, entry_bar=e_bar, entry_price=p0, exit_time="", exit_bar="", exit_price=None, mark_price=None, qty=u,
                        contract_value=r0(p0 * u), capital_used=0, move_pnl=0, brokerage=0, other_charges=0, total_charges=0, net=0,
                        booked_net=0, open_net=0, exit_charges_est=0, would_have_net=r0(full_gross - full_cost), reason="; ".join(RULE_WORDS[w] for w in why),
                        note="Signal skipped by the recipe. Not traded."))
                    continue
                seg_days = [d for d in days_all if ent_d <= d <= ex_d] if not isopen else [d for d in days_all if ent_d <= d]
                for d in seg_days:
                    first = d == ent_d; closes = (d == ex_d) and not isopen
                    start_p = p0 if first else m.dclose[m.days[m.days.index(d) - 1]]
                    end_p = p1 if closes else m.dclose[d]
                    gross = t["dir"] * (end_p - start_p) * u; parts = []
                    if first: parts.append(leg(p0, u, "buy" if t["dir"] == 1 else "sell", d, bse))
                    if closes: parts.append(leg(p1, u, opp, d, bse))
                    ch = lambda k: sum(x[k] for x in parts)
                    brk = ch("brokerage"); tot = ch("total"); oth = tot - brk
                    is_open = not closes; ex_est = leg(end_p, u, opp, d, bse)["total"] if is_open else 0.0
                    if closes and first: status = "Closed"; note = "Opened and closed the same day."
                    elif closes: status = "Closed (carried in)"; note = f"Opened {ent_d[5:]} {e_fill} at {p0:,.2f}. Today's result counts from the previous close. Entry charges were paid on {ent_d[5:]}."
                    elif first: status = "Open (carry forward)"; note = "Not squared off. Marked at today's close. The same trade continues on the next trading day."
                    else: status = "Open (carry forward)"; note = f"Opened {ent_d[5:]}. Still open, marked at today's close."
                    trades.append(dict(day=d, index=NAMES[nm], variant=lab, trade_no=tid, symbol=contract(nm, d), side=side, status=status,
                        entry_time=(e_fill if first else f"{ent_d[5:]} {e_fill}"), entry_bar=(e_bar if first else ""), entry_price=p0,
                        exit_time=(x_fill if closes else ""), exit_bar=(x_bar if closes else ""), exit_price=(p1 if closes else None),
                        mark_price=(None if closes else m.dclose[d]), qty=u, contract_value=r0(p0 * u), capital_used=r0(p0 * u * MARGIN),
                        move_pnl=r0(gross), brokerage=round(brk, 2), other_charges=round(oth, 2), total_charges=round(tot, 2), net=r0(gross - tot),
                        booked_net=(0 if is_open else r0(gross - tot)), open_net=(r0(gross - tot) if is_open else 0), exit_charges_est=r0(ex_est),
                        would_have_net=None, reason="", note=note, stt=round(ch("stt"), 2), exchange=round(ch("exchange"), 2),
                        stamp=round(ch("stamp"), 2), gst=round(ch("gst"), 2), sebi=round(ch("sebi"), 2)))
        for d in m.days[-KEEP_BAR_DAYS:]:
            for i in range(m.dfirst[d], m.dlast[d] + 1):
                bars.append(dict(index=NAMES[nm], day=d, time=B[i]["ts"][11:16], close=B[i]["c"], fast=round(m.E9[i], 2), slow=round(m.E21[i], 2)))
    groups = collections.defaultdict(list)
    for r in trades: groups[(r["day"], r["index"], r["variant"])].append(r)
    cum = collections.defaultdict(int)
    for k in sorted(groups):
        L = groups[k]; act = [r for r in L if r["status"] != "Skipped"]; sk = [r for r in L if r["status"] == "Skipped"]
        cum[(k[1], k[2])] += sum(r["net"] for r in act); opn = [r for r in act if r["status"].startswith("Open")]
        summary.append(dict(day=k[0], index=k[1], variant=k[2], trades_closed=sum(1 for r in act if r["status"].startswith("Closed")),
            trades_open=len(opn), trades_skipped=len(sk), day_move=sum(r["move_pnl"] for r in act), charges=r0(sum(r["total_charges"] for r in act)),
            net=sum(r["net"] for r in act), brokerage=r0(sum(r["brokerage"] for r in act)), other_charges=r0(sum(r["other_charges"] for r in act)),
            booked_net=sum(r["booked_net"] for r in act), open_net=sum(r["open_net"] for r in act), exit_charges_est=sum(r["exit_charges_est"] for r in act),
            capital_used=max([r["capital_used"] for r in act] or [0]), skipped_net_forgone=sum(r["would_have_net"] or 0 for r in sk),
            open_text=(", ".join(f"{r['side']} {r['symbol']} from {r['entry_time']} at {r['entry_price']:,.2f}" for r in opn) or "none"),
            cumulative_net=cum[(k[1], k[2])], balance=CAPITAL_START + cum[(k[1], k[2])]))
    for d in sorted(all_days):
        for ix in ("Nifty 50", "Bank Nifty", "Sensex"):
            for lab, _ in VAR:
                if (d, ix, lab) not in groups:
                    summary.append(dict(day=d, index=ix, variant=lab, trades_closed=0, trades_open=0, trades_skipped=0, day_move=0, charges=0, net=0, brokerage=0,
                        other_charges=0, booked_net=0, open_net=0, exit_charges_est=0, capital_used=0, skipped_net_forgone=0, open_text="none", cumulative_net=cum[(ix, lab)], balance=CAPITAL_START + cum[(ix, lab)]))
    summary.sort(key=lambda r: (r["day"], r["index"], r["variant"]))
    meta = [dict(key="data_through", value=data_through), dict(key="latest_day", value=last_day), dict(key="start_day", value=START_LOG),
            dict(key="margin_assumption", value=f"{int(MARGIN*100)}% of contract value (assumed, check Kite margin calculator)"),
            dict(key="contract_assumption", value="Near month future, rolled about 2 trading days before assumed expiry (assumed, check Kite)"),
            dict(key="lots", value="Nifty 65, Bank Nifty 30, Sensex 20 (1 lot each)"),
            dict(key="stt", value="Securities Transaction Tax 0.05% on the sell side from April 2026 (start date assumed)"),
            dict(key="capital", value="Each index starts with Rs 5,00,000 and trades 1 lot. The balance is the start plus the net result of every trade since the mock began.")]
    os.makedirs(f"{OUTD}/dash", exist_ok=True)
    for nm_, rows in (("trades", trades), ("summary", summary), ("bars", bars), ("meta", meta), ("strategies", strategies)):
        json.dump(rows, open(f"{OUTD}/dash/{nm_}.json", "w"), separators=(",", ":"))
    return trades, summary

if __name__ == "__main__":
    tr, sm = build()
    for s in sm: print(s["index"], s["variant"], "closed", s["trades_closed"], "open", s["trades_open"], "skip", s["trades_skipped"], "booked", s["booked_net"], "open", s["open_net"], "charges", s["charges"], "net", s["net"])
    tot = collections.defaultdict(int)
    for s in sm: tot[s["variant"]] += s["net"]
    print(dict(tot))
