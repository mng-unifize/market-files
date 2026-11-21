#!/usr/bin/env python3
"""One real round trip, run by itself: buy 1 Nifty lot, wait about 60 seconds, sell it, and check the account is flat again.

Purpose: prove the live path (login, order, fill, position check, log) with real money before the go-live decision. It is NOT a slippage study:
two orders are one data point.

  python 2026-11-20-live_selftest.py            DRY run: reads real quotes, sends NOTHING, uses a private position book (data/live/selftest_book.json)
  python 2026-11-20-live_selftest.py --live     REAL orders. Needs BOTH IBT_ORDER_MODE=live in the environment AND data/live/GO_LIVE containing today's date
                                                (the same two deliberate acts order_exec.py asks for)
  --again       allow a second live run on the same day
  --hold N      seconds between the buy and the sell (default 60)

It uses the same order flow as order_exec.py (limit order, re-price at 3 s and 10 s, cancel at 20 s, an exit gets one last wide limit order)
and the same safety checks (safety.pre_trade_checks: 1 lot cap, market hours, price band, spread, kill switch). Extra rules for the test:
Nifty only, 1 lot, window 10:00 to 14:30, free margin at least Rs 2.5 lakh, the test contract must be flat, STOP file must be absent.
If anything fails after the buy, the STOP file is latched (nothing else will trade), a HIGH priority phone message says exactly what to do by hand,
and the script exits with code 1. Results go to data/live/selftest.csv (never to orders.csv, so the daily order summary is not disturbed)."""
import os, sys, csv, json, time, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import safety as S, ticket as T, order_exec as X

CODE = "NSEI"; WINDOW = ("10:00", "14:30"); HOLD_S = 60; MIN_FREE = 250000; LIMIT_PCT = 0.0005   # same +/-0.05% first limit as the tickets
OUT = "data/live/selftest.csv"; BOOK = "data/live/selftest_book.json"
HDR = ["run_at_ist", "mode", "leg", "contract", "qty", "bid", "ask", "limit_first", "limit_final", "status", "attempts", "fill_price", "fill_seconds",
       "slip_vs_mid_pct", "slip_points", "order_id", "position_after", "round_trip_gross", "round_trip_charges", "round_trip_net", "result", "note"]

def write(row, path=None):
    path = path or OUT; os.makedirs(os.path.dirname(path) or ".", exist_ok=True); new = not os.path.exists(path)
    with X.locked(path), open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HDR, extrasaction="ignore")
        if new: w.writeheader()
        w.writerow({k: row.get(k, "") for k in HDR})

def ran_live_today(day, path=None):
    return any(r.get("mode") == "live" and r.get("leg") == "SUMMARY" and r.get("run_at_ist", "")[:10] == day for r in X.rows(path or OUT))

def orders_placed_today(day, orders_path=None):
    return sum(1 for r in X.rows(orders_path or X.OUT) if r.get("run_at_ist", "")[:10] == day and r.get("status") in X.PLACED)

def refusals(now, c, bid, ask, last, free, positions, live, again, limit, qty, stop_active, stop_reason, orders_today, ran_today):
    why = []
    hm = now.strftime("%H:%M")
    if not (WINDOW[0] <= hm <= WINDOW[1]): why.append("outside the test window %s to %s (%s)" % (WINDOW[0], WINDOW[1], hm))
    if live and ran_today and not again: why.append("a live test already ran today (use --again to repeat)")
    if positions.get(c["symbol"], 0) != 0: why.append("the account already holds %s of %s: the test needs it flat" % (positions[c["symbol"]], c["symbol"]))
    if free is not None and free < MIN_FREE: why.append("free margin Rs %s is below the Rs %s needed" % (format(round(free), ","), format(MIN_FREE, ",")))
    ok, w = S.pre_trade_checks("ENTRY", "SELFTEST|" + now.strftime("%Y-%m-%d %H:%M"), 1, qty, S.LOTS.get(CODE), limit, bid, ask, last, now, set(), stop_active, stop_reason, None, orders_today)
    return why + ([] if ok else w)

def alarm(notify, mode, title, body, reason, now):
    if mode == "live": S.latch_stop("self-test: " + reason[:120], now)
    try: notify("[%s] %s" % (mode.upper(), title), body, "high")
    except Exception: pass

