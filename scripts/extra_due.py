#!/usr/bin/env python3
"""Tells the extra-data workflow whether today's work is done.
  python scripts/extra_due.py                      exit 0 if the due day's files are all in (and were fetched after 21:00 India time), else 1
  python scripts/extra_due.py --final-after 22:30  as above, but when it is not complete and the India time is 22:30 or later, print a red
                                                   error line and exit 1 (the last starts of the night raise the alarm)
Complete means extra/index.json shows ok for delivery, announcements, bulk, block and pledge on the due day, and was last saved at or after
21:00 India time that day (the bulk, block and pledge files are snapshots of the latest day, so an earlier save could hold the day before)."""
import json, sys, os, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NEEDED = ("delivery", "announcements", "bulk", "block", "pledge")

def due_day(now):
    n = now - dt.timedelta(hours=6)
    while n.weekday() >= 5: n -= dt.timedelta(days=1)
    return n.date()

def complete(now, idx):
    day = due_day(now).isoformat()
    try:
        saved = dt.datetime.strptime(idx["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).astimezone(IST)
    except Exception:
        return False
    fresh = saved >= dt.datetime.fromisoformat(day + "T21:00:00").replace(tzinfo=IST)
    return fresh and all(idx.get("sets", {}).get(n, {}).get(day, {}).get("ok") for n in NEEDED)

if __name__ == "__main__":
    now = dt.datetime.now(IST)
    try: idx = json.load(open("extra/index.json"))
    except Exception: idx = {}
    ok = complete(now, idx)
    day = due_day(now).isoformat()
    print("extra data for", day, "is complete" if ok else "is not complete yet")
    if not ok and "--final-after" in sys.argv and now.strftime("%H:%M") >= sys.argv[sys.argv.index("--final-after") + 1]:
        print(f"::error title=extra data incomplete::Extra data for {day} is still not complete; check NSE and the extra-data runs")
        sys.exit(1)
    sys.exit(0 if ok else 1)
