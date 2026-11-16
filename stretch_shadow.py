"""Shadow flag, observer only. For each new ENTRY signal, records whether it fights a stretched 5-day move.
It changes no alert, order, skip rule or ledger. Output: data/live/stretch_shadow.csv (one row per entry id).
Move = percent change of the index close between 6 sessions back and the previous session, signed against the trade
(positive means the market ran against the entry side). Flagged = move above the index cutoff (top 10 percent of 5 years of entries).
Review after about 20 flagged signals: keep the idea only if flagged trades are net negative."""
import os, csv
CUTOFF = {"NSEI": 2.66, "NSEBANK": 3.22, "BSESN": 2.70}
COLS = ["event_id", "day", "index", "side", "signal_bar", "fight_move_pct", "cutoff_pct", "flagged"]

def daily_closes(path, before_day):
    last = {}
    with open(path) as f:
        for r in csv.reader(f):
            if len(r) < 5 or not r[0][:1].isdigit(): continue
            d = r[0][:10]
            if d < before_day: last[d] = float(r[4])
    return last

def fight_move(closes, side):
    days = sorted(closes)
    if len(days) < 6: return None
    pcl, c5 = closes[days[-1]], closes[days[-6]]
    x = 1 if side == "BUY" else -1
    return -x * (pcl - c5) / c5 * 100

def log_entry(live_dir, nm, ev, event_id):
    """Never raises. Writes once per event_id."""
    try:
        out = os.path.join(live_dir, "stretch_shadow.csv")
        if os.path.exists(out):
            with open(out) as f:
                if any(r.startswith(event_id + ",") for r in f): return
        mv = fight_move(daily_closes(os.path.join(live_dir, "candles", nm + "_5m.csv"), ev["day"]), ev["side"])
        if mv is None: return
        new = not os.path.exists(out)
        with open(out, "a", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(COLS)
            w.writerow([event_id, ev["day"], nm, ev["side"], ev["bar"], "%.2f" % mv, CUTOFF[nm], "yes" if mv > CUTOFF[nm] else "no"])
    except Exception:
        pass
