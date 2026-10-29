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

# Pass or fail rules for the 2 to 4 week paper run (agreed before results are seen)
LIVE_FIRST_DAY = os.environ.get("PAPER_LIVE_FIRST_DAY", "2026-10-26")   # first day the live job is expected to run
SLOTS = 76                      # 5-minute runs from 09:20 to 15:35 inclusive
CRITERIA = dict(min_days=20, match=0.99, missed=0, fut_coverage=0.98, good_run_days=0.90, max_gap_min=20, min_slot_share=0.80, stop_loss=300000)

def fetch_runs(day):
    """Runs of the paper-live workflow created on this (UTC) date. Uses GITHUB_TOKEN inside GitHub Actions. Returns None when it cannot be read."""
    f = os.environ.get("PAPER_RUNS_JSON")
    if f: return json.load(open(f))
    repo = os.environ.get("GITHUB_REPOSITORY"); tok = os.environ.get("GITHUB_TOKEN")
    if not repo: return None
    import urllib.request
    out = []
    for page in (1, 2):
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/paper-live.yml/runs?per_page=100&page={page}&created={day}",
                                     headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
        try: d = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception: return None if page == 1 else out
        out += d.get("workflow_runs", [])
        if len(out) >= d.get("total_count", 0): break
    return out

def check_coverage(today, has_candles, problems, info):
    """Did the 5-minute live job actually run all day? Appends one line per day to data/live/run_coverage.csv."""
    if today < LIVE_FIRST_DAY or not has_candles: return
    utc_days = {today, (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()}   # IST morning is the previous UTC date only before 05:30 UTC; runs here are all same UTC date
    ist = []; runs = []
    tks = sorted(glob.glob(f"{LIVE}/ticks/*.csv")) + ([f"{LIVE}/ticks.csv"] if os.path.exists(f"{LIVE}/ticks.csv") else [])
    if tks:   # every looping run logs its own tick file: exact, no API needed. Count each minute once, in case two runs overlap.
        seen = set()
        for tk in tks:
            for r in csv.DictReader(open(tk)):
                if r["ts_ist"][:10] == today and r["rc"] == "0" and "09:20" <= r["ts_ist"][11:16] <= "15:35" and r["ts_ist"] not in seen:
                    seen.add(r["ts_ist"]); ist.append(dt.datetime.strptime(r["ts_ist"], "%Y-%m-%d %H:%M"))
    else:
        runs = fetch_runs(today)
        if runs is None: info["run_coverage"] = "could not read the run history"; return
    for r in runs:
        t = r.get("run_started_at") or r.get("created_at")
        if not t or r.get("conclusion") != "success": continue
        d = dt.datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).astimezone(IST)
        if d.strftime("%Y-%m-%d") == today and "09:20" <= d.strftime("%H:%M") <= "15:35": ist.append(d)
    ist.sort(); mins = [d.hour * 60 + d.minute for d in ist]
    pts = [9 * 60 + 20] + mins + [15 * 60 + 35]; gap = max((b - a for a, b in zip(pts, pts[1:])), default=0)
    share = len(ist) / SLOTS; good = share >= CRITERIA["min_slot_share"] and gap <= CRITERIA["max_gap_min"]
    info["run_coverage"] = dict(runs_in_window=len(ist), slots=SLOTS, share=round(share, 2), longest_gap_min=gap, good_day=good)
    if not good: problems.append(("warn", f"live job coverage today: {len(ist)} of {SLOTS} five-minute runs, longest silent gap {gap} minutes (GitHub schedules can run late or skip)"))
    path = f"{LIVE}/run_coverage.csv"; rows = {}
    if os.path.exists(path):
        for r in csv.DictReader(open(path)): rows[r["day"]] = r
    rows[today] = dict(day=today, runs=len(ist), slots=SLOTS, longest_gap_min=gap, good=int(good))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["day", "runs", "slots", "longest_gap_min", "good"]); w.writeheader()
        for k in sorted(rows): w.writerow(rows[k])

