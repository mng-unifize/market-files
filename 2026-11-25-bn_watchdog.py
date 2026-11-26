"""Bank Nifty live order watchdog. READ ONLY: it places no orders and changes no trading file. A scheduled job runs it every 2 minutes; it does nothing outside 09:15 to 15:40 Monday to Friday or on a market holiday.
It looks at what actually happened to each live Bank Nifty signal and sends a phone message (ntfy, retried, plus a Healthchecks fail ping as a second route) when:
  1. the STOP file exists (automation stopped itself),
  2. a live order has any status other than filled or skipped (rejected, not filled, partial, blocked),
  3. a live signal got no order result within 4 minutes (the runner crashed or never started),
  4. it is past 15:13 on a live day and nothing was filled (the signal trade and the 3 PM backup both failed).
Each problem is sent once. State is kept in data/live/watchdog_state.json. Failed sends are written to data/live/notify_failures.log. Check by hand: python3 2026-11-25-bn_watchdog.py --print"""
import os, sys, csv, json, time, datetime as dt, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import importlib; BN = importlib.import_module("2026-11-22-order_exec_bn"); X = BN.X

STATE = "data/live/watchdog_state.json"; FAIL_LOG = "data/live/notify_failures.log"; STOP = "data/live/STOP"
GRACE_MIN = 4; FIRST, LAST = "09:15", "15:40"; BACKUP_DONE_BY = "15:13"
QUIET = ("SKIPPED",)

ALIVE = "data/live/watcher_alive.txt"; ALIVE_MAX_MIN = 8; ALIVE_FROM, ALIVE_TO = "09:20", "15:35"

def problems(now, gate_today, live_open, stop_text, alerts_rows, out_rows, alive_age_min=None):
    """Pure logic. Returns a list of (key, title, body). now is a datetime; rows are dicts."""
    P = []; today = now.strftime("%Y-%m-%d")
    if stop_text: P.append(("STOP|" + stop_text[:80], "AUTOMATION STOPPED", stop_text[:300] + "\nCheck the Kite app and fix by hand. To resume: delete data/live/STOP on the Mac."))
    if alive_age_min is not None and ALIVE_FROM <= now.strftime("%H:%M") <= ALIVE_TO and alive_age_min > ALIVE_MAX_MIN:
        P.append((today + "|watcher-stalled", "WATCHER STOPPED", "The signal watcher on the Mac has not checked in for %d minutes (limit %d). Signals are NOT being watched, so exits can be missed. Check the Mac, then run: launchctl kickstart -k gui/$(id -u)/com.ibt.watcher" % (alive_age_min, ALIVE_MAX_MIN)))
    byid = {}
    for r in out_rows: byid.setdefault(r.get("event_id", ""), []).append(r)
    if gate_today or live_open:
        for a in alerts_rows:
            if a.get("alert") != "sent" or a.get("event") not in ("ENTRY", "EXIT") or not a.get("logged_at_ist", "").startswith(today): continue
            eid = a["event_id"]; rows = byid.get(eid, [])
            try: logged = dt.datetime.strptime(a["logged_at_ist"][:16], "%Y-%m-%d %H:%M")
            except Exception: continue
            if not rows:
                if (now - logged).total_seconds() > GRACE_MIN * 60:
                    P.append((eid + "|none", "NO ORDER RESULT %s" % a["event"], "The %s %s signal (candle %s, alert logged %s) has no order result after %d minutes, seen at %s. The order runner may have crashed. Check the Kite app now." % (a.get("index", "Bank Nifty"), a["action"], a.get("signal_bar", "")[11:16], a.get("logged_at_ist", "")[11:16], GRACE_MIN, now.strftime("%H:%M"))))
                continue
            for r in rows:
                if r["status"] in X.DONE_OK or r["status"] in QUIET: continue
                why = (r.get("block_reasons") or r.get("note") or "not recorded")[:300]; at = r.get("run_at_ist", "")[11:19] or "unknown time"
                P.append((eid + "|" + r["status"], "ORDER %s" % r["status"].replace("_", " "), "%s %s %s: %s at %s (signal candle %s).\nReason: %s\nCheck the Kite app now." % (r.get("kind", ""), r.get("side", ""), r.get("contract", ""), r["status"], at, a.get("signal_bar", "")[11:16], why)))
        if gate_today and now.strftime("%H:%M") >= BACKUP_DONE_BY:
            filled = [r for r in out_rows if r.get("run_at_ist", "").startswith(today) and r.get("kind") == "ENTRY" and r.get("status") in X.DONE_OK]
            if not filled: P.append((today + "|nofill", "NO LIVE TRADE TODAY", "It is past %s on the live day and no Bank Nifty live order filled. The signal trade and the 3 PM backup both failed or did not run. Check the Kite app." % BACKUP_DONE_BY))
    return P

def _read(path):
    try: return list(csv.DictReader(open(path, newline="")))
    except Exception: return []

def deliver(W, title, body):
    """Phone message with retries, then a Healthchecks fail ping as a second route. Returns True if either route worked."""
    ok = False
    for i in range(3):
        if W.send_alert("[LIVE WATCHDOG] " + title, body, "urgent"): ok = True; break
        time.sleep(2 * (i + 1))
    try:
        hc = os.environ.get("HC_PING_URL", "").strip()
        if hc:
            with urllib.request.urlopen(hc.rstrip("/") + "/fail", data=("%s: %s" % (title, body))[:900].encode(), timeout=10) as r: ok = ok or 200 <= r.status < 300
    except Exception: pass
    if not ok:
        try: open(FAIL_LOG, "a").write("%s %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), title))
        except Exception: pass
    return ok

def main(argv):
    now = dt.datetime.now(); hm = now.strftime("%H:%M")
    if now.weekday() >= 5 or not (FIRST <= hm <= LAST): return 0
    import watcher as W
    if now.strftime("%Y-%m-%d") in W.holidays(): return 0
    if not W.load_env(): print("no environment"); return 2
    today = now.strftime("%Y-%m-%d")
    stop_text = open(STOP).read().strip() if os.path.exists(STOP) else ""
    try: age = (time.time() - os.path.getmtime(ALIVE)) / 60
    except OSError: age = 999.0     # no heartbeat file at all after 09:20 means the watcher is not running
    P = problems(now, BN.go_ok(today)[0], bool(BN.live_state()), stop_text, _read(BN.ALERTS_F), _read(BN.OUT), alive_age_min=round(age))
    try: st = json.load(open(STATE))
    except Exception: st = {}
    sent = set(st.get("sent", [])) if st.get("day") == today else set()
    for key, title, body in P:
        if key in sent: continue
        print(hm, title, "|", body.replace("\n", " "))
        if "--print" in argv: continue
        if deliver(W, title, body): sent.add(key)
    if "--print" not in argv: json.dump(dict(day=today, sent=sorted(sent)), open(STATE, "w"))
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
