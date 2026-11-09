"""Golden test: do the live signals ever differ from the backtest?  Read only. Writes only to a temp folder.

The live engine (paper/live.py recipe_events) is run on candle history that stops at a chosen bar, exactly as a live check sees it
(15 minute indices drop the unfinished bar). Its answer is compared with the same engine run on the full history (the backtest).
A cut is clean when the signals it shows are IDENTICAL to the backtest's signals up to that bar: nothing repaints, nothing is added
or removed once more candles arrive.  Cuts are taken at every real signal bar (entry, exit, skip) and at random ordinary bars.

  python golden_replay.py NSEI --days 120 --controls 30
  python golden_replay.py ALL --days 60 --controls 15
Result lines: CLEAN or MISMATCH with the first differences. Exit code 0 only if every cut is clean."""
import sys, os, csv, json, random, tempfile, datetime as dt, time
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import live as L, mock_detail as D

def sig(ev): return (ev["event"], ev["side"], ev["bar"], round(float(ev["price"]), 2))

def read(path):
    with open(path, newline="") as f: return {r["ts_ist"]: r for r in csv.DictReader(f)}

def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"])
        for k in sorted(rows): r = rows[k]; w.writerow([k, r["open"], r["high"], r["low"], r["close"], r.get("volume", 0)])

def bar_start(ts, tf):
    m = int(ts[11:13]) * 60 + int(ts[14:16]); k = (m - 555) // tf; s = 555 + k * tf
    return "%s %02d:%02d" % (ts[:10], s // 60, s % 60)

def run_index(nm, days, controls, seed=7):
    tf = D.CFG[nm][0]; tmp = tempfile.mkdtemp(prefix="ibt_golden_"); real_combine = L.combine
    full_work = os.path.join(tmp, "full"); real_combine(nm, full_work)
    full_rows = read(os.path.join(full_work, nm + "_5m.csv"))
    full_ev = L.recipe_events(nm, os.path.join(tmp, "fullrun"))     # the backtest: engine on all history
    full_sig = [sig(e) for e in full_ev]
    last_day = max(full_rows)[:10]
    since = (dt.date.fromisoformat(last_day) - dt.timedelta(days=days)).isoformat()
    # cut points: last 5-minute bar that completes the signal bar of each recent event, plus random ordinary bars
    cuts = {}
    for e in full_ev:
        if e["day"] >= since:
            b = e["bar"]; m = int(b[11:13]) * 60 + int(b[14:16]) + tf - 5
            cuts["%s %02d:%02d" % (b[:10], m // 60, m % 60)] = "signal"
    recent = sorted(t for t in full_rows if t[:10] >= since and "09:30" <= t[11:] <= "15:25")
    rnd = random.Random(seed); pool = [t for t in recent if t not in cuts]
    for t in rnd.sample(pool, min(controls, len(pool))): cuts[t] = "control"
    results = []; t0 = time.time()
    for cut in sorted(cuts):
        rows = {t: r for t, r in full_rows.items() if t <= cut}
        if tf != 5: rows = L.trim_incomplete(rows, tf)                 # a live check before 15:35 drops the unfinished bar
        def fake(name, work, rows=rows):
            write(os.path.join(work, name + "_5m.csv"), rows); return max(rows)
        L.combine = fake
        try: ev = L.recipe_events(nm, os.path.join(tmp, "cutrun"))
        finally: L.combine = real_combine
        got = [sig(e) for e in ev]
        top = bar_start(max(rows), tf) if tf != 5 else max(rows)
        want = [s for s in full_sig if s[2] <= top]
        ok = got == want
        results.append((cut, cuts[cut], ok, [g for g in got if g not in want][:3], [w for w in want if w not in got][:3]))
    L.combine = real_combine
    return nm, full_sig, results, time.time() - t0

def main(argv):
    if not argv: print(__doc__); return 2
    names = list(D.CFG) if argv[0] == "ALL" else [argv[0]]
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 120
    controls = int(argv[argv.index("--controls") + 1]) if "--controls" in argv else 30
    bad = 0
    for nm in names:
        nm, full_sig, res, secs = run_index(nm, days, controls)
        n_sig = sum(1 for r in res if r[1] == "signal"); n_ctl = len(res) - n_sig
        wrong = [r for r in res if not r[2]]; bad += len(wrong)
        print("%s: %d signal cuts, %d control cuts, %d mismatches (%.0f s)  %s" % (nm, n_sig, n_ctl, len(wrong), secs, "CLEAN" if not wrong else "MISMATCH"))
        for cut, kind, ok, extra, missing in wrong[:5]:
            print("   cut %s (%s): live shows but backtest does not: %s | backtest has but live lacks: %s" % (cut, kind, extra, missing))
        sys.stdout.flush()
    return 0 if bad == 0 else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
