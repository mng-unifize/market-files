"""Bank Nifty live readiness check. READ ONLY: it places no orders. Run by a scheduled job at 09:10 Monday to Thursday, after the 09:05 watcher start.
Sends ONE phone message (ntfy): READY, or NOT READY with the reasons. Checks: today's Kite token works, the stop file is absent, the live gate file covers today,
the December Bank Nifty future is found, and the free margin covers one lot (Kite's own figure from data/live/margin_per_lot.json plus a 10 percent buffer).
Monday is a dry run: the live gate starts Tuesday, so Monday's message says so. Check by hand any time: python3 2026-11-23-bn_ready_check.py --print"""
import os, sys, json, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import importlib; BN = importlib.import_module("2026-11-22-order_exec_bn")

BUFFER = 1.10

def need_rupees(path="data/live/margin_per_lot.json"):
    try: return float(json.load(open(path))["lots"]["NSEBANK"]["buy_total"])
    except Exception: return 221191.0

def check(today, kite, stop_exists, go_ok_fn, free_margin_fn, future_fn, need):
    """Returns (ready: bool, live_today: bool, lines: list). Pure logic, kite and file lookups are passed in so tests can fake them."""
    bad, info = [], []
    live, why = go_ok_fn(today)
    info.append("live gate today: " + ("yes" if live else "no (%s)" % why))
    if stop_exists: bad.append("STOP file exists, delete data/live/STOP")
    try: kite.profile(); info.append("Kite login: ok")
    except Exception as e: bad.append("Kite login failed (%s)" % type(e).__name__)
    try:
        sym = future_fn(); info.append("contract: %s" % sym)
        if not sym: bad.append("December Bank Nifty future not found")
    except Exception as e: bad.append("contract lookup failed (%s)" % type(e).__name__)
    try:
        free = free_margin_fn(); info.append("free margin Rs %s, need Rs %s plus buffer" % (format(int(free), ","), format(int(need), ",")))
        if free < need: bad.append("free margin Rs %s is below one lot Rs %s" % (format(int(free), ","), format(int(need), ",")))
        elif free < need * BUFFER: info.append("WARNING: margin is under the 10 percent buffer, add funds")
    except Exception as e: bad.append("margin lookup failed (%s)" % type(e).__name__)
    return (not bad), live, info + ["PROBLEM: " + b for b in bad]

def main(argv):
    import watcher as W
    today = dt.date.today().isoformat()
    if not W.load_env(): print("no environment"); return 2
    tok, why = W.load_token(today)
    if not tok:
        msg = "No Kite token for today (%s). Log in now. Live trading cannot start without it." % why
        print(msg)
        if "--print" not in argv: W.send_alert("[LIVE] Bank Nifty NOT READY", msg, "high")
        return 1
    live_today = BN.go_ok(today)[0]; tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat(); next_live = BN.go_ok(tomorrow)[0]; holding = BN.live_state()
    if not (live_today or next_live or holding): print("not a gate day and nothing open: no check needed"); return 0
    kite = W.make_kite(tok)
    def free(): return float(kite.margins("equity")["net"])
    def fut():
        import ticket as T; L, D = W.engine(); f = T.load_futures(kite, L).get("NSEBANK") or []
        nxt = [c for c in f if c["expiry"] > today]
        return nxt[0]["symbol"] if nxt else ""
    only_exit = bool(holding) and not live_today          # a live position is open on a non-gate day: only the exit matters, so margin for a new lot is not checked
    ready, live, lines = check(today, kite, os.path.exists(os.path.join(HERE, "data/live/STOP")), BN.go_ok, free, fut, 0 if only_exit else need_rupees())
    head = ("POSITION OPEN, EXIT WILL BE AUTOMATIC" if ready else "NOT READY") if only_exit else ("READY" if ready and live else ("DRY RUN OK, live starts tomorrow" if ready else "NOT READY"))
    body = "\n".join(lines)
    print(head); print(body)
    if "--print" not in argv: W.send_alert("[LIVE] Bank Nifty " + head, body, "default" if ready else "high")
    return 0 if ready else 1

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
