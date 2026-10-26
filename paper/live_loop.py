#!/usr/bin/env python3
"""Keeps the live paper recorder on a steady 5-minute clock inside ONE GitHub job.
GitHub's own "*/5" schedule is throttled on this repository (about one run an hour), so instead the workflow starts a few
times a day and this script calls paper/live.py at every 5-minute mark until 15:50 India time or --max-minutes, whichever is first.
paper/live.py is unchanged in how it works: stateless, and a late tick catches up. This script only provides the clock,
pushes new ledger rows and the final candles, and logs every tick to data/live/ticks.csv (used for the coverage check).
Env: same as live.py. LOOP_NOW (tests only, "YYYY-MM-DD HH:MM:SS" India time) is not supported; tests monkeypatch."""
import os, sys, time, subprocess, tempfile, datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LIVE = "data/live"; TICKS = f"{LIVE}/ticks.csv"
FIRST, LAST = "09:15", "15:50"
PUSH_PATHS = ["data/live/ledger.csv", "data/live/live_start.txt", "data/live/live_status.json", "data/live/dryrun_last.json", TICKS]

def now(): return dt.datetime.now(IST)

def sleep_until(t):
    while True:
        s = (t - now()).total_seconds()
        if s <= 0: return
        time.sleep(min(s, 30))

def next_mark(t):
    """Next 5-minute mark plus 20 seconds, so the candle that just closed is available at Kite."""
    base = t.replace(second=0, microsecond=0)
    nxt = base + dt.timedelta(minutes=5 - base.minute % 5)
    return nxt + dt.timedelta(seconds=20)

def run(cmd): return subprocess.run(cmd, capture_output=True, text=True)

def push(msg, paths, candles=False):
    run(["git", "add", *[p for p in paths if os.path.exists(p)]])
    if candles and os.path.isdir(f"{LIVE}/candles"): run(["git", "add", f"{LIVE}/candles"])
    if run(["git", "diff", "--cached", "--quiet"]).returncode == 0: return True
    run(["git", "commit", "-m", msg])
    for i in range(3):
        r1 = run(["git", "pull", "--rebase", "origin", os.environ.get("GITHUB_REF_NAME", "main")])
        r2 = run(["git", "push"])
        if r1.returncode == 0 and r2.returncode == 0: return True
        time.sleep(5)
    print("push failed:", r2.stderr[:200]); return False

def tick(t):
    out = tempfile.NamedTemporaryFile("w+", delete=False); out.close()
    env = dict(os.environ, GITHUB_OUTPUT=out.name)
    r = subprocess.run([sys.executable, "paper/live.py"], env=env, capture_output=True, text=True)
    vals = dict(l.strip().split("=", 1) for l in open(out.name) if "=" in l); os.unlink(out.name)
    print(t.strftime("%H:%M:%S"), "rc", r.returncode, vals); print((r.stdout or "")[-400:], (r.stderr or "")[-300:])
    return r.returncode, vals

def log_tick(t, rc):
    os.makedirs(LIVE, exist_ok=True); new = not os.path.exists(TICKS)
    with open(TICKS, "a") as f:
        if new: f.write("ts_ist,rc\n")
        f.write(f"{t.strftime('%Y-%m-%d %H:%M')},{rc}\n")

def main(max_minutes=170):
    start = now(); end = min(start + dt.timedelta(minutes=max_minutes), start.replace(hour=15, minute=50, second=0, microsecond=0))
    if start.weekday() >= 5 or start.strftime("%H:%M") > LAST:
        print("outside market window, nothing to do"); return 0
    if start.strftime("%H:%M") < FIRST:
        sleep_until(start.replace(hour=9, minute=15, second=20, microsecond=0)); start = now()
    t = start
    try:
        while True:
            hhmm = t.strftime("%H:%M")
            if hhmm > LAST or t > end: break
            rc, vals = tick(t); log_tick(t, rc)
            if vals.get("changed") == "true":
                push(f"paper live {t.strftime('%Y-%m-%d-%H%M')}", PUSH_PATHS, candles=(vals.get("final") == "true"))
            if vals.get("final") == "true" and hhmm >= "15:45": break
            sleep_until(next_mark(now())); t = now()
    finally:
        push(f"paper live ticks {now().strftime('%Y-%m-%d-%H%M')}", [TICKS])
    return 0

if __name__ == "__main__":
    mm = int(sys.argv[sys.argv.index("--max-minutes") + 1]) if "--max-minutes" in sys.argv else 170
    sys.exit(main(mm))