def run(broker, c, now_fn, clock, sleep, notify, hold_s=HOLD_S, free=None, again=False, out=None, stop_state=None, orders_path=None):
    """Runs the round trip. Returns dict(result=OK|REFUSED|NOT_FILLED|STOPPED, ...). Never raises for an order problem."""
    mode = broker.mode; out = out or OUT; now = now_fn(); day = now.strftime("%Y-%m-%d"); qty = S.LOTS[CODE]; tick = c["tick"]
    base = dict(run_at_ist=now.strftime("%Y-%m-%d %H:%M:%S"), mode=mode, contract=c["symbol"], qty=qty)
    rdy, rwhy = broker.ready()
    if not rdy: write(dict(base, leg="SUMMARY", result="REFUSED", note="broker not ready: " + rwhy), out); return dict(result="REFUSED", why=["broker not ready: " + rwhy])
    b, a, last = broker.depth(c); bid = b[0][0] if b else None; ask = a[0][0] if a else None
    limit = T.tick_round(ask * (1 + LIMIT_PCT), tick, True) if ask else None
    stop_active, stop_reason = stop_state if stop_state is not None else (os.path.exists(S.STOP_FILE), "STOP file present")
    why = refusals(now, c, bid, ask, last, free, broker.positions(), mode == "live", again, limit, qty, stop_active, stop_reason, orders_placed_today(day, orders_path), ran_live_today(day, out))
    if why:
        write(dict(base, leg="SUMMARY", result="REFUSED", note="; ".join(why)[:300]), out); return dict(result="REFUSED", why=why)
    def leg(side, r, bd, ak, lim):
        fp = r["fill_price"]; m = (bd + ak) / 2; sgn = 1 if side == "BUY" else -1
        return dict(base, leg=side, bid=bd, ask=ak, limit_first=lim, limit_final=r["limit_final"], status=r["status"], attempts=r["attempts"], order_id=r["order_id"],
                    fill_price=round(fp, 2) if fp is not None else "", fill_seconds=round(r["fill_s"] or 0, 1),
                    slip_vs_mid_pct=round(sgn * (fp - m) / m * 100, 4) if fp is not None else "", slip_points=round(sgn * (fp - m), 2) if fp is not None else "")
    # ---- leg 1: BUY ----
    r1 = X.execute(broker, c, "BUY", qty, limit, tick, clock, sleep, "selftestbuy", None)
    write(leg("BUY", r1, bid, ask, limit), out)
    if r1["status"] not in X.DONE_OK:
        if r1["status"] == "NOT_FILLED":
            write(dict(base, leg="SUMMARY", result="NOT_FILLED", note="buy not filled, cancelled, no position opened"), out)
            return dict(result="NOT_FILLED")
        alarm(notify, mode, "SELF-TEST BUY PROBLEM (%s)" % r1["status"], "Buy of 1 lot %s ended %s. CHECK THE KITE APP NOW; if any quantity is held, SELL it by hand. STOP is on." % (c["symbol"], r1["status"]), r1["status"], now)
        write(dict(base, leg="SUMMARY", result="STOPPED", note="buy " + r1["status"]), out); return dict(result="STOPPED", why=["buy " + r1["status"]])
    sleep(hold_s)
    # ---- leg 2: SELL. Holding a position is the worse outcome, so the sell is never blocked by the entry-style checks; it gets the wide fallback order too ----
    b2, a2, last2 = broker.depth(c); bid2 = b2[0][0] if b2 else None; ask2 = a2[0][0] if a2 else None
    if not (bid2 and ask2):
        alarm(notify, mode, "SELF-TEST SELL NOT SENT", "Bought 1 lot %s but there is no quote to sell. SELL IT BY HAND NOW. STOP is on." % c["symbol"], "no quote for the sell", now)
        write(dict(base, leg="SUMMARY", result="STOPPED", note="no quote for sell"), out); return dict(result="STOPPED", why=["no quote for sell"])
    lim2 = T.tick_round(bid2 * (1 - LIMIT_PCT), tick, False)
    r2 = X.execute(broker, c, "SELL", qty, lim2, tick, clock, sleep, "selftestsell", X.FALLBACK_PCT)
    write(leg("SELL", r2, bid2, ask2, lim2), out)
    if r2["status"] not in X.DONE_OK:
        alarm(notify, mode, "SELF-TEST SELL DID NOT FILL (%s)" % r2["status"], "1 lot %s is HELD. SELL it by hand now (about %s). STOP is on." % (c["symbol"], bid2), "sell " + r2["status"], now)
        write(dict(base, leg="SUMMARY", result="STOPPED", note="sell " + r2["status"]), out); return dict(result="STOPPED", why=["sell " + r2["status"]])
    pos = broker.positions().get(c["symbol"], 0)
    gross = (r2["fill_price"] - r1["fill_price"]) * qty; charges = None
    try:
        import mock_trades as MT
        charges = MT.tcost(r1["fill_price"], r2["fill_price"], qty, 1, day, day, False)
    except Exception: pass
    net = gross - charges if charges is not None else ""
    summ = dict(base, leg="SUMMARY", position_after=pos, round_trip_gross=round(gross), round_trip_charges=round(charges) if charges is not None else "", round_trip_net=round(net) if net != "" else "")
    if pos != 0:
        alarm(notify, mode, "SELF-TEST POSITION MISMATCH", "After the round trip the account shows %s of %s (should be 0). CHECK THE KITE APP NOW. STOP is on." % (pos, c["symbol"]), "position %s" % pos, now)
        write(dict(summ, result="STOPPED", note="position not flat"), out); return dict(result="STOPPED", why=["position %s" % pos])
    write(dict(summ, result="OK", note="round trip complete, account flat"), out)
    try: notify("[%s] SELF-TEST OK" % mode.upper(), "Bought %s at %.2f, sold at %.2f. Gross %s, charges %s, net %s. Position is flat." % (c["symbol"], r1["fill_price"], r2["fill_price"], round(gross), round(charges) if charges is not None else "?", round(net) if net != "" else "?"), "default")
    except Exception: pass
    return dict(result="OK", gross=gross, charges=charges, net=net, buy=r1["fill_price"], sell=r2["fill_price"])

