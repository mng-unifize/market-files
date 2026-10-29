#!/usr/bin/env python3
"""Keeps the live paper recorder on a steady 5-minute clock inside ONE GitHub job.
GitHub's own "*/5" schedule is throttled on this repository (about one run an hour), so instead the workflow starts a few
times a day and this script calls paper/live.py at every 5-minute mark until 15:50 India time or --max-minutes, whichever is first.
paper/live.py is unchanged in how it works: stateless, and a late tick catches up. This script only provides the clock,
pushes new ledger rows and the final candles, and logs every tick to its OWN file, data/live/ticks/ticks-<run id>.csv
(used for the coverage check; one file per run, so overlapping runs never write the same file).
Shared files merge safely: before every tick the newest ledger rows are pulled in, so paper/live.py sees every event_id
already recorded; after every merge duplicate event_ids are dropped; .gitattributes marks ledger.csv as append-only
(merge=union) and the rebase prefers this run's copy of the small status files. Ticks are saved at least every 30 minutes. A failed save is retried, never leaves git on a detached HEAD, prints a red
error line in the log, and makes the job fail (so GitHub emails you) if the last save did not land.
Env: same as live.py. LOOP_NOW (tests only, "YYYY-MM-DD HH:MM:SS" India time) is not supported; tests monkeypatch."""
import os, sys, io, csv, time, subprocess, tempfile, datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
RUN_ID = f"{os.environ.get('GITHUB_RUN_ID', 'local')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
LIVE = "data/live"; LEDGER = f"{LIVE}/ledger.csv"; TICKS = f"{LIVE}/ticks/ticks-{RUN_ID}.csv"
FIRST, LAST = "09:15", "15:50"
SAVE_EVERY = dt.timedelta(minutes=30)
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
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

def heal():
    """A rebase that stops on a conflict leaves git on a detached HEAD, and every later push then fails with
    'You are not currently on a branch'. Abort it and get back on the branch (local commits are kept)."""
    if os.path.isdir(".git/rebase-merge") or os.path.isdir(".git/rebase-apply"): run(["git", "rebase", "--abort"])
    settle_stash()
    if run(["git", "symbolic-ref", "-q", "HEAD"]).returncode != 0: run(["git", "checkout", "-B", BRANCH])

def settle_stash():
    """'pull --autostash' can leave conflict markers in a file this run edited (status file, candles) and still report success.
    Keep this run's own copy of such files, unmark them, and drop the stash, so the next pull is not blocked."""
    bad = run(["git", "diff", "--name-only", "--diff-filter=U"]).stdout.split()
    for f in bad: run(["git", "checkout", "--theirs", "--", f]); run(["git", "reset", "-q", "--", f])
    if bad: run(["git", "stash", "drop"])

def merge_ledger(remote_text=""):
    """One ledger row per event_id (first one wins; the remote copy goes first). Returns True when the local file changed."""
    local = open(LEDGER, newline="").read() if os.path.exists(LEDGER) else ""
    seen, rows, head = set(), [], None
    for text in (remote_text, local):
        for r in csv.reader(io.StringIO(text, newline="")):
            if not r: continue
            if r[0] == "event_id": head = head or r; continue
            if r[0] in seen: continue
            seen.add(r[0]); rows.append(r)
    if head is None and not rows: return False
    buf = io.StringIO(newline=""); w = csv.writer(buf)
    if head: w.writerow(head)
    w.writerows(rows)
    if buf.getvalue() == local: return False
    open(LEDGER, "w", newline="").write(buf.getvalue()); return True

def sync_ledger():
    """Before every tick: bring in ledger rows another run already pushed, so live.py's event_id check sees them."""
    if run(["git", "fetch", "origin", BRANCH]).returncode != 0: return
    r = run(["git", "show", f"origin/{BRANCH}:{LEDGER}"])
    if r.returncode == 0: merge_ledger(r.stdout)

def push(msg, paths, candles=False):
    run(["git", "add", *[p for p in paths if os.path.exists(p)]])
    if candles and os.path.isdir(f"{LIVE}/candles"): run(["git", "add", f"{LIVE}/candles"])
    if run(["git", "diff", "--cached", "--quiet"]).returncode == 0: return True
    run(["git", "commit", "-m", msg])
    err = ""
    for i in range(4):
        heal()
        r1 = run(["git", "pull", "--rebase", "--autostash", "-X", "theirs", "origin", BRANCH])
        if r1.returncode != 0:
            err = "pull: " + (r1.stderr or r1.stdout)[-200:]; heal(); time.sleep(5 * (i + 1)); continue
        settle_stash()
        if merge_ledger():   # drop duplicate event_ids that the merge brought in
            run(["git", "add", LEDGER]); run(["git", "commit", "-m", "paper live ledger: drop duplicate event_id rows"])
        r2 = run(["git", "push", "origin", f"HEAD:{BRANCH}"])
        if r2.returncode == 0: return True
        err = "push: " + r2.stderr[-200:]; time.sleep(5 * (i + 1))
    heal()
    print(f"::error title=push failed::{msg}: {err.strip()}", flush=True); return False

def tick(t):
    out = tempfile.NamedTemporaryFile("w+", delete=False); out.close()
    env = dict(os.environ, GITHUB_OUTPUT=out.name)
    r = subprocess.run([sys.executable, "paper/live.py"], env=env, capture_output=True, text=True)
    vals = dict(l.strip().split("=", 1) for l in open(out.name) if "=" in l); os.unlink(out.name)
    print(t.strftime("%H:%M:%S"), "rc", r.returncode, vals); print((r.stdout or "")[-400:], (r.stderr or "")[-300:])
    return r.returncode, vals

def log_tick(t, rc):
    os.makedirs(os.path.dirname(TICKS), exist_ok=True); new = not os.path.exists(TICKS)
    with open(TICKS, "a") as f:
        if new: f.write("ts_ist,rc\n")
        f.write(f"{t.strftime('%Y-%m-%d %H:%M')},{rc}\n")

def main(max_minutes=170):
    start = now(); end = min(start + dt.timedelta(minutes=max_minutes), start.replace(hour=15, minute=50, second=0, microsecond=0))
    if start.weekday() >= 5 or start.strftime("%H:%M") > LAST:
        print("outside market window, nothing to do"); return 0
    if start.strftime("%H:%M") < FIRST:
        sleep_until(start.replace(hour=9, minute=15, second=20, microsecond=0)); start = now()
    t = start; last_save = now(); ok = True
    try:
        while True:
            hhmm = t.strftime("%H:%M")
            if hhmm > LAST or t > end: break
            sync_ledger()
            rc, vals = tick(t); log_tick(t, rc)
            if vals.get("changed") == "true":
                ok = push(f"paper live {t.strftime('%Y-%m-%d-%H%M')}", PUSH_PATHS, candles=(vals.get("final") == "true"))
                if ok: last_save = now()
            elif now() - last_save >= SAVE_EVERY:
                ok = push(f"paper live ticks {now().strftime('%Y-%m-%d-%H%M')}", [TICKS])
                if ok: last_save = now()
            if vals.get("final") == "true" and hhmm >= "15:45": break
            sleep_until(next_mark(now())); t = now()
    finally:
        ok = push(f"paper live ticks {now().strftime('%Y-%m-%d-%H%M')}", [TICKS])
    return 0 if ok else 1

if __name__ == "__main__":
    mm = int(sys.argv[sys.argv.index("--max-minutes") + 1]) if "--max-minutes" in sys.argv else 170
    sys.exit(main(mm))
