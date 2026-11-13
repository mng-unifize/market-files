"""Reconcile the watcher's signals against the IBT Futures dashboard data (data/mock/dash/trades.json).

Sends nothing unless --notify is given and a mismatch is found. --today also writes data/live/signal_check.json and signal_check_log.csv (the three way check
the daily overview shows: dashboard base vs Mac watcher alerts vs GitHub paper ledger, plus whether each alert reached the phone); with --publish it commits and pushes only those two files.

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
SC_JSON = os.path.join(HERE, "data", "live", "signal_check.json")
SC_LOG = os.path.join(HERE, "data", "live", "signal_check_log.csv")
TOL = 0.0003
OPP = {"BUY": "SELL", "SELL": "BUY"}

def hm(s):
    """HH:MM from any time text: 11:15, 11:15:00 or 2026-11-11 11:15."""
    m = re.findall(r"\d\d:\d\d", str(s)); return m[0] if m else ""
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

RAW_LIVE = "https://raw.githubusercontent.com/mng-unifize/market-files/main/data/live/ledger.csv"
def ledger_lines(remote):
    """Lines of ledger.csv. remote=True reads the newest copy from GitHub (the local folder may be behind), local copy as fallback."""
    if remote:
        try:
            import urllib.request, time
            with urllib.request.urlopen(RAW_LIVE + "?t=" + str(int(time.time())), timeout=30) as r: return r.read().decode("utf-8").splitlines()
        except Exception: pass
    with open(LEDGER, newline="") as f: return f.read().splitlines()

def from_ledger(day, remote=False):
    rows = []
    if True:
        for r in csv.DictReader(ledger_lines(remote)):
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
def read_alert_rows(day):
    rows = []
    if os.path.exists(ALERTS):
        with open(ALERTS, newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("signal_bar", ""))[:10] == day or str(r.get("fill_time", ""))[:10] == day: rows.append(r)
    return rows

LOG_HDR = ["day", "expected", "watcher_matched", "watcher_missing", "watcher_extra", "notifications_needed", "notifications_sent", "ledger_matched", "ledger_missing", "ledger_extra", "all_agree"]
def signal_status(day, trades, ledger_remote=True):
    """Three way check for one day. Base = dashboard data (built after the close from full day candles, so the most accurate).
    Compared with it: the Mac watcher's alerts.csv and the GitHub paper recorder's ledger.csv. Also: was each ENTRY and EXIT alert actually sent to the phone."""
    import datetime as dt
    exp = expected(day, trades); A = read_alert_rows(day)
    got_w = [(r["index"], r["event"], r["action"], hm(r["fill_time"]), num(r["price"])) for r in A]
    mw, missw, extraw = compare(exp, got_w)
    try: led = from_ledger(day, ledger_remote); led_ok = True
    except Exception as e: led = []; led_ok = False; led_err = "%s" % type(e).__name__
    ml, missl, extral = compare(exp, led)
    need = [r for r in A if r["event"] in ("ENTRY", "EXIT")]; unsent = [r for r in need if r.get("alert") != "sent"]
    st = dict(day=day, checked_at_ist=dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M"),
              base="dashboard data built after the close from full day candles",
              expected=dict(total=len(exp), entries=sum(1 for e in exp if e[1] == "ENTRY"), exits=sum(1 for e in exp if e[1] == "EXIT"), skips=sum(1 for e in exp if e[1] == "SKIP")),
              watcher=dict(source="Mac watcher alerts (alerts.csv)", matched=len(mw), missing=[line(e) for e in missw], extra=[line(g) for g in extraw],
                           notifications_needed=len(need), notifications_sent=len(need) - len(unsent), not_sent=["%s %s %s: %s" % (r["event"], r["action"], r["index"], r.get("alert")) for r in unsent]),
              github_paper=dict(source="GitHub paper recorder (ledger.csv)", readable=led_ok, matched=len(ml), missing=[line(e) for e in missl], extra=[line(g) for g in extral]))
    st["all_agree"] = bool(led_ok and not missw and not extraw and not unsent and not missl and not extral)
    return st

def write_signal_status(st):
    """Writes data/live/signal_check.json (latest) and upserts one row for the day in data/live/signal_check_log.csv. Returns the two paths."""
    rows = []
    if os.path.exists(SC_LOG):
        with open(SC_LOG, newline="") as f: rows = [r for r in csv.DictReader(f) if r["day"] != st["day"]]
    w, g = st["watcher"], st["github_paper"]
    rows.append(dict(day=st["day"], expected=st["expected"]["total"], watcher_matched=w["matched"], watcher_missing=len(w["missing"]), watcher_extra=len(w["extra"]),
                     notifications_needed=w["notifications_needed"], notifications_sent=w["notifications_sent"], ledger_matched=g["matched"], ledger_missing=len(g["missing"]), ledger_extra=len(g["extra"]), all_agree=st["all_agree"]))
    rows.sort(key=lambda r: r["day"]); os.makedirs(os.path.dirname(SC_LOG), exist_ok=True)
    with open(SC_LOG, "w", newline="") as f:
        wr = csv.DictWriter(f, LOG_HDR); wr.writeheader(); wr.writerows(rows)
    st = dict(st, days_checked=len(rows), days_clean=sum(1 for r in rows if str(r["all_agree"]) == "True"))
    with open(SC_JSON, "w") as f: json.dump(st, f, indent=1)
    return st, [SC_JSON, SC_LOG]

def publish(paths, day):
    """Commits and pushes only the given files, with a fixed author so it never depends on git config. Returns a short result text. Never raises."""
    import subprocess
    env = dict(os.environ, GIT_AUTHOR_NAME="IBT signal check", GIT_AUTHOR_EMAIL="manoj.ng@unifize.com", GIT_COMMITTER_NAME="IBT signal check", GIT_COMMITTER_EMAIL="manoj.ng@unifize.com")
    rel = [os.path.relpath(p, HERE) for p in paths]
    def git(*a):
        try: return subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, env=env, timeout=120)
        except Exception as e: return subprocess.CompletedProcess(a, 1, "", "%s" % type(e).__name__)
    try:
        git("add", "--", *rel)
        if git("diff", "--cached", "--quiet", "--", *rel).returncode == 0: return "nothing new to publish"
        c = git("commit", "-m", "Signal check " + day, "--", *rel)
        if c.returncode != 0: return "commit failed: " + (c.stderr or c.stdout)[:150]
        for attempt in (1, 2):
            git("pull", "--rebase", "--autostash"); p = git("push")
            if p.returncode == 0: return "published"
        return "push failed: " + (p.stderr or p.stdout)[:150]
    except Exception as e: return "publish error: %s" % type(e).__name__

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

def run_today(notify, do_publish=False):
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
    st = signal_status(day, trades); st, paths = write_signal_status(st); problems = []
    w, g = st["watcher"], st["github_paper"]
    print("Signal check: watcher %d of %d matched, %d notifications sent of %d needed; GitHub paper %d matched, %d missing, %d extra; all agree: %s | clean days %d of %d" % (
          w["matched"], st["expected"]["total"], w["notifications_sent"], w["notifications_needed"], g["matched"], len(g["missing"]), len(g["extra"]), st["all_agree"], st["days_clean"], st["days_checked"]))
    if not st["all_agree"]:
        problems = ["Signal check %s: the sources do not agree." % day] + ["watcher missing: " + x for x in w["missing"]] + ["watcher extra: " + x for x in w["extra"]] + ["not sent to phone: " + x for x in w["not_sent"]] + \
                   ["GitHub paper missing: " + x for x in g["missing"]] + ["GitHub paper extra: " + x for x in g["extra"]] + ([] if g["readable"] else ["GitHub paper ledger could not be read"])
        if notify: notify_text("Signal check: mismatch", "\n".join(problems))
    try:                                                       # the order executor's day report rides along; it can never break the signal check
        import order_exec; extra = order_exec.write_summary(day)
        if extra:
            paths = paths + [os.path.join(HERE, p) for p in extra]; print("order report: %s" % extra[0])
            if notify:
                d = order_exec.summary_dict(day); notify_text("Orders today (%s)" % ("rehearsal" if d["mode"] == "shadow" else "live"), order_exec.summary_text(d))
    except Exception as e: print("order report skipped:", type(e).__name__)
    if do_publish:
        res = publish(paths, day); print("publish:", res)
        if res not in ("published", "nothing new to publish") and notify: notify_text("Signal check: could not publish", "The result for %s was saved on the Mac but not pushed to GitHub (%s). The daily overview will show it as not checked. Run git pull --rebase and git push." % (day, res))
    if ok is True and st["all_agree"]: print("Clean.")
    return 0 if (ok is True and st["all_agree"]) else 1

def main(argv):
    notify = "--notify" in argv; pub = "--publish" in argv; argv = [a for a in argv if a not in ("--notify", "--publish")]
    if argv == ["--today"]: return run_today(notify, pub)
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
