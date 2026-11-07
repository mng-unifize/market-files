#!/usr/bin/env python3
"""Exit 0 if the evening results for the trading day that is due are already complete, so a repeat start can stop
at once; exit 1 if the evening job still has work to do. Complete means: data/live/last_run.json is for that day,
was written after 16:00 India time, and has status ok."""
import json, sys, datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

def due_day(now):
    n = now - dt.timedelta(hours=6)          # a run just after midnight still belongs to the day that closed
    while n.weekday() >= 5: n -= dt.timedelta(days=1)
    return n.strftime("%Y-%m-%d")

def complete(now, last_run):
    day = due_day(now)
    return last_run.get("today") == day and last_run.get("status") == "ok" and last_run.get("run_at", "") >= day + " 16:00"

if __name__ == "__main__":
    try: r = json.load(open("data/live/last_run.json"))
    except Exception: print("no last_run.json yet"); sys.exit(1)
    ok = complete(dt.datetime.now(IST), r)
    print("results for", due_day(dt.datetime.now(IST)), "already complete" if ok else "not complete yet")
    sys.exit(0 if ok else 1)
