"""Bank Nifty live order runner, one lot. A separate copy of the order flow in order_exec.py: it reuses order_exec.py's checks, price ladder, logging and
messages unchanged, but trades ONLY Bank Nifty and starts from a clean baseline. order_exec.py itself is untouched and keeps rehearsing all three indexes.

Gates (all must hold or nothing is sent): LIVE_ENABLED below is True, AND data/live/GO_LIVE_BANKNIFTY contains today's date, AND the usual safety checks pass
(kill switch, daily loss stop, one lot cap, order cap, price band, spread, market hours).
Baseline: signals before BASELINE are ignored. An EXIT is sent only if this runner holds a live position, so the old paper position is never sold by mistake.
An ENTRY is sent only when this runner is flat. Own log: data/live/orders_bn_live.csv (same columns as orders.csv, includes slippage against the candle close).

Run by watcher.py after each Bank Nifty alert:   order_exec_bn.py --events <event ids>
Run at 15:00 by a scheduled job:                  order_exec_bn.py --backup     (only if no live Bank Nifty order was placed today)
Check state any time:                             order_exec_bn.py --status"""
import os, sys, csv, time, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import order_exec as X, safety as S, ticket as T

LIVE_ENABLED = True                      # set False to switch this runner off completely
CODE = "NSEBANK"; INDEX_NAME = "Bank Nifty"; PREFIX = "BANKNIFTY"
BASELINE = "2026-11-24 09:15"            # signals with a candle start before this are ignored
GO_FILE = "data/live/GO_LIVE_BANKNIFTY"; OUT = "data/live/orders_bn_live.csv"
ALERTS_F = "data/live/bn_live/alerts.csv"; TICKETS_F = "data/live/bn_live/tickets.csv"
BACKUP_WINDOW = ("15:00", "15:12"); LIMIT_PCT = 0.0005; CANDLE_MIN = 5

def go_ok(today, go_file=GO_FILE, enabled=None):
    enabled = LIVE_ENABLED if enabled is None else enabled
    if not enabled: return False, "LIVE_ENABLED is False in order_exec_bn.py"
    try: txt = open(go_file).read()
    except OSError: return False, "%s is missing: no live orders" % go_file
    if today not in txt: return False, "%s is not dated today: no live orders" % go_file
    return True, ""

def _read(path):
    try: return list(csv.DictReader(open(path)))
    except OSError: return []

def filtered_files(alerts_src="data/live/alerts.csv", tickets_src="data/live/tickets.csv", alerts_f=ALERTS_F, tickets_f=TICKETS_F, baseline=BASELINE):
    """Copies of the alert and ticket logs holding only Bank Nifty signals from the baseline on, so the position check starts clean."""
    os.makedirs(os.path.dirname(alerts_f), exist_ok=True)
    ar = [r for r in _read(alerts_src) if r.get("index") == INDEX_NAME and r.get("signal_bar", "") >= baseline]
    keep = {r["event_id"] for r in ar}
    tr = [r for r in _read(tickets_src) if r["event_id"] in keep or (r["event_id"].startswith(CODE + "|") and r["event_id"].endswith("|ROLL") and r.get("logged_at_ist", "") >= baseline)]
    for path, rows_, hdr in ((alerts_f, ar, _header(alerts_src)), (tickets_f, tr, T.HDR_T)):
        with open(path, "w", newline="") as f:
            w = csv.writer(f); w.writerow(hdr)
            for r in rows_: w.writerow([r.get(k, "") for k in hdr])
    return len(ar), len(tr)

def _header(path):
    try: return next(csv.reader(open(path)))
    except (OSError, StopIteration): return ["event_id", "logged_at_ist", "event", "index", "action", "signal_bar", "fill_time", "lot_size", "price", "stop", "reason", "closes_entry", "alert"]

def live_state(out=OUT):
    """What this runner holds: None when flat, else dict(side, contract, qty) from its own filled orders (ENTRY opens, EXIT closes, a roll keeps it open)."""
    st = None
    for r in _read(out):
        if r["status"] not in X.DONE_OK: continue
        if r["kind"] in ("ENTRY", "ROLL-OPEN"): st = dict(side=r["side"], contract=r["contract"], qty=int(r["qty"]))
        elif r["kind"] == "EXIT": st = None
    return st

def _skip(ctx, key, label, tk, why):
    row = {k: "" for k in X.HDR}
    row.update(event_id=key, run_at_ist=ctx.now_fn().strftime("%Y-%m-%d %H:%M:%S"), mode="live", kind=label, index=tk["index"], side=tk["side"], contract=tk["contract"], qty=tk["qty"], status="SKIPPED", block_reasons=why)
    X.write(row, ctx.out)

