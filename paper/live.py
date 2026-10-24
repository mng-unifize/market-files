#!/usr/bin/env python3
"""Live paper trading recorder. Runs every 5 minutes during market hours (GitHub Actions). Needs: pip install kiteconnect.
Each run is stateless: it fetches the last few days of 5-minute candles from Kite, runs the same engine as the evening rebuild,
and appends any NEW signal to data/live/ledger.csv (once per event). A late or skipped run loses nothing: the next run catches up.
Signals come from index candles. Fill prices are recorded from the near-month futures candle at the same time.
Env: KITE_API_KEY, KITE_ACCESS_TOKEN, PAPER_RECORD ("yes" to write the ledger, anything else is a dry run), GITHUB_OUTPUT (optional),
PAPER_NOW (tests only, "YYYY-MM-DD HH:MM" India time), PAPER_START (first day to record, default: first recorded day)."""
import sys, os, csv, json, time, datetime as dt, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_detail as D
from mock_trades import M, ema, rules_, combine

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LIVE = "data/live"; CANDLES = f"{LIVE}/candles"; LEDGER = f"{LIVE}/ledger.csv"
HDR = ["event_id", "recorded_at_ist", "event", "index", "side", "signal_bar", "fill_time", "index_price", "fut_symbol", "fut_price", "fut_candle", "qty", "fut_bid", "fut_ask", "quote_delay_min", "note"]
IDX = {"NSEI": ("NSE", "NIFTY 50"), "NSEBANK": ("NSE", "NIFTY BANK"), "BSESN": ("BSE", "SENSEX")}
FUT_EX = {"NSEI": "NFO", "NSEBANK": "NFO", "BSESN": "BFO"}
FUT_NAME = {"NSEI": "NIFTY", "NSEBANK": "BANKNIFTY", "BSESN": "SENSEX"}
LOTS = {"NSEI": 65, "NSEBANK": 30, "BSESN": 20}
FIRST_RUN, LAST_RUN = "09:20", "15:50"; FINAL_FROM = "15:35"

def now_ist():
    v = os.environ.get("PAPER_NOW")
    return dt.datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if v else dt.datetime.now(IST)

def get_client():
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=os.environ["KITE_API_KEY"].strip()); k.set_access_token(os.environ["KITE_ACCESS_TOKEN"].strip()); return k

def out(key, val):
    p = os.environ.get("GITHUB_OUTPUT")
    if p: open(p, "a").write(f"{key}={val}\n")