def roll_info(trades):
    """Positions carried across a contract change are rolled in real trading. The replay ignores it; count them and estimate charges."""
    from mock_trades import leg
    by = {}
    for t in trades:
        if t["status"] != "Skipped": by.setdefault((t["index"], t["trade_no"]), []).append(t)
    n = 0; cost = 0.0
    for rows in by.values():
        rows.sort(key=lambda r: r["day"])
        for a, b in zip(rows, rows[1:]):
            if a["symbol"] != b["symbol"]:
                n += 1; px = b.get("mark_price") or b.get("exit_price") or b["entry_price"]; u = b["qty"]; bse = b["index"] == "Sensex"
                cost += leg(px, u, "sell", b["day"], bse)["total"] + leg(px, u, "buy", b["day"], bse)["total"]
    return n, round(cost)

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
    # live uses Kite candles, the replay can still hold Yahoo candles for the newest day; a price gap under 0.02% (about 4.5 points on Nifty) is that source difference, not a real mismatch. A side or signal difference still counts.
    diff = [k for k in exp if k in led and (abs(float(led[k]["index_price"]) - float(exp[k][0])) > 0.0002 * float(exp[k][0]) or led[k]["side"] != exp[k][1])]
    nofut = [k for k, r in led.items() if k[1] != "SKIP" and r["fut_price"] == ""]
    wide = [k for k, r in led.items() if r["fut_price"] != "" and abs(float(r["fut_price"]) / float(r["index_price"]) - 1) > 0.01]
    nonskip = sum(1 for k in led if k[1] != "SKIP")
    info["live"] = dict(start=start, events=len(led), non_skip=nonskip, expected=len(exp), missed=len(missed), extra=len(extra), price_or_side_differs=len(diff), no_future_price=len(nofut), future_far_from_index=len(wide))
    if missed: problems.append(("warn", f"live ledger is missing {len(missed)} replay events (first: {missed[:3]}). Usually a missed Kite login or a failed live run. They are filled in after the next successful live run if within 6 days; the replay itself is unaffected."))
    if extra: problems.append(("warn", f"live ledger has {len(extra)} events the replay does not (first: {extra[:3]})"))
    if diff: problems.append(("warn", f"{len(diff)} live events differ from the replay in price or side (first: {diff[:3]})"))
    if nofut: problems.append(("warn", f"{len(nofut)} live events have no futures price (first: {nofut[:2]})"))
    if wide: problems.append(("warn", f"{len(wide)} live events have a futures price over 1% away from the index price (first: {wide[:2]})"))