def make_legs(orig):
    def legs(ctx, eid):
        if eid.split("|")[0] != CODE: return []
        out = []; st = live_state(ctx.out); roll = eid.endswith("|ROLL"); bar = "" if roll else eid.split("|")[1]
        for key, label, tk in orig(ctx, eid):
            if label == "ENTRY":
                why = "before the live baseline %s" % BASELINE if bar < BASELINE else ("a live position is already open" if st else "")
            elif label == "EXIT": why = "" if st else "no live position to close (the position is from before the live baseline)"
            else: why = "" if st else "no live position to roll"
            if why: _skip(ctx, key, label, tk, why)
            else: out.append((key, label, tk))
        return out
    return legs

def position_gate(ctx):
    """Before any order: Kite's Bank Nifty position must equal what this runner holds. Returns '' when it matches."""
    st = live_state(ctx.out); exp = {st["contract"]: (st["qty"] if st["side"] == "BUY" else -st["qty"])} if st else {}
    got = {k: v for k, v in ctx.broker.positions().items() if k.startswith(PREFIX)}
    return "; ".join(S.position_check([dict(tradingsymbol=k, quantity=v) for k, v in got.items()], exp))

def say(ctx, title, body, priority="default"):
    try: ctx.notify("[LIVE] " + title, body, priority)
    except Exception as e: print("phone message failed:", type(e).__name__)

def run_events(ctx, ids, orig_legs=None):
    orig_legs = orig_legs or X.legs_for; X.legs_for = make_legs(orig_legs)
    try:
        bad = position_gate(ctx)
        if bad:
            S.latch_stop("Bank Nifty position mismatch before order: " + bad[:100], ctx.now_fn())
            say(ctx, "STOPPED before order", "Kite and the live log disagree on Bank Nifty: %s. No order sent. Check the Kite app. Resume: delete data/live/STOP." % bad, "high"); return []
        return X.run_batch(ctx, ids)
    finally: X.legs_for = orig_legs

def pick_contract(ctx, today):
    tk = [r for r in _read("data/live/tickets.csv") if r["event_id"].startswith(CODE + "|") and r.get("contract")]
    if tk:
        c = next((f for f in ctx.futs.get(CODE, []) if f["symbol"] == tk[-1]["contract"]), None)
        if c: return c
    fl = sorted((f for f in ctx.futs.get(CODE, []) if f["expiry"] > (today + dt.timedelta(days=3)).isoformat()), key=lambda f: f["expiry"]); return fl[0] if fl else None

