#!/usr/bin/env python3
"""Signal watcher for this Mac. Alerts only: it never places, changes or cancels orders, and has no code that could.

Started by launchd at 09:05 India time on weekdays. It runs until the last check at 15:30 and the last heartbeat at 16:00.
  1. Secrets: /Users/ng/ibt/.env (python-dotenv). The day's Kite access token: /Users/ng/ibt/kite_token.json (written by login.py).
  2. Heartbeat: pings HC_PING_URL every 5 minutes from 09:05 to 16:00 on every weekday, market holiday or not. It runs in its own
     thread, so a trading error never stops the pings.
  3. Checks: every 5 minutes (20 seconds after the mark) from 09:20 to 15:30 it fetches closed 5-minute index candles from Kite and runs
     the unchanged engine in paper/live.py (recipe_events). The engine builds the 15-minute candles for Nifty 50 and Sensex itself;
     Bank Nifty uses the 5-minute candles as they are.
  4. Each new ENTRY or EXIT for today sends one ntfy alert and is appended to data/live/alerts.csv. SKIP signals are only logged.
Holidays: dates in holidays.txt skip the trading logic. An unlisted holiday is detected when Kite has no candles for today by 09:45.
Rehearsal: `python watcher.py --rehearse` runs one full check now and prints what it would have alerted. It sends nothing, pings nothing,
and writes nothing into this folder. `--rehearse-day YYYY-MM-DD` replays an earlier weekday the same way and compares the result with the GitHub ledger.
Nothing secret is ever printed or logged."""
import os, sys, csv, json, time, shutil, threading, tempfile, datetime as dt
import urllib.request
import ticket as T

def use_certifi():
    """Point Python's HTTPS (urllib) and requests (used by kiteconnect) at certifi's certificate bundle, unless one is already set.
    Some macOS Python builds have no usable system certificates (CERTIFICATE_VERIFY_FAILED), and launchd starts jobs with a bare environment."""
    try: import certifi
    except ImportError: return False
    path = certifi.where()
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        if not os.environ.get(k): os.environ[k] = path
    return True
use_certifi()

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER_DIR = os.path.join(HERE, "paper")
ENV_PATH = "/Users/ng/ibt/.env"
TOKEN_PATH = "/Users/ng/ibt/kite_token.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LIVE = "data/live"; ALERTS = LIVE + "/alerts.csv"; CANDLES = LIVE + "/candles"; TICKETS = LIVE + "/tickets.csv"
HOLIDAY_FILE = "holidays.txt"
WORK = os.path.join(tempfile.gettempdir(), "ibt_watcher_work")
PING_FIRST, PING_LAST = "09:05", "16:00"
FIRST_TICK, FINAL_TICK = "09:20", "15:30"
HOLIDAY_AFTER = "09:45"
FRESH_MINUTES = 15          # an older signal is logged but not alerted, so you never act on a stale message
REQUIRED = ("KITE_API_KEY", "NTFY_TOPIC", "HC_PING_URL")
STOP_TEXT = "none, exits on opposite crossover"
HDR = ["event_id", "logged_at_ist", "event", "index", "action", "signal_bar", "fill_time", "lot_size", "price", "stop", "reason", "closes_entry", "alert"]
_secrets = []

def scrub(s):
    s = str(s)
    for v in _secrets:
        if v and len(v) >= 6: s = s.replace(v, "***")
    return s

def log(*a):
    print(dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"), scrub(" ".join(str(x) for x in a)), flush=True)

# ---------- clock and schedule ----------
class RealClock:
    def now(self): return dt.datetime.now(IST)
    def sleep_until(self, t):
        while True:
            s = (t - self.now()).total_seconds()
            if s <= 0: return
            time.sleep(min(s, 30))

def at(t, hhmm): return t.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:5]), second=0, microsecond=0)

def next_ping(t):
    """Next heartbeat time strictly after t, or None when the day is done."""
    if t.weekday() >= 5: return None
    if t < at(t, PING_FIRST): return at(t, PING_FIRST)
    nxt = t.replace(second=0, microsecond=0) + dt.timedelta(minutes=5 - t.minute % 5)
    return nxt if nxt <= at(t, PING_LAST) else None

