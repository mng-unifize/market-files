"""Reconcile the watcher's signals against the IBT Futures dashboard data (data/mock/dash/trades.json).

Read only. Sends nothing unless --notify is given and a mismatch is found. Writes nothing.

  python reconcile.py --day 2026-11-11                      alerts.csv (what the watcher logged) vs the dashboard
  python reconcile.py --replay 2026-10-30 2026-11-02 ...    replay each day with Kite candles (watcher --rehearse-day), then compare
  python reconcile.py --ledger 2026-10-28 2026-11-04        GitHub recorder ledger.csv vs the dashboard (no Kite needed)
  python reconcile.py --today --notify                      for launchd: skips weekends and holidays, waits up to 75 minutes for the dashboard
                                                            data to include today, then checks today's alerts. Notifies on a mismatch, or if
                                                            the dashboard never updated (then today cannot be verified).
  add --notify to send one ntfy message if anything does not match

A match means the same index, event (ENTRY, EXIT or SKIP), side, fill time (HH:MM) and a price within 0.03 percent.
The dashboard data is built after the close from full day candles, so it is an independent reference for the live watcher."""
import os, sys, csv, json, re, io, contextlib, collections
HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "data", "mock", "dash", "trades.json")
SUMMARY = os.path.join(HERE, "data", "mock", "dash", "summary.json")
ALERTS = os.path.join(HERE, "data", "live", "alerts.csv")
LEDGER = os.path.join(HERE, "data", "live", "ledger.csv")
TOL = 0.0003
OPP = {"BUY": "SELL", "SELL": "BUY"}

def hm(s): return str(s).strip()[-8:][:5] if re.search(r"\d\d:\d\d", str(s)) else ""
def num(x):
    try: return float(str(x).replace(",", ""))
    except ValueError: return None

def expected(day, trades):
    """Events the dashboard says happened on `day`: [(index, event, action, HH:MM, price)]."""
    first = {}
    for r in trades:
        n = (r["index"], r.get("trade_no"))        # trade numbers restart for each index
        if r.get("trade_no") is not None and (n not in first or r["day"] < first[n]["day"]): first[n] = r
    out = []
    for r in trades:
        if r["day"] == day and r["status"] == "Skipped":
            out.append((r["index"], "SKIP", r["side"], hm(r["entry_time"]), r["entry_price"]))
    for n, r in first.items():
        if r["day"] == day and r["status"] != "Skipped":
            out.append((r["index"], "ENTRY", r["side"], hm(r["entry_time"]), r["entry_price"]))
    for r in trades:
        if r["day"] == day and str(r["status"]).startswith("Closed") and r.get("exit_time"):
            out.append((r["index"], "EXIT", OPP.get(r["side"], r["side"]), hm(r["exit_time"]), r["exit_price"]))
    return out

def from_alerts(day):
    rows = []
    if os.path.exists(ALERTS):
        with open(ALERTS, newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("signal_bar", ""))[:10] == day or str(r.get("fill_time", ""))[:10] == day:
                    rows.append((r["index"], r["event"], r["action"], hm(r["fill_time"]), num(r["price"])))
    return rows

def from_ledger(day):
    rows = []
    with open(LEDGER, newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("signal_bar", ""))[:10] == day:
                rows.append((r["index"], r["event"], r["side"], hm(r["fill_time"]), num(r["index_price"])))
    return rows

RX = re.compile(r"^\s*\d+\. (?:WOULD ALERT|log only) \| (ENTRY|EXIT|SKIP) (BUY|SELL) (.+?) \| signal bar (.+?) \| fill (.+?) \| lot (.+?) \| price (.+?)\s*$")
def from_replay(day):
    import watcher
    buf = []
    code = watcher.rehearse(day=day, out=buf.append)
    rows = []
    for line in buf:
        m = RX.match(line)
        if m: rows.append((m.group(3), m.group(1), m.group(2), hm(m.group(5)), num(m.group(7))))
    return rows, code, buf

def close(a, b): return a is not None and b is not None and abs(a - b) <= TOL * max(abs(b), 1)

def compare(exp, got):
    """Multiset match. Returns (matched, missing, extra)."""
    got = list(got); matched = []; missing = []
    for e in exp:
        hit = None
        for i, g in enumerate(got):
            if g[:4] == e[:4] and close(g[4], e[4]): hit = i; break
        if hit is None: missing.append(e)
        else: matched.append(e); got.pop(hit)
    return matched, missing, got

def line(e): return "%s %s %s at %s price %s" % (e[1], e[2], e[0], e[3], e[4])