def save_rows(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"])
        for k in sorted(rows): w.writerow(rows[k])

def load_rows(path):
    return {r["ts_ist"]: [r["ts_ist"], r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in csv.DictReader(open(path))} if os.path.exists(path) else {}

def candles(kite, token, now, days=6):
    """Completed 5-minute candles only (the candle still forming is dropped)."""
    a = (now - dt.timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0, tzinfo=None)
    res = []
    for attempt in range(3):
        try:
            res = kite.historical_data(token, a, now.replace(tzinfo=None), "5minute"); break
        except Exception as e:
            if attempt == 2: raise
            time.sleep(2 + 2 * attempt)
    time.sleep(0.4); rows = {}
    for x in res:
        d = x["date"]; t = d.strftime("%Y-%m-%d %H:%M")
        if t[11:] >= "15:30": continue
        if d.replace(tzinfo=None) + dt.timedelta(minutes=5) > now.replace(tzinfo=None): continue   # still forming
        rows[t] = [t, x["open"], x["high"], x["low"], x["close"], x.get("volume", 0)]
    return rows

def trim_incomplete(rows, tf):
    """Drop the trailing, not yet complete candle of a longer timeframe (for example a 15-minute bar with only 5 or 10 minutes done)."""
    if tf == 5 or not rows: return rows
    last = max(rows); mins = int(last[11:13]) * 60 + int(last[14:16]); k = (mins - 555) // tf
    if mins + 5 >= 555 + (k + 1) * tf: return rows
    return {t: r for t, r in rows.items() if not (t[:10] == last[:10] and (int(t[11:13]) * 60 + int(t[14:16]) - 555) // tf == k)}

def find_tokens(kite, today):
    tokens = {}; futs = {}
    for ex in ("NSE", "BSE"):
        for ins in kite.instruments(ex):
            for nm, (e, sym) in IDX.items():
                if e == ex and ins["tradingsymbol"] == sym: tokens[nm] = ins["instrument_token"]
    for ex in ("NFO", "BFO"):
        for ins in kite.instruments(ex):
            for nm in FUT_EX:
                if FUT_EX[nm] == ex and ins.get("name") == FUT_NAME[nm] and ins.get("instrument_type") == "FUT":
                    futs.setdefault(nm, {})[ins["tradingsymbol"]] = (ins["instrument_token"], str(ins["expiry"])[:10], ins.get("lot_size"))
    return tokens, futs

def pick_future(nm, day, futs):
    want = D.contract(nm, day); f = futs.get(nm, {})
    if want in f: return want, f[want][0]
    ok = sorted((e, s) for s, (t, e, _l) in f.items() if e >= day)   # fallback: nearest expiry
    return (ok[0][1], f[ok[0][1]][0]) if ok else (want, None)

def recipe_events(nm, work):
    """Entry, exit and skipped-signal events from the engine for one index, using the same rules as the dashboard."""
    tf, fa, sl, rule_names = D.CFG[nm]
    combine(nm, work); m = M(nm, D.resample(nm, work, tf)); B = m.B
    c = [b["c"] for b in B]; m.E9 = ema(c, fa); m.E21 = ema(c, sl); m.start = m.S + dt.timedelta(days=60)
    T = D.fix_prevlen(m, [t for t in m.gen("hold") if dt.date.fromisoformat(t["d"]) >= m.start])
    R = rules_(m, [t for t in T if m.year(t["d"]) < 3]); R = {k: R[k] for k in rule_names}
    stopped = D.loss_stop_flags([t for t in T if not any(f(t) for f in R.values())], D.STOP_LOSS[nm]) if nm in D.STOP_LOSS else set()
    ev = []
    for t in T:
        why = [k for k, f in R.items() if f(t)]
        if not why and id(t) in stopped: why = ["monthly loss stop"]
        side = "BUY" if t["dir"] == 1 else "SELL"; opp = "SELL" if t["dir"] == 1 else "BUY"
        e = B[t["ent"]]; ent_ts = e["ts"]
        ev.append(dict(event="SKIP" if why else "ENTRY", nm=nm, side=side, bar=ent_ts, fill=D.fill_time(ent_ts, tf), price=e["c"], tf=tf, day=e["d"],
                       note=("skipped: " + "; ".join(D.RULE_WORDS[w] for w in why)) if why else ""))
        if not why and not t.get("open"):
            x = B[t["ex"]]; ev.append(dict(event="EXIT", nm=nm, side=opp, bar=x["ts"], fill=D.fill_time(x["ts"], tf), price=t["p1"], tf=tf, day=x["d"], note=""))
    return ev

def main():
    now = now_ist(); hhmm = now.strftime("%H:%M"); today = now.strftime("%Y-%m-%d")
    record = os.environ.get("PAPER_RECORD", "").lower() == "yes"
    if now.weekday() >= 5 or hhmm < FIRST_RUN or hhmm > LAST_RUN:
        print("outside market window, nothing to do", today, hhmm); out("final", "false"); return 0
    final = hhmm >= FINAL_FROM; out("final", "true" if final else "false")
    status = dict(run_at=now.strftime("%Y-%m-%d %H:%M"), mode="record" if record else "dry run", problems=[], new_events=[], notes=[])
    try:
        kite = get_client(); tokens, futs = find_tokens(kite, today)
    except Exception as e:
        status["problems"].append("login or instrument lookup failed: " + str(e)[:200]); write_status(status, True)
        print(status); return 1 if hhmm < "09:35" else 0   # fail loudly only in the first runs so you get one email, not 75
    missing = [nm for nm in IDX if nm not in tokens]
    if missing: status["problems"].append(f"index not found at Kite: {missing}")
    work = "/tmp/live_work"; fut_c = {}; fut_sym = {}; fut_tok = {}
    for nm in IDX:
        if nm not in tokens: continue
        try:
            rows = candles(kite, tokens[nm], now)
        except Exception as e:
            status["problems"].append(f"{nm} candles failed: {str(e)[:150]}"); continue
        tf = D.CFG[nm][0]; path = f"{CANDLES}/{nm}_5m.csv"
        keep = trim_incomplete(rows, tf) if not final else rows
        merged = load_rows(path); merged.update(keep)
        save_rows(path, merged)
        n_today = sum(1 for k in rows if k[:10] == today); status[f"{nm}_candles_today"] = n_today
        if final and n_today and n_today < 74: status["problems"].append(f"{nm}: only {n_today} of 75 candles today")
        sym, ftok = pick_future(nm, today, futs); fut_sym[nm] = sym; fut_tok[nm] = ftok
        if ftok is None: status["problems"].append(f"{nm}: future {sym} not found at Kite"); fut_c[nm] = {}
        else:
            try: fut_c[nm] = candles(kite, ftok, now, days=1)
            except Exception as e: status["problems"].append(f"{nm} future candles failed: {str(e)[:150]}"); fut_c[nm] = {}
    if not any(status.get(f"{nm}_candles_today") for nm in IDX):
        status["notes"].append("no candles today (market holiday, or data not available yet)")
        if final: write_status(status, record)
        print(status); return 0
    start = os.environ.get("PAPER_START") or (open(f"{LIVE}/live_start.txt").read().strip() if os.path.exists(f"{LIVE}/live_start.txt") else today)
    have = set()
    if os.path.exists(LEDGER): have = {r["event_id"] for r in csv.DictReader(open(LEDGER))}
    new = []; last_entry = {}
    if os.path.exists(LEDGER):
        for r in csv.DictReader(open(LEDGER)):
            if r["event"] == "ENTRY": last_entry[r["index"]] = r["fut_symbol"]
    for nm in IDX:
        if nm not in tokens: continue
        for ev in recipe_events(nm, work):
            if ev["day"] < start: continue
            eid = f"{nm}|{ev['bar']}|{ev['event']}"
            if eid in have: continue
            fts = (dt.datetime.strptime(ev["bar"], "%Y-%m-%d %H:%M") + dt.timedelta(minutes=ev["tf"] - 5)).strftime("%Y-%m-%d %H:%M")   # last 5-minute candle of the bar
            fc = fut_c.get(nm, {}).get(fts); fp = round(float(fc[4]), 2) if fc and ev["day"] == today else ""
            notes = [ev["note"]] if ev["note"] else ([] if fp != "" else ["future price not captured (late catch-up or missing candle)"])
            if ev["event"] == "ENTRY": last_entry[D.NAMES[nm]] = fut_sym.get(nm, "")
            if ev["event"] == "EXIT" and last_entry.get(D.NAMES[nm]) and fut_sym.get(nm) and last_entry[D.NAMES[nm]] != fut_sym[nm]:
                notes.append(f"rolled: entered in {last_entry[D.NAMES[nm]]}, exit priced in {fut_sym[nm]}; roll cost not modelled")
            bid = ask = delay = ""
            fill_dt = dt.datetime.strptime(ev["day"] + " " + ev["fill"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            lag = (now - fill_dt).total_seconds() / 60
            if ev["event"] != "SKIP" and ev["day"] == today and lag <= 15 and fut_tok.get(nm):
                try:
                    q = kite.quote([f"{FUT_EX[nm]}:{fut_sym[nm]}"])[f"{FUT_EX[nm]}:{fut_sym[nm]}"]["depth"]
                    bid, ask, delay = q["buy"][0]["price"], q["sell"][0]["price"], round(lag, 1)
                except Exception as e: notes.append("quote not captured: " + str(e)[:60])
                time.sleep(1.1)   # the quote endpoint allows 1 request per second
            new.append([eid, now.strftime("%Y-%m-%d %H:%M"), ev["event"], D.NAMES[nm], ev["side"], ev["bar"], ev["fill"], ev["price"], fut_sym.get(nm, ""), fp, fts if fp != "" else "", LOTS[nm], bid, ask, delay, "; ".join(notes)])
    status["new_events"] = [[r[0], r[1], r[2], r[3], r[4], r[6]] for r in new]
    if record and new:
        os.makedirs(LIVE, exist_ok=True)
        first = not os.path.exists(LEDGER)
        with open(LEDGER, "a", newline="") as f:
            w = csv.writer(f)
            if first: w.writerow(HDR)
            w.writerows(new)
        if not os.path.exists(f"{LIVE}/live_start.txt"): open(f"{LIVE}/live_start.txt", "w").write(start)
    if final or status["problems"] or (record and new): write_status(status, record)
    out("changed", "true" if (record and new) or final else "false")
    print(json.dumps(status, indent=1)); return 0

def write_status(status, record):
    os.makedirs(LIVE, exist_ok=True)
    json.dump(status, open(f"{LIVE}/{'live_status' if record else 'dryrun_last'}.json", "w"), indent=1)

sys.exit(main()) if __name__ == "__main__" else None