def next_tick(t):
    """Next check time (5-minute mark plus 20 seconds, so the candle that just closed is available), or None."""
    if t.weekday() >= 5: return None
    first = at(t, FIRST_TICK) + dt.timedelta(seconds=20)
    if t < first: return first
    cand = t.replace(minute=t.minute - t.minute % 5, second=0, microsecond=0) + dt.timedelta(seconds=20)
    if cand <= t: cand += dt.timedelta(minutes=5)
    return cand if cand <= at(t, FINAL_TICK) + dt.timedelta(seconds=20) else None

# ---------- outside world ----------
def ping(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r: ok = 200 <= r.status < 300
    except Exception as e:
        log("heartbeat ping failed:", type(e).__name__); return False
    log("heartbeat ping", "ok" if ok else "bad status"); return ok

def ntfy(title, body, priority="high"):
    topic = os.environ["NTFY_TOPIC"].strip()
    base = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    url = topic if topic.startswith("http") else base + "/" + topic
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST", headers={"Title": title, "Priority": priority})
    with urllib.request.urlopen(req, timeout=15) as r: return 200 <= r.status < 300

def send_alert(title, body, priority="high"):
    try: return bool(ntfy(title, body, priority))
    except Exception as e:
        log("ntfy send failed:", type(e).__name__, str(e)[:100]); return False

def make_kite(token):
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=os.environ["KITE_API_KEY"].strip()); k.set_access_token(token); return k

def is_token_error(e): return type(e).__name__ == "TokenException" or "access_token" in str(e).lower() or "TokenException" in str(e)

def heartbeat(url, clock, ping_fn=None):
    ping_fn = ping_fn or ping
    t = clock.now()
    if t.weekday() >= 5 or t > at(t, PING_LAST): return
    if t >= at(t, PING_FIRST): ping_fn(url)          # started late: ping at once
    while True:
        nxt = next_ping(clock.now())
        if nxt is None: return
        clock.sleep_until(nxt); ping_fn(url)

# ---------- engine (paper/live.py, unchanged) ----------
_E = {}
def engine():
    if not _E:
        if PAPER_DIR not in sys.path: sys.path.insert(0, PAPER_DIR)
        import live as L, mock_detail as D
        _E["L"], _E["D"] = L, D
    return _E["L"], _E["D"]

# ---------- files ----------
def holidays():
    out = set()
    if os.path.exists(HOLIDAY_FILE):
        with open(HOLIDAY_FILE) as f:
            for line in f:
                s = line.split("#")[0].strip()
                if s: out.add(s)
    return out

def load_token(today):
    try:
        with open(TOKEN_PATH) as f: d = json.load(f)
    except FileNotFoundError: return None, "token file missing"
    except Exception: return None, "token file unreadable"
    if not d.get("access_token"): return None, "token file empty"
    if d.get("date") != today: return None, "token file is from " + str(d.get("date"))
    return d["access_token"], "ok"

def seen_ids():
    if not os.path.exists(ALERTS): return set()
    with open(ALERTS, newline="") as f: return {r["event_id"] for r in csv.DictReader(f)}

def append_row(row):
    os.makedirs(LIVE, exist_ok=True)
    first = not os.path.exists(ALERTS)
    with open(ALERTS, "a", newline="") as f:
        w = csv.writer(f)
        if first: w.writerow(HDR)
        w.writerow([row[k] for k in HDR])

# ---------- one check ----------
class State:
    def __init__(self): self.token = None; self.kite = None; self.tokens = {}; self.warned = set(); self.failed = set(); self.futs = None; self.quotes = True

def append_ticket(row):
    os.makedirs(LIVE, exist_ok=True); first = not os.path.exists(TICKETS)
    with open(TICKETS, "a", newline="") as f:
        w = csv.writer(f)
        if first: w.writerow(T.HDR_T)
        w.writerow([row[k] for k in T.HDR_T])

def seen_tickets():
    if not os.path.exists(TICKETS): return set()
    with open(TICKETS, newline="") as f: return {r["event_id"] for r in csv.DictReader(f)}

def ticket_for(st, kind, nm, side, c, lag_seconds, eid, t):
    """Builds the order ticket (read-only quote call) and returns (row, lines). Never raises."""
    L, D = engine()
    quote = T.get_quote(st.kite, c) if (c and st.quotes) else (None, None, None, "no contract" if not c else "not requested")
    return T.build(kind, nm, D.NAMES[nm], side, c, T.LOTS_DEFAULT, L.LOTS[nm], quote, STOP_TEXT, lag_seconds, eid, t)