def check_day(day, source, trades, days_known):
    if day not in days_known:
        return None, ["%s: dashboard data has no rows for this day yet (the Futures task builds it about 5:20 PM). git pull and retry." % day]
    exp = expected(day, trades)
    if source == "alerts": got = from_alerts(day)
    elif source == "ledger": got = from_ledger(day)
    else: got = source
    matched, missing, extra = compare(exp, got)
    msgs = ["%s: %d expected, %d matched, %d missing, %d extra" % (day, len(exp), len(matched), len(missing), len(extra))]
    for e in missing: msgs.append("   MISSING (in dashboard, not seen): " + line(e))
    for g in extra: msgs.append("   EXTRA (seen, not in dashboard): " + line(g))
    return (not missing and not extra), msgs

RAW = "https://raw.githubusercontent.com/mng-unifize/market-files/main/data/mock/dash/"
def load_dash(remote):
    """trades.json and summary.json. remote=True reads the newest copy from GitHub (the local folder may be behind), local copy as fallback."""
    out = []
    for name, local in (("trades.json", TRADES), ("summary.json", SUMMARY)):
        data = None
        if remote:
            try:
                import urllib.request, time
                with urllib.request.urlopen(RAW + name + "?t=" + str(int(time.time())), timeout=30) as r: data = json.load(r)
            except Exception: data = None
        if data is None: data = json.load(open(local))
        out.append(data)
    trades, summary = out
    return trades, {r["day"] for r in summary} | {r["day"] for r in trades}

def notify_text(title, body):
    sys.path.insert(0, HERE); import watcher
    if watcher.load_env(("NTFY_TOPIC",)): watcher.ntfy(title, body[:900])

def run_today(notify):
    import datetime as dt, time
    sys.path.insert(0, HERE); import watcher
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    now = dt.datetime.now(IST); day = now.strftime("%Y-%m-%d")
    print("reconcile --today %s, run at %s India time" % (day, now.strftime("%H:%M")))
    if now.weekday() >= 5: print("Weekend, nothing to check."); return 0
    if day in watcher.holidays(): print("Market holiday, nothing to check."); return 0
    deadline = time.time() + 75 * 60
    while True:
        trades, days_known = load_dash(True)
        if day in days_known: break
        if time.time() >= deadline:
            msg = "The dashboard data (data/mock/dash) still has no rows for %s, so today's watcher alerts could not be checked. Look at the IBT-Futures task and the evening-paper workflow." % day
            print(msg)
            if notify: notify_text("Watcher check: cannot verify today", msg)
            return 1
        print("dashboard has no rows for %s yet, waiting 5 minutes" % day); time.sleep(300)
    ok, msgs = check_day(day, "alerts", trades, days_known)
    print("\n".join(msgs))
    if ok is not True and notify: notify_text("Watcher check: mismatch", "\n".join(msgs))
    if ok is True: print("Clean.")
    return 0 if ok is True else 1

def main(argv):
    notify = "--notify" in argv; argv = [a for a in argv if a != "--notify"]
    if argv == ["--today"]: return run_today(notify)
    if not argv or argv[0] not in ("--day", "--replay", "--ledger") or len(argv) < 2:
        print(__doc__); return 2
    trades, days_known = load_dash(False)
    mode, days = argv[0], argv[1:]
    if mode == "--ledger" and len(days) == 2 and days[0] < days[1]:   # a range, weekdays only
        import datetime as dt
        d0 = dt.date.fromisoformat(days[0]); d1 = dt.date.fromisoformat(days[1])
        days = [(d0 + dt.timedelta(n)).isoformat() for n in range((d1 - d0).days + 1) if (d0 + dt.timedelta(n)).weekday() < 5]
    bad = []; total = 0
    for d in days:
        if mode == "--replay":
            rows, code, _ = from_replay(d)
            ok, msgs = check_day(d, rows, trades, days_known)
            if code not in (0, None): msgs.append("   note: replay returned code %s" % code)
        else:
            ok, msgs = check_day(d, "alerts" if mode == "--day" else "ledger", trades, days_known)
        print("\n".join(msgs))
        total += 1
        if ok is not True: bad.append((d, msgs))
    print("\n%d of %d days clean." % (total - len(bad), total))
    if bad and notify:
        sys.path.insert(0, HERE); import watcher
        if watcher.load_env(("NTFY_TOPIC",)):
            watcher.ntfy("Watcher check: mismatch", "\n".join(m for _, ms in bad for m in ms)[:900])
    return 0 if not bad else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
