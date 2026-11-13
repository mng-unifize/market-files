#!/usr/bin/env python3
"""Order executor. ONE code path for the rehearsal and for live trading. The only difference is the broker object it talks to:
  shadow (default)  ShadowBroker, in this file: nothing is sent to the exchange. It watches the live futures quote and pretends the order was placed,
                    filled when the market would have filled a limit order, and keeps its own position book.
  live              KiteBroker, in live_broker.py: places, re-prices and cancels real orders. It is used only when BOTH the environment variable IBT_ORDER_MODE=live
                    is set AND the file data/live/GO_LIVE contains today's date. Either missing means rehearsal, so a live run always needs two deliberate acts, dated for today.
Everything else is identical in both modes: the safety checks, the daily order cap, the order flow (place, re-price at 3 s and 10 s, cancel at 20 s, exit fallback), the position
check after every fill, the automatic stop, the phone messages and the log.

The watcher starts it in the background after each alert (and after each roll alert), one process per index, so it can never delay or break the alerts.
For each signal (EXIT first, then ENTRY, in order; a roll is a close leg then an open leg) it:
  1. checks safety.py: kill switch, daily loss stop (futures prices), 1 lot cap, duplicate guard, order cap per day, market hours, price band, spread, broker ready;
  2. places the limit order, watches it, re-prices at 3 s (+0.1%) and 10 s (+0.2%), cancels after 20 s; an EXIT then gets one last wide limit order (touch +/- 0.5%, 5 s);
  3. measures the cost against the futures candle close of the signal bar;
  4. after a fill compares the broker's positions with what the signals say we hold;
  5. any failure (not filled, partly filled, rejected, position mismatch, order cap, duplicate) latches the STOP file and sends a HIGH priority phone message with the exact thing to do by hand;
  6. sends one phone message per order, and appends one row to data/live/orders.csv.

  python order_exec.py --events EVENT_ID [EVENT_ID ...]     what the watcher runs
  python order_exec.py --report                             summary of data/live/orders.csv
  python order_exec.py --summary [YYYY-MM-DD]               writes data/live/order_summary.json for the daily overview"""
import os, sys, csv, json, time, fcntl, contextlib, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import safety as S, ticket as T

OUT = "data/live/orders.csv"; SUMMARY = "data/live/order_summary.json"; BOOK = "data/live/sim_positions.json"; TICKETS = "data/live/tickets.csv"; ALERTS = "data/live/alerts.csv"
POLL_S = 1.0; STEPS = [(3, 0.001), (10, 0.002)]; GIVEUP_S = 20; FALLBACK_PCT = 0.005; FALLBACK_WAIT_S = 5; ASSUMED_PCT = 0.01
PLACED = ("FILLED", "REPRICED_FILLED", "FALLBACK_FILLED", "NOT_FILLED", "PARTIAL", "REJECTED")
DONE_OK = ("FILLED", "REPRICED_FILLED", "FALLBACK_FILLED")
PREFIX = {"NSEI": "NIFTY", "NSEBANK": "BANKNIFTY", "BSESN": "SENSEX"}
HDR = ["event_id", "run_at_ist", "mode", "kind", "index", "side", "contract", "qty", "status", "block_reasons", "signal_bar", "candle_close_time", "sent_after_close_s", "fill_after_close_s",
       "limit_first", "limit_final", "attempts", "bid_at_send", "ask_at_send", "order_id", "fill_price", "ref_fut_close", "ref_source", "slip_points", "slip_pct", "slip_rupees",
       "slip_vs_mid_pct", "daily_pnl_before", "pnl_basis", "daily_pnl_index", "position_check", "would_be_order", "note"]