def warn_once(st, key, title, body, notify):
    if key not in st.warned and notify(title, body): st.warned.add(key)

def index_tokens(kite):
    L, _ = engine(); out = {}
    for ex in ("NSE", "BSE"):
        for ins in kite.instruments(ex):
            for nm, (e, sym) in L.IDX.items():
                if e == ex and ins["tradingsymbol"] == sym: out[nm] = ins["instrument_token"]
    return out

def describe(nm, ev, last_entry, t):
    """Builds the alert title, body and csv row for one engine event."""
    L, D = engine()
    tf, fa, sl, rules = D.CFG[nm]; name = D.NAMES[nm]; lot = L.LOTS[nm]
    price = "%.2f" % float(ev["price"]); kind = ev["event"]; side = ev["side"]
    eid = "%s|%s|%s" % (nm, ev["bar"], kind); closes = ""
    if kind == "ENTRY":
        reason = "%d/%d exponential moving average crossover %s on %d-minute candles; no skip rule matched (%s)" % (fa, sl, "up" if side == "BUY" else "down", tf, ", ".join(rules))
    elif kind == "EXIT":
        reason = "opposite %d/%d exponential moving average crossover on %d-minute candles" % (fa, sl, tf)
        if last_entry:
            closes = "%s|%s|ENTRY" % (nm, last_entry["bar"])
    else:
        reason = ev.get("note", "")
    lines = []
    if kind == "EXIT":
        lines.append("EXIT %s %s (paper engine position)" % (side, name))
        lines.append("Closes: %s entry made %s at %s (entry id %s)" % (last_entry["side"], last_entry["bar"], "%.2f" % float(last_entry["price"]), closes) if last_entry else "Closes: the open paper engine position (its entry was not found in the engine output)")
    else:
        lines.append("%s %s %s" % (kind, side, name))
    lines += ["Lot size: %d" % lot, "Price: %s (index, %d-minute bar closing at the signal bar %s)" % (price, tf, ev["bar"]),
              "Stop: " + STOP_TEXT, "Reason: " + reason, "Paper engine signal only. No order was placed."]
    title = "%s %s %s" % (kind, side, name)
    row = dict(event_id=eid, logged_at_ist=t.strftime("%Y-%m-%d %H:%M"), event=kind, index=name, action=side, signal_bar=ev["bar"], fill_time=ev["day"] + " " + ev["fill"],
               lot_size=lot, price=price, stop=STOP_TEXT, reason=reason, closes_entry=closes, alert="")
    return title, "\n".join(lines), row