def _boundary(now, minutes=CANDLE_MIN, lag_ok=20):
    """(close time of the candle that just ended, wait seconds). If we are within lag_ok seconds after a boundary use it, else wait for the next."""
    m = (now.minute // minutes) * minutes; b = now.replace(minute=m, second=0, microsecond=0)
    if (now - b).total_seconds() <= lag_ok: return b, 0
    nb = b + dt.timedelta(minutes=minutes); return nb, (nb - now).total_seconds() + 3

def _synthetic(ctx, eid, event, side, tk, closes=""):
    now = ctx.now_fn().strftime("%Y-%m-%d %H:%M:%S"); bar = eid.split("|")[1]
    ah = _header("data/live/alerts.csv"); ar = dict(event_id=eid, logged_at_ist=now, event=event, index=INDEX_NAME, action=side, signal_bar=bar, fill_time=now, lot_size=tk["qty"], price=(tk["ask"] if side == "BUY" else tk["bid"]), stop="none", reason="backup test trade", closes_entry=closes, alert="sent")
    for path, hdr, row in ((ctx.alerts, ah, ar), (ctx.tickets, T.HDR_T, dict(tk, event_id=eid, logged_at_ist=now, kind=event))):
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(hdr)
            w.writerow([row.get(k, "") for k in hdr])

def _ticket(ctx, c, side):
    b, a, last = ctx.broker.depth(c); bid = b[0][0] if b else None; ask = a[0][0] if a else None
    if not bid or not ask: return None
    lim = T.tick_round(ask * (1 + LIMIT_PCT), c["tick"], True) if side == "BUY" else T.tick_round(bid * (1 - LIMIT_PCT), c["tick"], False)
    return dict(index=INDEX_NAME, side=side, contract=c["symbol"], expiry=c.get("expiry", ""), lots=1, qty=c["lot"], bid=bid, ask=ask, last=last or "", limit=lim, stop="none", seconds_after_candle=0, quote_status="ok", note="backup test trade")

def run_backup(ctx):
    """15:00 backup: if no live Bank Nifty order was placed today, buy 1 lot just after a 5-minute candle closes and sell just after the next close. Returns a text."""
    now = ctx.now_fn(); day = now.strftime("%Y-%m-%d"); hm = now.strftime("%H:%M")
    if not (BACKUP_WINDOW[0] <= hm <= BACKUP_WINDOW[1]): return "outside the backup window %s to %s: nothing done" % BACKUP_WINDOW
    if any(r["run_at_ist"][:10] == day and r["status"] in X.PLACED for r in _read(ctx.out)): return "a live Bank Nifty order was already placed today: backup not needed"
    if live_state(ctx.out): return "a live Bank Nifty position is open: backup not run"
    bad = position_gate(ctx)
    if bad: S.latch_stop("Bank Nifty position mismatch before backup: " + bad[:100], now); say(ctx, "STOPPED before backup", bad, "high"); return "position mismatch: " + bad
    c = pick_contract(ctx, now.date())
    if not c: return "no Bank Nifty contract found"
    b_end, wait = _boundary(now)
    if wait: ctx.sleep(wait)
    bar = (b_end - dt.timedelta(minutes=CANDLE_MIN)).strftime("%Y-%m-%d %H:%M"); eid_in = "%s|%s|ENTRY" % (CODE, bar)
    tk = _ticket(ctx, c, "BUY")
    if not tk: say(ctx, "Backup not run", "No usable Bank Nifty quote"); return "no quote"
    _synthetic(ctx, eid_in, "ENTRY", "BUY", tk)
    r, ok = X.one_order(ctx, eid_in, "ENTRY", tk, eid_in, False)
    if not ok: return "backup entry did not complete: %s" % r.get("status")
    nb, wait2 = _boundary(ctx.now_fn(), lag_ok=-1)
    ctx.sleep(wait2)
    bar2 = (nb - dt.timedelta(minutes=CANDLE_MIN)).strftime("%Y-%m-%d %H:%M"); eid_out = "%s|%s|EXIT" % (CODE, bar2)
    tk2 = _ticket(ctx, c, "SELL")
    if not tk2:
        S.latch_stop("backup exit: no quote", ctx.now_fn()); say(ctx, "BACKUP EXIT NOT SENT", "No quote. SELL 1 lot %s BY HAND NOW. Automation stopped." % c["symbol"], "high"); return "no quote at exit"
    _synthetic(ctx, eid_out, "EXIT", "SELL", tk2, closes=eid_in)
    r2, ok2 = X.one_order(ctx, eid_out, "EXIT", tk2, eid_out, False)
    return "backup round trip done" if ok2 else "backup exit did not complete: %s (automation stopped, close by hand)" % r2.get("status")

def build_ctx(now_fn=None):
    import watcher as W
    if not W.load_env(): return None, "no environment"
    now = now_fn or (lambda: dt.datetime.now(W.IST).replace(tzinfo=None))
    tok, why = W.load_token(now().strftime("%Y-%m-%d"))
    if not tok: return None, "no token: " + str(why)
    kite = W.make_kite(tok); L, D = W.engine(); futs = T.load_futures(kite, L)
    from live_broker import KiteBroker
    def notify(title, body, priority="default"): return W.send_alert(title, body, priority)
    return X.Ctx(kite, futs, KiteBroker(kite), now, time.time, time.sleep, notify, os.environ.get("NTFY_TOPIC", "").strip(), os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
                 lambda code: D.CFG[code][0], alerts=ALERTS_F, tickets=TICKETS_F, out=OUT), ""

def main(argv):
    today = dt.date.today().isoformat()
    if "--status" in argv:
        ok, why = go_ok(today); print("go-live file today:", "yes" if ok else "no (%s)" % why); print("baseline:", BASELINE); print("live position:", live_state() or "flat")
        print("rows today:", [(r["kind"], r["status"], r["fill_price"], r["slip_pct"]) for r in _read(OUT) if r["run_at_ist"][:10] == today]); return 0
    ok, why = go_ok(today)
    if not ok: print("order_exec_bn: not live today (%s)" % why); return 0
    if "--events" in argv: ids = [i for i in argv[argv.index("--events") + 1:] if i.startswith(CODE + "|")]
    elif "--backup" in argv: ids = []
    else: print(__doc__); return 2
    if "--events" in argv and not ids: return 0
    filtered_files(); ctx, why = build_ctx()
    if ctx is None: print("order_exec_bn:", why); return 2
    try:
        if "--backup" in argv: print(run_backup(ctx))
        else: run_events(ctx, ids)
    except Exception as e:
        print("order_exec_bn failed:", type(e).__name__, str(e)[:200]); say(ctx, "BANK NIFTY ORDER RUN CRASHED", "%s. Check the Kite app and act by hand if needed. Nothing further sent." % type(e).__name__, "high")
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
