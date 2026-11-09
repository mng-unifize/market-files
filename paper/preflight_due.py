#!/usr/bin/env python3
"""Exit 0 if today's preflight already passed, so a repeat start can stop at once; exit 1 if it still has to run.
Passed means: data/live/preflight.json was written today (India time) and has status pass."""
import json, sys, datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

def passed_today(now, res):
    return res.get("status") == "pass" and res.get("run_at", "")[:10] == now.strftime("%Y-%m-%d")

if __name__ == "__main__":
    try: r = json.load(open("data/live/preflight.json"))
    except Exception: print("no preflight result yet"); sys.exit(1)
    ok = passed_today(dt.datetime.now(IST), r)
    print("preflight already passed today" if ok else "preflight has not passed today yet")
    sys.exit(0 if ok else 1)