def run_tick(st, t, make_kite_fn=None, notify_fn=None, fresh_minutes=FRESH_MINUTES, token_day=None):
    make_kite_fn = make_kite_fn or make_kite; notify = notify_fn or send_alert
    L, D = engine()
    today = t.strftime("%Y-%m-%d"); h = t.strftime("%H:%M"); final = h >= FINAL_TICK
    if today in holidays(): log("listed market holiday, trading logic skipped"); return "holiday"
    token, why = load_token(token_day or today)      # a replay of an earlier day still needs today's token
    if not token:
        log("no Kite token for today:", why)
        warn_once(st, "token", "Kite login needed", "No Kite access token for today (%s). Run login.py. No signals are being watched until you do." % why, notify)
        return "no token"
    if token != st.token: st.token = token; st.kite = make_kite_fn(token); st.tokens = {}; st.futs = None
    kite = st.kite; ok_idx = 0; total_today = 0
    try:
        if not st.tokens: st.tokens = index_tokens(kite)
        if st.futs is None: st.futs = T.load_futures(kite, L)
        for nm in L.IDX:
            if nm not in st.tokens: log(nm, "not found at Kite"); continue
            rows = {k: v for k, v in L.candles(kite, st.tokens[nm], t).items() if k[11:] >= "09:15"}
            keep = rows if final else L.trim_incomplete(rows, D.CFG[nm][0])
            path = "%s/%s_5m.csv" % (CANDLES, nm); merged = L.load_rows(path); merged.update(keep); L.save_rows(path, merged)
            ok_idx += 1; total_today += sum(1 for k in rows if k[:10] == today)
    except Exception as e:
        log("candle fetch failed:", type(e).__name__, str(e)[:150])
        if is_token_error(e):
            st.token = None
            warn_once(st, "rejected", "Kite login needed", "Kite rejected today's access token. Run login.py again.", notify)
        return "error"
    if ok_idx < len(L.IDX): log("only %d of %d indexes fetched" % (ok_idx, len(L.IDX)))
    if total_today == 0:
        if h >= HOLIDAY_AFTER and ok_idx == len(L.IDX): log("no candles today, treating as a market holiday, trading logic skipped"); return "holiday"
        log("no candles for today yet"); return "waiting"
    seen = seen_ids(); new_rows = 0; hol = holidays(); futs = st.futs or {}; seen_t = seen_tickets()
    for nm in L.IDX:
        if nm not in st.tokens: continue
        try: events = L.recipe_events(nm, WORK)
        except Exception as e: log(nm, "engine failed:", type(e).__name__, str(e)[:150]); continue
        last_entry = None; pos = None
        for ev in events:
            if ev["event"] == "ENTRY":
                last_entry = ev; pos = None if T.entry_blocked(D, nm, ev["day"], futs, hol) else ev
            elif ev["event"] == "EXIT": pos = None
            if ev["day"] != today: continue          # earlier days are history, never alerted
            title, body, row = describe(nm, ev, last_entry, t)
            if row["event_id"] in seen: continue
            if ev["event"] == "SKIP":
                row["alert"] = "log only"
            elif ev["event"] == "ENTRY" and T.entry_blocked(D, nm, ev["day"], futs, hol):
                row["alert"] = "not sent, expiry week (no new entries in the last %d sessions before expiry; paper still takes it)" % T.BLOCK_SESSIONS
            elif ev["event"] == "EXIT" and last_entry and T.entry_blocked(D, nm, last_entry["day"], futs, hol):
                row["alert"] = "not sent, its entry was blocked (expiry week), so you hold nothing to close"
            else:
                fill = dt.datetime.strptime(ev["day"] + " " + ev["fill"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
                lag = (t - fill).total_seconds() / 60
                if fresh_minutes is not None and lag > fresh_minutes:
                    row["alert"] = "not sent, stale (%d min old)%s" % (lag, "; earlier send attempts failed" if row["event_id"] in st.failed else "")
                else:
                    lag_s = (t - fill).total_seconds()
                    if ev["event"] == "ENTRY": c = T.contract_for(nm, ev["day"], futs, hol, D)
                    else: c = T.held_contract(nm, last_entry["day"], ev["day"], futs, hol, D) if last_entry else None
                    trow, tlines = ticket_for(st, ev["event"], nm, ev["side"], c, lag_s, row["event_id"], t)
                    body = body + "\n" + "\n".join(tlines)
                    if not notify(title, body):
                        st.failed.add(row["event_id"]); log("alert not delivered, will retry next check:", row["event_id"]); continue
                    row["alert"] = "sent"
                    if row["event_id"] not in seen_t: append_ticket(trow); seen_t.add(row["event_id"])
            append_row(row); seen.add(row["event_id"]); new_rows += 1
            log("signal", row["event_id"], "|", row["alert"])
        try: new_rows += roll_check(st, t, nm, pos, futs, hol, seen_t, notify)
        except Exception as e: log(nm, "roll check failed:", type(e).__name__, str(e)[:150])
    return "ok (%d new)" % new_rows

def roll_check(st, t, nm, pos, futs, hol, seen_t, notify):
    """ROLL alert for an open position on the session before expiry (and again on expiry day if still open), from 14:45."""
    L, D = engine(); today = t.strftime("%Y-%m-%d")
    if not pos or t.strftime("%H:%M") < T.ROLL_FROM: return 0
    c = T.contract_for(nm, pos["day"], futs, hol, D)
    if not c or today not in (T.roll_day(c["expiry"], hol), c["expiry"]): return 0
    eid = "%s|%s|ROLL" % (nm, today)
    if eid in seen_t: return 0
    nxt = [f for f in futs.get(nm, []) if f["expiry"] > c["expiry"]]
    if not nxt: log(nm, "roll needed but no next-month contract found"); return 0
    side = pos["side"]; close_side = "SELL" if side == "BUY" else "BUY"; name = D.NAMES[nm]
    r1, l1 = ticket_for(st, "ROLL-CLOSE", nm, close_side, c, None, eid, t)
    r2, l2 = ticket_for(st, "ROLL-OPEN", nm, side, nxt[0], None, eid, t)
    body = ["ROLL %s: your %s position expires %s." % (name, side, c["expiry"]), "Leg 1, close the near month:"] + l1[1:] + ["Leg 2, open the next month (same side):"] + l2[1:] + ["Paper engine signal only. No order was placed."]
    if not notify("ROLL %s" % name, "\n".join(body)): return 0
    r1["kind"] = "ROLL"; r1["note"] = "close near month: %s; open next month: %s" % (c["symbol"], nxt[0]["symbol"]); append_ticket(r1); seen_t.add(eid)
    append_ticket(dict(r2, kind="ROLL", note="open leg"))
    log("roll alert", eid); return 1

def trading_loop(clock, st, tick=None):
    tick = tick or run_tick
    def safe(t):
        try: log("check:", tick(st, t))
        except Exception as e: log("check crashed, continuing:", type(e).__name__, str(e)[:150])
    t = clock.now()
    if t.weekday() >= 5: return
    if t >= at(t, FIRST_TICK) + dt.timedelta(seconds=20) and t <= at(t, PING_LAST): safe(t)   # started late: catch up at once
    while True:
        nxt = next_tick(clock.now())
        if nxt is None: return
        clock.sleep_until(nxt); safe(clock.now())

def ledger_report(day, replayed_ids):
    """Compares a replayed day with the GitHub paper ledger (your local copy of data/live/ledger.csv). Same event ids, so it is an exact comparison."""
    path = os.path.join(HERE, LIVE, "ledger.csv"); led = set()
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                parts = r.get("event_id", "").split("|")
                if len(parts) == 3 and parts[1][:10] == day: led.add(r["event_id"])
    lines = ["Check against the GitHub paper ledger (your local data/live/ledger.csv) for %s:" % day]
    if not led:
        lines.append("  No ledger rows for that day. The recorder may not have been running, or your local copy is older than that day.")
        return lines
    mine = set(replayed_ids); both = mine & led
    lines.append("  %d of %d replayed signals are in the ledger (ledger has %d for that day)" % (len(both), len(mine), len(led)))
    lines.append("  in the ledger but not replayed: " + (", ".join(sorted(led - mine)) or "none"))
    lines.append("  replayed but not in the ledger: " + (", ".join(sorted(mine - led)) or "none"))
    return lines

def rehearse(clock=None, out=print, make_kite_fn=None, day=None):
    """One full check cycle now, with today's real Kite candles and the unchanged signal logic. Prints what would have been alerted.
    Sends nothing to ntfy, makes no heartbeat pings, and writes nothing into this folder: it works in a temporary copy of data/live/candles.
    Signals already alerted by a real run are marked, because rehearsal never reads or writes the real alerts.csv except to look at it."""
    global WORK, ntfy, ping
    clock = clock or RealClock(); t = clock.now(); token_day = None
    if day is not None:                                  # replay of an earlier weekday, as the 15:30 end-of-day check
        try: d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError: out("--rehearse-day needs a date like 2026-11-04"); return 2
        if d.date() >= t.date(): out("--rehearse-day needs a day before today. To rehearse today, run --rehearse."); return 2
        if d.weekday() >= 5: out("%s is a weekend, the market was closed." % day); return 2
        token_day = t.strftime("%Y-%m-%d"); t = d.replace(hour=15, minute=30, second=20)
    if not load_env(("KITE_API_KEY",)): return 2
    real_ids = set(); real_alerts = os.path.join(HERE, ALERTS)
    if os.path.exists(real_alerts):
        with open(real_alerts, newline="") as f: real_ids = {r["event_id"] for r in csv.DictReader(f)}
    def blocked(*a, **k): raise RuntimeError("rehearsal must never send or ping")
    saved = (os.getcwd(), WORK, ntfy, ping); tmp = tempfile.mkdtemp(prefix="ibt_rehearsal_"); sent = []; rows = []; counts = {}
    try:
        os.makedirs(os.path.join(tmp, "data", "live"))
        for d in ("kite", "intraday"):
            src = os.path.join(HERE, "data", d)
            if os.path.isdir(src): os.symlink(src, os.path.join(tmp, "data", d))
        src = os.path.join(HERE, CANDLES)
        if os.path.isdir(src): shutil.copytree(src, os.path.join(tmp, CANDLES))
        if os.path.exists(os.path.join(HERE, HOLIDAY_FILE)): shutil.copy(os.path.join(HERE, HOLIDAY_FILE), tmp)
        os.chdir(tmp); WORK = os.path.join(tmp, "work"); ntfy = ping = blocked
        def capture(title, body): sent.append((title, body)); return True
        st0 = State(); st0.quotes = day is None
        result = run_tick(st0, t, make_kite_fn=make_kite_fn, notify_fn=capture, fresh_minutes=None, token_day=token_day)
        if os.path.exists(ALERTS):
            with open(ALERTS, newline="") as f: rows = list(csv.DictReader(f))
        today = t.strftime("%Y-%m-%d"); L, _ = engine()
        for nm in L.IDX: counts[nm] = sum(1 for k in L.load_rows("%s/%s_5m.csv" % (CANDLES, nm)) if k[:10] == today)
    finally:
        os.chdir(saved[0]); WORK, ntfy, ping = saved[1], saved[2], saved[3]; shutil.rmtree(tmp, ignore_errors=True)
    if day:
        out("REHEARSAL OF A PAST DAY (%s): nothing sent to ntfy, no pings, nothing written to your folder." % day)
        out("Replayed as the 15:30 end-of-day check, with Kite's candles for that day | check result: %s" % result)
    else:
        out("REHEARSAL: nothing sent to ntfy, no pings, nothing written to your folder.")
        out("Run at %s India time | check result: %s" % (t.strftime("%Y-%m-%d %H:%M"), result))
    out("Candles for %s: " % (day or "today") + (", ".join("%s %d" % kv for kv in counts.items()) if counts else "none fetched"))
    out("Stale rule is off here. A live run alerts within %d minutes of each signal, so every ENTRY and EXIT below would have been alerted at the time." % FRESH_MINUTES)
    out("")
    when = "on " + day if day else "today"
    out("Signals %s (%d):" % (when, len(rows)) if rows else "No signals %s." % when)
    for i, r in enumerate(rows, 1):
        verdict = "log only" if (r["event"] == "SKIP" or r["alert"].startswith("not sent")) else "WOULD ALERT"
        out(" %d. %s | %s %s %s | signal bar %s | fill %s | lot %s | price %s" % (i, verdict, r["event"], r["action"], r["index"], r["signal_bar"], r["fill_time"][11:], r["lot_size"], r["price"]))
        out("    stop: %s | reason: %s" % (r["stop"], r["reason"]))
        if r["closes_entry"]: out("    closes: " + r["closes_entry"])
        if r["alert"].startswith("not sent"): out("    note: " + r["alert"])
        if r["event_id"] in real_ids: out("    already sent by a real run (it is in the real alerts.csv)")
    if day:
        out("")
        for line in ledger_report(day, [r["event_id"] for r in rows]): out(line)
    out("")
    out("ntfy messages that would have been sent (%d):" % len(sent))
    for title, body in sent: out("---\n%s\n%s" % (title, body))
    if sent: out("---")
    return 0 if result.startswith(("ok", "holiday", "waiting")) else 1

def load_env(required=REQUIRED):
    if not os.path.isfile(ENV_PATH): print("The secrets file is missing at", ENV_PATH); return False
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing: print("Missing in the secrets file:", ", ".join(missing)); return False
    _secrets[:] = [os.environ.get(k, "").strip() for k in REQUIRED] + [os.environ.get("KITE_API_SECRET", "").strip()]
    return True

def main():
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--rehearse-day" or a.startswith("--rehearse-day="):
            return rehearse(day=a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) else ""))
    if "--rehearse" in args: return rehearse()
    clock = RealClock(); t = clock.now()
    if t.weekday() >= 5: print("Weekend, nothing to do."); return 0
    if t > at(t, PING_LAST): print("After 16:00, nothing to do."); return 0
    os.chdir(HERE)
    if not load_env(): return 2
    hb = threading.Thread(target=heartbeat, args=(os.environ["HC_PING_URL"].strip(), clock), daemon=True); hb.start()
    log("watcher started")
    trading_loop(clock, State())
    hb.join(); log("watcher finished for the day"); return 0

if __name__ == "__main__": sys.exit(main())
