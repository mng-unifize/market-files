#!/usr/bin/env python3
"""Evening run for the paper (mock) trading ledger. Standard library only.
Run from the repo main folder:  python3 paper/evening.py
1. Rebuilds data/mock/dash/*.json from the candle files (the engine is the single source of truth).
2. Checks the result and writes data/live/last_run.json (status ok, warn or fail) and data/live/ledger_snapshot.json.
Exit code is always 0 unless the engine itself crashes, so the workflow still commits the status file."""
import sys, os, json, csv, glob, datetime as dt, traceback, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LIVE = "data/live"; DASH = "data/mock/dash"
INDEXES = ("NSEI", "NSEBANK", "BSESN")
NAMES = {"NSEI": "Nifty 50", "NSEBANK": "Bank Nifty", "BSESN": "Sensex"}
TOL = 0.0015   # 0.15 percent: allowed gap between Yahoo and Kite candle closes

def rows(p):
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []

def check_data(today, problems, info):
    """Freshness, completeness and Yahoo versus Kite agreement."""
    for nm in INDEXES:
        kite = {r["ts_ist"]: r for r in rows(f"data/kite/{nm}_5m.csv")}
        yah = {r["ts_ist"]: r for r in rows(f"data/intraday/{nm}_5m.csv")}
        allr = dict(yah); allr.update(kite)
        last = max(allr) if allr else ""
        info[nm] = dict(last_candle=last, source_of_last="kite" if last in kite else "yahoo")
        days = sorted({k[:10] for k in allr})
        if not days: problems.append(("fail", f"{NAMES[nm]}: no candle data at all")); continue
        if last[:10] < today: problems.append(("warn", f"{NAMES[nm]}: newest candle is {last}, expected {today} (holiday, or the fetch failed)"))
        # completeness for the newest day: 75 candles from 09:15 to 15:25 (15:30 is a closing print)
        d = last[:10]; n = sum(1 for k in allr if k[:10] == d and k[11:] < "15:30")
        info[nm]["candles_on_last_day"] = n
        if last[:10] == today and n < 74: problems.append(("warn", f"{NAMES[nm]}: only {n} of 75 candles on {d}"))
        # Yahoo versus Kite where both exist
        bad = [k for k in yah if k in kite and abs(float(yah[k]["close"]) / float(kite[k]["close"]) - 1) > TOL]
        both = sum(1 for k in yah if k in kite)
        info[nm]["overlap_candles"] = both; info[nm]["overlap_off_over_0_15pct"] = len(bad)
        if both and len(bad) / both > 0.02: problems.append(("warn", f"{NAMES[nm]}: {len(bad)} of {both} overlapping candles differ over 0.15% between Yahoo and Kite"))

def check_ledger(snap_old, trades, summary, problems, info):
    # 1 reconciliation: summary net per index equals sum of trade-segment net per index
    by_t = {}; by_s = {}
    for t in trades: by_t[t["index"]] = by_t.get(t["index"], 0) + t["net"]
    for s in summary: by_s[s["index"]] = by_s.get(s["index"], 0) + s["net"]
    for ix in by_s:
        if abs(by_t.get(ix, 0) - by_s[ix]) > 2: problems.append(("fail", f"{ix}: trades net {by_t.get(ix,0)} does not match summary net {by_s[ix]}"))
    # 2 every executed trade segment has a price and a side
    for t in trades:
        if t["status"] != "Skipped" and (not t.get("entry_price") or t["side"] not in ("BUY", "SELL")):
            problems.append(("fail", f"incomplete trade row {t['day']} {t['index']} #{t['trade_no']}"))
    # 3 immutability: rows of earlier days must not change versus the last snapshot
    last_day = max((s["day"] for s in summary), default="")
    if snap_old:
        old = snap_old.get("rows", {}); changed = []
        for t in trades:
            if t["day"] >= last_day: continue
            k = f"{t['day']}|{t['index']}|{t['trade_no']}"
            if k in old and old[k] != rowhash(t): changed.append(k)
        missing = [k for k in old if k[:10] < last_day and k not in {f"{t['day']}|{t['index']}|{t['trade_no']}" for t in trades}]
        info["changed_past_rows"] = len(changed); info["missing_past_rows"] = len(missing)
        if changed or missing:
            problems.append(("warn", f"{len(changed)} earlier trade rows changed and {len(missing)} vanished since the last run (first: {(changed + missing)[:3]}). This happens when official Kite candles replace Yahoo candles; review it."))
    return last_day

def rowhash(t):  # would_have_net is a hypothetical for skipped trades and depends on later candles, so it is not part of the fingerprint
    return hashlib.sha1(json.dumps({k: t[k] for k in sorted(t) if k not in ("note", "would_have_net")}, sort_keys=True, default=str).encode()).hexdigest()[:12]

def main():
    os.makedirs(LIVE, exist_ok=True)
    today = dt.datetime.now(IST).strftime("%Y-%m-%d")
    problems = []; info = {}; status_extra = {}
    try:
        snap_old = json.load(open(f"{LIVE}/ledger_snapshot.json")) if os.path.exists(f"{LIVE}/ledger_snapshot.json") else None
    except Exception: snap_old = None
    check_data(today, problems, info)
    try:
        import mock_detail as D
        D.build()
        trades = json.load(open(f"{DASH}/trades.json")); summary = json.load(open(f"{DASH}/summary.json"))
        last_day = check_ledger(snap_old, trades, summary, problems, info)
        rows_now = {f"{t['day']}|{t['index']}|{t['trade_no']}": rowhash(t) for t in trades}
        json.dump(dict(saved=today, rows=rows_now), open(f"{LIVE}/ledger_snapshot.json", "w"), separators=(",", ":"))
        status_extra = dict(latest_day=last_day, trade_rows=len(trades), summary_rows=len(summary),
                            net_by_index={ix: sum(s["net"] for s in summary if s["index"] == ix) for ix in {s["index"] for s in summary}})
        if last_day < today: problems.append(("warn", f"dashboard latest day is {last_day}, today is {today}"))
    except Exception:
        problems.append(("fail", "engine crashed: " + traceback.format_exc().splitlines()[-1]))
        print(traceback.format_exc(), file=sys.stderr)
    status = "fail" if any(p[0] == "fail" for p in problems) else "warn" if problems else "ok"
    out = dict(status=status, run_at=dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M"), today=today, problems=[f"{a}: {b}" for a, b in problems], data=info, **status_extra)
    json.dump(out, open(f"{LIVE}/last_run.json", "w"), indent=1)
    print(json.dumps(out, indent=1))

main()
