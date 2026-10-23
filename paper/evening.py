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

def check_live(trades, problems, info):
    """Compare the live ledger (data/live/ledger.csv) with what the replay engine says should have happened, for days since the live start."""
    path = f"{LIVE}/ledger.csv"
    if not os.path.exists(path): info["live"] = "no live ledger yet (dry run or not started)"; return
    start = open(f"{LIVE}/live_start.txt").read().strip() if os.path.exists(f"{LIVE}/live_start.txt") else "9999"
    led = {}
    for r in csv.DictReader(open(path)):
        if r["signal_bar"][:10] >= start: led[(r["index"], r["event"], r["signal_bar"])] = r
    exp = {}
    for t in trades:
        if t["day"] < start: continue
        d = t["day"]
        if t["status"] == "Skipped": exp[(t["index"], "SKIP", f"{d} {t['entry_bar']}")] = (t["entry_price"], t["side"])
        else:
            if t.get("entry_bar"): exp[(t["index"], "ENTRY", f"{d} {t['entry_bar']}")] = (t["entry_price"], t["side"])
            if t["status"].startswith("Closed") and t.get("exit_bar"): exp[(t["index"], "EXIT", f"{d} {t['exit_bar']}")] = (t["exit_price"], "SELL" if t["side"] == "BUY" else "BUY")
    missed = sorted(k for k in exp if k not in led); extra = sorted(k for k in led if k not in exp)
    diff = [k for k in exp if k in led and (abs(float(led[k]["index_price"]) - float(exp[k][0])) > 0.01 or led[k]["side"] != exp[k][1])]
    nofut = [k for k, r in led.items() if k[1] != "SKIP" and r["fut_price"] == ""]
    wide = [k for k, r in led.items() if r["fut_price"] != "" and abs(float(r["fut_price"]) / float(r["index_price"]) - 1) > 0.01]
    info["live"] = dict(start=start, events=len(led), expected=len(exp), missed=len(missed), extra=len(extra), price_or_side_differs=len(diff), no_future_price=len(nofut), future_far_from_index=len(wide))
    if missed: problems.append(("warn", f"live ledger is missing {len(missed)} replay events (first: {missed[:3]}). Usually a missed Kite login or a failed live run. They are filled in after the next successful live run if within 6 days; the replay itself is unaffected."))
    if extra: problems.append(("warn", f"live ledger has {len(extra)} events the replay does not (first: {extra[:3]})"))
    if diff: problems.append(("warn", f"{len(diff)} live events differ from the replay in price or side (first: {diff[:3]})"))
    if nofut: problems.append(("warn", f"{len(nofut)} live events have no futures price (first: {nofut[:2]})"))
    if wide: problems.append(("warn", f"{len(wide)} live events have a futures price over 1% away from the index price (first: {wide[:2]})"))

def rowhash(t):  # would_have_net is a hypothetical for skipped trades and depends on later candles, so it is not part of the fingerprint
    return hashlib.sha1(json.dumps({k: t[k] for k in sorted(t) if k not in ("note", "would_have_net")}, sort_keys=True, default=str).encode()).hexdigest()[:12]

def write_dash_live(out):
    """Two small files for the dashboard's live check panel (the evening Claude task uploads them)."""
    os.makedirs(f"{LIVE}/dash", exist_ok=True)
    live = out["data"].get("live"); probs = out["problems"]
    if isinstance(live, dict): live_txt = f"{live['events']} live events, {live['missed']} missed, {live['extra']} extra, {live['price_or_side_differs']} differing, {live['no_future_price']} without futures price"
    else: live_txt = live or "no live ledger yet"
    head = f"{out['status']}: {len(probs)} problem(s); latest day {out.get('latest_day', '?')}; live: {live_txt}"
    rows = [dict(item="headline", value=head), dict(item="status", value=out["status"]), dict(item="checked at (India time)", value=out["run_at"]),
            dict(item="latest day in ledger", value=str(out.get("latest_day", "?"))), dict(item="problems", value="; ".join(probs) if probs else "none"), dict(item="live ledger", value=live_txt)]
    for nm in INDEXES:
        d = out["data"].get(nm, {})
        rows.append(dict(item=f"newest candle {NAMES[nm]}", value=f"{d.get('last_candle', '?')} ({d.get('source_of_last', '?')})"))
    for ix, v in sorted(out.get("net_by_index", {}).items()): rows.append(dict(item=f"net result {ix} (Rs)", value=f"{v:,}"))
    json.dump(rows, open(f"{LIVE}/dash/live_check.json", "w"), separators=(",", ":"))
    src = f"{LIVE}/ledger.csv"; hdr = "event_id,recorded_at_ist,event,index,side,signal_bar,fill_time,index_price,fut_symbol,fut_price,fut_candle,qty,note\n"
    open(f"{LIVE}/dash/live_ledger.csv", "w").write(open(src).read() if os.path.exists(src) else hdr)

def main():
    os.makedirs(LIVE, exist_ok=True)
    _n = dt.datetime.now(IST)
    while _n.weekday() >= 5: _n -= dt.timedelta(days=1)   # weekends: expect the Friday data
    today = _n.strftime("%Y-%m-%d")
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
        check_live(trades, problems, info)
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
    write_dash_live(out)
    print(json.dumps(out, indent=1))

main()