def order_mode(env=None, today=None, go_live="data/live/GO_LIVE"):
    """(mode, note). Live needs IBT_ORDER_MODE=live AND a GO_LIVE file that contains today's date."""
    env = os.environ if env is None else env; today = today or dt.date.today().isoformat()
    if str(env.get("IBT_ORDER_MODE", "shadow")).strip().lower() != "live": return "shadow", ""
    try: txt = open(go_live).read()
    except OSError: return "shadow", "IBT_ORDER_MODE is live but data/live/GO_LIVE is missing: staying in rehearsal"
    if today not in txt: return "shadow", "GO_LIVE is not dated today: staying in rehearsal"
    return "live", ""

@contextlib.contextmanager
def locked(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(lf, fcntl.LOCK_UN)

def rows(path):
    if not os.path.exists(path): return []
    with open(path, newline="") as f: return list(csv.DictReader(f))

# ---------- quotes and fills ----------
def depth(kite, c):
    """([(price, qty)] bids, [(price, qty)] asks, last) from one read-only quote call. Empty lists when unusable."""
    key = "%s:%s" % (c["ex"], c["symbol"])
    try:
        q = kite.quote([key])[key]; d = q.get("depth") or {}
        b = [(float(x["price"]), int(x["quantity"])) for x in (d.get("buy") or []) if x.get("price")]
        a = [(float(x["price"]), int(x["quantity"])) for x in (d.get("sell") or []) if x.get("price")]
        return b, a, q.get("last_price")
    except Exception:
        return [], [], None

def walk(levels, qty, limit, side):
    """Volume weighted price for `qty` units from the visible levels within the limit. None when the visible depth is not enough."""
    need = qty; val = 0.0
    for p, q in levels:
        if (side == "BUY" and p > limit) or (side == "SELL" and p < limit): break
        take = min(need, q); val += take * p; need -= take
        if need <= 0: return val / qty
    return None

class Order:
    def __init__(self, oid, c, side, qty, limit, state="OPEN"):
        self.id = oid; self.c = c; self.side = side; self.qty = qty; self.limit = limit; self.state = state; self.filled = 0; self.avg = None; self.reason = ""

# ---------- the rehearsal broker ----------
class ShadowBroker:
    mode = "shadow"
    def __init__(self, kite, futs, book=BOOK, seed=None):
        self.kite = kite; self.futs = futs; self.n = 0; self.book = book; self.seed = seed or {}
    def ready(self):
        for fl in self.futs.values():
            for c in fl[:1]:
                b, a, _ = depth(self.kite, c)
                return (True, "") if (b and a) else (False, "no futures quote from Kite")
        return False, "no futures contract list from Kite"
    def depth(self, c): return depth(self.kite, c)
    def place(self, c, side, qty, limit, tag):
        self.n += 1; return Order("SIM-%d-%d" % (os.getpid(), self.n), c, side, qty, limit)
    def poll(self, o):
        if o.state != "OPEN": return o
        b, a, _ = depth(self.kite, o.c); lv = a if o.side == "BUY" else b
        px = walk(lv, o.qty, o.limit, o.side) if lv else None
        if px is not None: o.state = "COMPLETE"; o.filled = o.qty; o.avg = px; self._apply(o)
        return o
    def modify(self, o, limit): o.limit = limit
    def cancel(self, o):
        if o.state == "OPEN": o.state = "CANCELLED"
    def positions(self):
        with locked(self.book):
            if not os.path.exists(self.book):
                with open(self.book, "w") as f: json.dump(self.seed, f)
            return {k: int(v) for k, v in json.load(open(self.book)).items()}
    def _apply(self, o):
        with locked(self.book):
            bk = json.load(open(self.book)) if os.path.exists(self.book) else dict(self.seed)
            bk[o.c["symbol"]] = int(bk.get(o.c["symbol"], 0)) + (o.qty if o.side == "BUY" else -o.qty)
            bk = {k: v for k, v in bk.items() if v}
            with open(self.book, "w") as f: json.dump(bk, f)

# ---------- the order flow, identical in both modes ----------
def execute(broker, c, side, qty, limit, tick, clock, sleep, tag, fallback_pct=None):
    """Place, watch, re-price up the ladder (STEPS), cancel after GIVEUP_S. For an EXIT (fallback_pct set) a last wide limit order follows the cancel. Returns a result dict."""
    o = broker.place(c, side, qty, limit, tag); t0 = clock(); st = dict(lim0=limit, attempts=1, ids=[o.id], fb=False)
    try: return _flow(broker, c, side, qty, tick, clock, sleep, o, t0, st, tag, fallback_pct)
    except BaseException:
        for x in [o] + st.get("extra", []):
            try: broker.cancel(x)          # a crash must never leave an order sitting at the exchange
            except Exception: pass
        raise

def _px(side, a, b, pct, tick):
    return T.tick_round(a[0][0] * (1 + pct), tick, True) if side == "BUY" else T.tick_round(b[0][0] * (1 - pct), tick, False)

def _settle(broker, o, sleep):
    """After a cancel, wait up to 3 s for the exchange to confirm, so a fill that raced the cancel is never missed."""
    for _ in range(3):
        broker.poll(o)
        if o.state != "OPEN": break
        sleep(1)
    return o

def _flow(broker, c, side, qty, tick, clock, sleep, o, t0, st, tag, fb_pct):
    steps = list(STEPS)
    def res(status, fill=None, s=None, o_=None):
        o_ = o_ or o
        return dict(status=status, fill_price=fill, fill_s=s, limit_first=st["lim0"], limit_final=o_.limit, attempts=st["attempts"], order_id="|".join(str(i) for i in st["ids"]), reason=o_.reason, filled=o.filled, qty=qty, fallback=st["fb"])
    def ok(): return "REPRICED_FILLED" if st["attempts"] > 1 else "FILLED"
    while True:
        el = clock() - t0
        if o.state == "REJECTED": return res("REJECTED")
        broker.poll(o)
        if o.state == "COMPLETE": return res(ok(), o.avg, el)
        if o.state == "REJECTED": return res("REJECTED")
        if o.state == "CANCELLED": return res("PARTIAL" if o.filled else "NOT_FILLED")
        if el >= GIVEUP_S:
            broker.cancel(o); _settle(broker, o, sleep)
            if o.state == "COMPLETE": return res(ok(), o.avg, clock() - t0)
            if o.state == "OPEN": return res("PARTIAL" if o.filled else "NOT_FILLED")      # cancel not confirmed: never send a second order on top of it
            if fb_pct is None: return res("PARTIAL" if o.filled else "NOT_FILLED")
            return _fallback(broker, c, side, qty, tick, clock, sleep, o, t0, st, tag, fb_pct, res)
        if steps and el >= steps[0][0]:
            pct = steps.pop(0)[1]; b, a, _ = broker.depth(c)
            if b and a: broker.modify(o, _px(side, a, b, pct, tick)); st["attempts"] += 1
        sleep(POLL_S)

def _fallback(broker, c, side, qty, tick, clock, sleep, o, t0, st, tag, pct, res):
    """EXIT only. The ladder failed and the first order is confirmed cancelled. Send the rest as one wide limit order (the touch plus or minus pct), which behaves like a
    market order with a price cap. Never used after a rejection or an unconfirmed cancel."""
    left = qty - o.filled; b, a, _ = broker.depth(c)
    if not (b and a): return res("PARTIAL" if o.filled else "NOT_FILLED")
    o2 = broker.place(c, side, left, _px(side, a, b, pct, tick), tag + "F"); st["extra"] = [o2]; st["ids"].append(o2.id); st["attempts"] += 1; t1 = clock()
    while o2.state == "OPEN" and clock() - t1 < FALLBACK_WAIT_S:
        broker.poll(o2)
        if o2.state == "OPEN": sleep(POLL_S)
    if o2.state == "OPEN": broker.cancel(o2); _settle(broker, o2, sleep)
    got = o.filled + (o2.filled if o2.state in ("COMPLETE", "CANCELLED", "OPEN") else 0)
    if o2.state == "COMPLETE": got = qty
    px = [(o.filled, o.avg)] if o.filled and o.avg else []
    if o2.filled and o2.avg: px.append((o2.filled if o2.state != "COMPLETE" else left, o2.avg))
    avg = sum(n * p for n, p in px) / sum(n for n, _ in px) if px else None
    st["fb"] = True; o.filled = got
    if got >= qty: return res("FALLBACK_FILLED", avg, clock() - t0, o2)
    r = res("PARTIAL" if got else ("REJECTED" if o2.state == "REJECTED" else "NOT_FILLED"), avg, None, o2); return r

def would_be_order(c, side, qty, limit, key):
    tag = ("ibt" + "".join(ch for ch in key if ch.isalnum())[-14:])[:20]
    return json.dumps(dict(variety="regular", exchange=c["ex"], tradingsymbol=c["symbol"], transaction_type=side, quantity=qty, product="NRML", order_type="LIMIT", price=limit, validity="DAY", tag=tag))

def prev_close_fn(kite, futs):
    by = {f["symbol"]: f for fl in futs.values() for f in fl}; cache = {}
    def f(sym, day):
        if (sym, day) in cache: return cache[(sym, day)]
        v = None
        try:
            c = by[sym]; d = dt.date.fromisoformat(day)
            rs = [r for r in kite.historical_data(c["token"], d - dt.timedelta(days=10), d, "day") if (r["date"].date() if hasattr(r["date"], "date") else r["date"]) < d]
            v = float(rs[-1]["close"]) if rs else None
        except Exception: v = None
        cache[(sym, day)] = v; return v
    return f

def last_fn(kite, futs):
    by = {f["symbol"]: f for fl in futs.values() for f in fl}
    def f(sym):
        try: c = by[sym]; key = "%s:%s" % (c["ex"], c["symbol"]); return float(kite.quote([key])[key]["last_price"])
        except Exception: return None
    return f

def ref_close(kite, c, bar, tf):
    try:
        start = dt.datetime.strptime(bar, "%Y-%m-%d %H:%M"); end = start + dt.timedelta(minutes=tf)
        for r in kite.historical_data(c["token"], start, end, "%dminute" % tf):
            if r["date"].replace(tzinfo=None) == start: return float(r["close"])
    except Exception: return None
    return None

def write(row, out):
    with locked(out):
        first = not os.path.exists(out)
        with open(out, "a", newline="") as f:
            w = csv.writer(f)
            if first: w.writerow(HDR)
            w.writerow([row.get(k, "") for k in HDR])

class Ctx:
    """Everything one run needs, so tests can swap the broker, the clock and the phone."""
    def __init__(self, kite, futs, broker, now_fn, clock, sleep, notify, topic, base, tf_of, alerts=ALERTS, tickets=TICKETS, out=OUT):
        self.__dict__.update(locals()); del self.__dict__["self"]

def legs_for(ctx, eid):
    """List of (key, label, ticket row) for one event id: one leg for ENTRY or EXIT, close then open for a roll."""
    rs = [r for r in rows(ctx.tickets) if r["event_id"] == eid]
    if eid.endswith("|ROLL"):
        return [(eid + "|CLOSE", "ROLL-CLOSE", rs[0]), (eid + "|OPEN", "ROLL-OPEN", rs[1])] if len(rs) >= 2 else []
    return [(eid, rs[0]["kind"], rs[0])] if rs and rs[0]["kind"] in ("ENTRY", "EXIT") else []

def say(ctx, mode, title, body, priority="default"):
    try: ctx.notify("[%s] %s" % ("LIVE" if mode == "live" else "REHEARSAL", title), body, priority)
    except Exception as e: print("phone message failed:", type(e).__name__)

def one_order(ctx, key, label, tk, eid, batch_failed):
    now = ctx.now_fn(); mode = ctx.broker.mode; code = eid.split("|")[0]
    kind = "ENTRY" if label in ("ENTRY", "ROLL-OPEN") else "EXIT"; side = tk["side"]; qty = int(tk["qty"]); lots = int(tk["lots"])
    c = next((f for f in ctx.futs.get(code, []) if f["symbol"] == tk["contract"]), None)
    row = {k: "" for k in HDR}; day = now.strftime("%Y-%m-%d")
    roll = label.startswith("ROLL"); bar = None if roll else eid.split("|")[1]; tf = ctx.tf_of(code)
    close_t = (dt.datetime.strptime(bar, "%Y-%m-%d %H:%M") + dt.timedelta(minutes=tf)) if bar else None
    row.update(event_id=key, run_at_ist=now.strftime("%Y-%m-%d %H:%M:%S"), mode=mode, kind=label, index=tk["index"], side=side, contract=tk["contract"], qty=qty, signal_bar=bar or "",
               candle_close_time=close_t.strftime("%H:%M") if close_t else "")
    done = {r["event_id"] for r in rows(ctx.out)}; today_rows = [r for r in rows(ctx.out) if r["run_at_ist"][:10] == day]
    placed_today = sum(1 for r in today_rows if r["status"] in PLACED)
    stop, why_stop, warn = S.kill_switch(ctx.topic, ctx.base, now=now)
    pnl_idx, _ = S.daily_pnl(day)
    pnl, _, basis = S.daily_pnl_futures(day, tickets=ctx.tickets, alerts=ctx.alerts, shadow=ctx.out, prev_close_fn=prev_close_fn(ctx.kite, ctx.futs), last_fn=last_fn(ctx.kite, ctx.futs))
    kinds = set(basis.values()); row.update(daily_pnl_before=round(pnl), daily_pnl_index=round(pnl_idx), pnl_basis=("futures" if kinds <= {"futures"} else ("index" if kinds == {"index"} else "mixed")))
    b, a, last = ctx.broker.depth(c) if c else ([], [], None); bid = b[0][0] if b else None; ask = a[0][0] if a else None
    ok, why = S.pre_trade_checks(kind, key, lots, qty, S.LOTS.get(code), float(tk["limit"]) if tk["limit"] else None, bid, ask, last, now, done, stop, why_stop, pnl, placed_today)
    if not c: ok = False; why = why + ["contract not found at Kite"]
    rdy, rwhy = ctx.broker.ready()
    if not rdy: ok = False; why = why + ["broker not ready: " + rwhy]
    if batch_failed: ok = False; why = why + ["an earlier order for this signal did not complete, so this one is not sent"]
    if not ok:
        row.update(status="BLOCKED", block_reasons="; ".join(why)); write(row, ctx.out)
        serious = kind == "EXIT" or any(x.startswith(("duplicate", "order cap")) for x in why)
        if any(x.startswith(("duplicate", "order cap")) for x in why): S.latch_stop("; ".join(why)[:150], now)
        hand = "\nDO THIS BY HAND NOW: %s %d lot %s, limit about %s." % (side, lots, tk["contract"], tk["limit"] or "market view") if kind == "EXIT" else ""
        say(ctx, mode, "BLOCKED %s %s" % (tk["index"], label), "No order was sent:\n" + "\n".join(why) + hand, "high" if serious else "default")
        return row, False
    limit = float(tk["limit"]); row.update(limit_first=limit, would_be_order=would_be_order(c, side, qty, limit, key), bid_at_send=bid, ask_at_send=ask,
                                           sent_after_close_s=round((now - close_t).total_seconds()) if close_t else "")
    res = execute(ctx.broker, c, side, qty, limit, c["tick"], ctx.clock, ctx.sleep, key, FALLBACK_PCT if kind == "EXIT" else None)
    row.update(status=res["status"], limit_final=res["limit_final"], attempts=res["attempts"], order_id=res["order_id"])
    if res["fill_price"] is not None:
        fp = res["fill_price"]; d = 1 if side == "BUY" else -1; mid = (bid + ask) / 2 if bid and ask else None
        ref = None if roll else ref_close(ctx.kite, c, bar, tf); src = "futures candle close"
        if ref is None: ref = mid; src = "quote midpoint at send"
        row.update(fill_price=round(fp, 2), ref_fut_close=ref or "", ref_source=src)
        if close_t: row["fill_after_close_s"] = round((now - close_t).total_seconds() + (res["fill_s"] or 0))
        if ref: pts = d * (fp - ref); row.update(slip_points=round(pts, 2), slip_pct=round(pts / ref * 100, 4), slip_rupees=round(pts * qty))
        if mid: row["slip_vs_mid_pct"] = round(d * (fp - mid) / mid * 100, 4)
    problems = []
    if res["status"] in DONE_OK:
        if label == "ROLL-CLOSE": row["position_check"] = "n/a mid-roll"
        else:
            exp = S.expected_positions(ctx.alerts, ctx.tickets, upto=None if roll else eid); pre = PREFIX[code]
            got = {k: v for k, v in ctx.broker.positions().items() if k.startswith(pre)}; exp = {k: v for k, v in exp.items() if k.startswith(pre)}
            mism = S.position_check([dict(tradingsymbol=k, quantity=v) for k, v in got.items()], exp); row["position_check"] = "ok" if not mism else "; ".join(mism)
            if mism: problems.append("position mismatch: " + "; ".join(mism))
    else:
        problems.append("%s: %s %d lot %s was %s%s" % (label, side, lots, tk["contract"], res["status"].replace("_", " ").lower(), (" (" + res["reason"] + ")") if res["reason"] else ""))
    if warn: row["note"] = warn
    write(row, ctx.out)
    if problems:
        S.latch_stop("; ".join(problems)[:150], now)
        hand = "\nAutomation is STOPPED. Check the Kite app and fix by hand. To resume: delete data/live/STOP on the Mac."
        say(ctx, mode, "STOPPED after %s %s" % (tk["index"], label), "\n".join(problems) + hand, "high")
        return row, False
    cost = "cost %.4f%% (Rs %s)" % (float(row["slip_pct"]), format(int(row["slip_rupees"]), ",")) if row["slip_pct"] != "" else "cost not measured"
    if res.get("fallback"): note = "EXIT FALLBACK USED: the normal orders did not fill in 20 s, so the exit went out as one wide limit order. Position is correct. "
    else: note = ""
    say(ctx, mode, "%s %s %s" % (res["status"].replace("_", " "), tk["index"], label), note + "%s %d lot %s at %.2f (limit %.2f, %s), %s%s. Position check: %s." % (
        side, lots, tk["contract"], res["fill_price"], res["limit_final"], "re-priced" if res["attempts"] > 1 else "first limit", cost,
        (", %d s after the candle closed" % row["fill_after_close_s"]) if row["fill_after_close_s"] != "" else "", row["position_check"]), "high" if res.get("fallback") else "default")
    return row, True

def run_batch(ctx, ids):
    """Process the event ids of one index in order. After any failure the rest of the batch is blocked."""
    failed = False; out = []
    for eid in ids:
        for key, label, tk in legs_for(ctx, eid):
            r, ok = one_order(ctx, key, label, tk, eid, failed); out.append(r)
            if not ok: failed = True
    return out

# ---------- reports ----------
def summary_dict(day, path=OUT, alerts=ALERTS, tickets=TICKETS):
    R = [r for r in rows(path) if r["run_at_ist"][:10] == day]
    exp = [r["event_id"] for r in rows(alerts) if r.get("alert") == "sent" and r["event"] in ("ENTRY", "EXIT") and r["signal_bar"][:10] == day]
    for r in rows(tickets):
        if r["event_id"].endswith("|ROLL") and r["logged_at_ist"][:10] == day and r.get("note", "").startswith("close near month"): exp += [r["event_id"] + "|CLOSE", r["event_id"] + "|OPEN"]
    if not R and not exp: return None
    done = [r for r in R if r["status"] in DONE_OK]
    def f(r, k):
        try: return float(r[k])
        except (TypeError, ValueError): return None
    sl = [f(r, "slip_pct") for r in done if f(r, "slip_pct") is not None]; rs = [f(r, "slip_rupees") for r in done if f(r, "slip_rupees") is not None]
    fs = [f(r, "fill_after_close_s") for r in done if f(r, "fill_after_close_s") is not None]; last = R[-1] if R else {}
    stop = open(S.STOP_FILE).read().strip() if os.path.exists(S.STOP_FILE) else ""
    mode = last.get("mode") or "shadow"
    return dict(day=day, generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mode=mode, mode_text="rehearsal, no real orders placed" if mode == "shadow" else "LIVE orders", assumed_slip_pct=ASSUMED_PCT,
                orders=len(R), filled=len(done), repriced=sum(r["status"] == "REPRICED_FILLED" for r in R), fallbacks=sum(r["status"] == "FALLBACK_FILLED" for r in R), blocked=sum(r["status"] == "BLOCKED" for r in R), not_filled=sum(r["status"] == "NOT_FILLED" for r in R),
                partial=sum(r["status"] == "PARTIAL" for r in R), rejected=sum(r["status"] == "REJECTED" for r in R), expected_orders=len(exp), missing_orders=[x for x in exp if x not in {r["event_id"] for r in R}],
                position_mismatches=sum(1 for r in R if r.get("position_check") not in ("", "ok", "n/a mid-roll")), stopped=bool(stop), stop_reason=stop[:200],
                avg_slip_pct=round(sum(sl) / len(sl), 4) if sl else None, worst_slip_pct=round(max(sl), 4) if sl else None, avg_slip_rupees=round(sum(rs) / len(rs)) if rs else None,
                total_slip_rupees=round(sum(rs)) if rs else None, avg_seconds_to_fill=round(sum(fs) / len(fs), 1) if fs else None, slowest_fill_seconds=round(max(fs)) if fs else None,
                daily_pnl_futures=f(last, "daily_pnl_before") if R else None, daily_pnl_index=f(last, "daily_pnl_index") if R else None, pnl_basis=last.get("pnl_basis"),
                loss_stop_tripped=any("daily loss stop" in (r.get("block_reasons") or "") for r in R), kill_switch_seen=any("kill switch" in (r.get("block_reasons") or "") for r in R),
                blocked_reasons=[r["block_reasons"] for r in R if r["status"] == "BLOCKED"],
                orders_detail=[{k: r[k] for k in ("event_id", "index", "kind", "side", "status", "limit_first", "fill_price", "ref_fut_close", "slip_pct", "slip_rupees", "sent_after_close_s", "fill_after_close_s", "position_check")} for r in R])

def write_summary(day, path=OUT, out_path=SUMMARY, alerts=ALERTS, tickets=TICKETS):
    """Writes the JSON for the daily overview. Returns the files to publish (empty when there were no signals and no orders)."""
    d = summary_dict(day, path, alerts, tickets)
    if d is None: return []
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh: json.dump(d, fh, indent=1)
    return [out_path, path]

def summary_text(d):
    tag = "REHEARSAL" if d["mode"] == "shadow" else "LIVE"
    lines = ["%s orders today: %d placed, %d filled, %d blocked, %d not filled or rejected (expected %d)." % (tag, d["orders"], d["filled"], d["blocked"], d["not_filled"] + d["rejected"] + d["partial"], d["expected_orders"])]
    if d["avg_slip_pct"] is not None: lines.append("Average cost %.4f%% a side against the %.2f%% assumed, Rs %s in total, %s s to fill on average." % (d["avg_slip_pct"], d["assumed_slip_pct"], format(d["total_slip_rupees"], ","), d["avg_seconds_to_fill"]))
    if d["daily_pnl_futures"] is not None: lines.append("Day profit or loss on futures prices: Rs %s (stop at Rs 40,000 loss)." % format(round(d["daily_pnl_futures"]), ","))
    if d.get("fallbacks"): lines.append("%d exit(s) needed the fallback order. Check the limit logic." % d["fallbacks"])
    if d["missing_orders"]: lines.append("NO ORDER LOGGED FOR: " + ", ".join(d["missing_orders"]))
    if d["stopped"]: lines.append("Automation is STOPPED: " + d["stop_reason"])
    return "\n".join(lines)

def report(path=OUT, out=print):
    R = rows(path)
    if not R: out("No orders logged yet."); return
    done = [r for r in R if r["status"] in DONE_OK]
    out("Orders: %d logged, %d filled, %d blocked, %d not filled" % (len(R), len(done), sum(r["status"] == "BLOCKED" for r in R), sum(r["status"] == "NOT_FILLED" for r in R)))
    sl = [float(r["slip_pct"]) for r in done if r["slip_pct"] not in ("", None)]
    if sl:
        out("Slippage vs the futures candle close: average %.4f%% a side (model assumes %.2f%%), worst %.4f%%, best %.4f%%" % (sum(sl) / len(sl), ASSUMED_PCT, max(sl), min(sl)))
        rs = [float(r["slip_rupees"]) for r in done if r["slip_rupees"] not in ("", None)]
        out("Rupees per order (1 lot): average %.0f, total %.0f" % (sum(rs) / len(rs), sum(rs)))
    fs = [float(r["fill_after_close_s"]) for r in done if r["fill_after_close_s"] != ""]
    if fs: out("Seconds from candle close to fill: average %.1f, slowest %.0f" % (sum(fs) / len(fs), max(fs)))
    for nm in sorted({r["index"] for r in done}):
        x = [float(r["slip_pct"]) for r in done if r["index"] == nm and r["slip_pct"] != ""]
        if x: out("  %-11s %2d orders, average %.4f%%" % (nm, len(x), sum(x) / len(x)))

def main(argv):
    if "--report" in argv: report(); return 0
    if "--summary" in argv:
        i = argv.index("--summary"); day = argv[i + 1] if len(argv) > i + 1 else dt.date.today().isoformat()
        print(write_summary(day) or "no orders for " + day); return 0
    if "--events" not in argv: print(__doc__); return 2
    ids = argv[argv.index("--events") + 1:]
    import watcher as W
    if not W.load_env(): return 2
    now = lambda: dt.datetime.now(W.IST).replace(tzinfo=None)
    tok, why = W.load_token(now().strftime("%Y-%m-%d"))
    if not tok: print("no token:", why); return 2
    kite = W.make_kite(tok); L, D = W.engine(); futs = T.load_futures(kite, L)
    mode, note = order_mode(today=now().strftime("%Y-%m-%d"))
    if mode == "live":
        from live_broker import KiteBroker
        broker = KiteBroker(kite)
    else:
        broker = ShadowBroker(kite, futs, seed=S.expected_positions(before=ids[0]))
    def notify(title, body, priority="default"): return W.send_alert(title, body, priority)
    if note: notify("Order mode note", note)
    ctx = Ctx(kite, futs, broker, now, time.time, time.sleep, notify, os.environ.get("NTFY_TOPIC", "").strip(), os.environ.get("NTFY_SERVER", "https://ntfy.sh"), lambda code: D.CFG[code][0])
    try: run_batch(ctx, ids)
    except Exception as e:
        print("order run failed:", type(e).__name__, str(e)[:200])
        try: notify("[%s] ORDER RUN CRASHED" % mode.upper(), "%s on %s. Nothing further was sent for this signal. Check the Kite app and act by hand if needed." % (type(e).__name__, ", ".join(ids)), "high")
        except Exception: pass
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