def pick_contract(futs, tickets_path="data/live/tickets.csv"):
    """The Nifty contract the watcher is using now (latest ticket), else the first listed."""
    lst = futs.get(CODE, [])
    last = [r for r in X.rows(tickets_path) if r.get("index") == "Nifty 50"]
    if last:
        c = next((f for f in lst if f["symbol"] == last[-1]["contract"]), None)
        if c: return c
    return lst[0] if lst else None

def main(argv):
    live = "--live" in argv; again = "--again" in argv; hold = HOLD_S
    if "--hold" in argv: hold = int(argv[argv.index("--hold") + 1])
    import watcher as W
    if not W.load_env(): return 2
    now_fn = lambda: dt.datetime.now(W.IST).replace(tzinfo=None); today = now_fn().strftime("%Y-%m-%d")
    tok, why = W.load_token(today)
    if not tok: print("no token:", why); return 2
    kite = W.make_kite(tok); L, D = W.engine(); futs = T.load_futures(kite, L)
    mode, note = X.order_mode(today=today)
    if live and mode != "live": print("REFUSED: --live needs IBT_ORDER_MODE=live AND data/live/GO_LIVE containing", today, "(%s)" % (note or "IBT_ORDER_MODE is not live")); return 2
    c = pick_contract(futs)
    if not c: print("no Nifty futures contract found"); return 2
    if live:
        from live_broker import KiteBroker; broker = KiteBroker(kite)
    else:
        broker = X.ShadowBroker(kite, futs, book=BOOK, seed={})
    try: free = float(kite.margins("equity")["available"]["live_balance"])
    except Exception: free = None
    def notify(title, body, priority="default"): return W.send_alert(title, body, priority)
    print("%s test on %s (%s)" % ("LIVE" if live else "DRY", c["symbol"], "free margin %s" % (round(free) if free is not None else "unknown")))
    res = run(broker, c, now_fn, time.time, time.sleep, notify, hold, free, again)
    print(json.dumps(res, default=str))
    return 0 if res["result"] in ("OK", "NOT_FILLED") else 1

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