def check_stop(trades, problems, info):
    """Experiment stop rule: combined net result (after charges, all three indexes) since live recording began."""
    path = f"{LIVE}/live_start.txt"
    if not os.path.exists(path): info["stop_rule"] = "recording not started"; return
    start = open(path).read().strip(); net = sum(t["net"] for t in trades if t["day"] >= start)
    info["stop_rule"] = dict(since=start, net=net, limit=-CRITERIA["stop_loss"])
    if net <= -CRITERIA["stop_loss"]:
        problems.append(("warn", f"STOP RULE HIT: combined net since {start} is Rs {net:,}, at or below the agreed limit of minus Rs {CRITERIA['stop_loss']:,}. The experiment ends here; review before anything else."))

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

    pf = f"{LIVE}/preflight.json"
    if os.path.exists(pf):
        try:
            d = json.load(open(pf)); bad = [c for c in d["checks"] if c["result"] == "FAIL"]
            rows.append(dict(item="morning preflight", value=(f"{d['status'].upper()} at {d['run_at']}: {len(d['checks']) - len(bad)} of {len(d['checks'])} checks passed" + ("; failed: " + "; ".join(c["check"] + " (" + c["detail"][:80] + ")" for c in bad) if bad else ""))))
        except Exception: rows.append(dict(item="morning preflight", value="could not read the result"))
    else: rows.append(dict(item="morning preflight", value="not run yet"))
    n_roll, c_roll = roll_info(out.get("_trades", []))
    rows.append(dict(item="contract rolls so far (replay)", value=f"{n_roll} rolls, estimated extra charges about Rs {c_roll:,} (price gap between contracts not included)"))
    C = CRITERIA; cov = {}
    p = f"{LIVE}/run_coverage.csv"
    if os.path.exists(p): cov = {r["day"]: r for r in csv.DictReader(open(p))}
    days = len(cov); good_days = sum(int(r["good"]) for r in cov.values())
    rows.append(dict(item="SCORECARD days recorded", value=f"{days} of at least {C['min_days']} needed before judging" + ("" if days >= C["min_days"] else " (WAIT)")))
    if isinstance(live, dict):
        exp = max(live["expected"], 1); bad = live["missed"] + live["extra"] + live["price_or_side_differs"]; rate = 1 - bad / exp
        fut = 1 - live["no_future_price"] / max(live.get("non_skip", 0), 1)
        judge = days >= C["min_days"]
        def verdict(ok): return ("PASS" if ok else "FAIL") if judge else ("on track" if ok else "behind")
        rows.append(dict(item="SCORECARD live matches replay", value=f"{rate:.1%} (target {C['match']:.0%}) {verdict(rate >= C['match'])}"))
        rows.append(dict(item="SCORECARD missed events", value=f"{live['missed']} (target {C['missed']}) {verdict(live['missed'] <= C['missed'])}"))
        rows.append(dict(item="SCORECARD futures price on entries and exits", value=f"{fut:.1%} (target {C['fut_coverage']:.0%}) {verdict(fut >= C['fut_coverage'])}"))
    if days:
        share = good_days / days
        rows.append(dict(item="SCORECARD days with good run coverage", value=f"{share:.0%} (target {C['good_run_days']:.0%}; a good day has {C['min_slot_share']:.0%} of runs and no gap over {C['max_gap_min']} minutes) " + (("PASS" if share >= C["good_run_days"] else "FAIL") if days >= C["min_days"] else ("on track" if share >= C["good_run_days"] else "behind"))))
    sr = out["data"].get("stop_rule")
    cur = f"combined net since recording began: Rs {sr['net']:,}" if isinstance(sr, dict) else "recording not started"
    hit = isinstance(sr, dict) and sr["net"] <= -C["stop_loss"]
    rows.append(dict(item="SCORECARD loss that ends the experiment", value=f"Rs {C['stop_loss']:,} combined; {cur}" + (" LIMIT REACHED" if hit else "")))
    json.dump(rows, open(f"{LIVE}/dash/live_check.json", "w"), separators=(",", ":"))
    src = f"{LIVE}/ledger.csv"; hdr = "event_id,recorded_at_ist,event,index,side,signal_bar,fill_time,index_price,fut_symbol,fut_price,fut_candle,qty,fut_bid,fut_ask,quote_delay_min,note\n"
    open(f"{LIVE}/dash/live_ledger.csv", "w").write(open(src).read() if os.path.exists(src) else hdr)

def main():
    os.makedirs(LIVE, exist_ok=True)
    _n = dt.datetime.now(IST)
    while _n.weekday() >= 5: _n -= dt.timedelta(days=1)   # weekends: expect the Friday data
    today = _n.strftime("%Y-%m-%d")
    problems = []; info = {}; status_extra = {}; trades_for_dash = []
    try:
        snap_old = json.load(open(f"{LIVE}/ledger_snapshot.json")) if os.path.exists(f"{LIVE}/ledger_snapshot.json") else None
    except Exception: snap_old = None
    check_data(today, problems, info)
    try:
        import mock_detail as D
        D.build()
        trades = json.load(open(f"{DASH}/trades.json")); trades_for_dash = trades; summary = json.load(open(f"{DASH}/summary.json"))
        last_day = check_ledger(snap_old, trades, summary, problems, info)
        check_live(trades, problems, info)
        check_stop(trades, problems, info)
        check_coverage(today, any(info.get(n, {}).get('last_candle', '')[:10] == today for n in INDEXES), problems, info)
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
    out['_trades'] = trades_for_dash
    write_dash_live(out)
    out.pop('_trades', None)
    print(json.dumps(out, indent=1))

main()
