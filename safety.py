"""Gate 4 safety layer for automated orders. Pure logic plus two file reads and one read-only ntfy poll. It places no orders and has no code that could.

Used by order_exec.py (and, later, the live sender). Every check returns plain reasons, so a block can be sent to your phone in words.
  1. Kill switch: a file data/live/STOP, or the message STOP sent to your ntfy topic from your phone. A phone STOP creates the file (a latch), so it stays
     stopped until you delete the file on the Mac. The phone cannot switch trading back on, on purpose.
  2. Daily loss stop: once the day is down DAILY_LOSS_STOP rupees on paper (all three indexes together, mark to market), new ENTRIES are blocked.
     EXITS are never blocked by the loss stop, so a losing position can always be closed.
  3. Hard caps: 1 lot, quantity exactly lots x lot size, order only inside market hours, limit within a price band of the last price, spread not abnormal.
  4. Duplicate guard: one signal (event_id) can only ever produce one order.
  5. Position check against Kite positions (used when orders are live)."""
import os, csv, json, datetime as dt, urllib.request

DAILY_LOSS_STOP = 40000          # rupees, all three indexes together (owner decision 13 Nov 2026)
MAX_LOTS = 1
MAX_ORDERS_PER_DAY = 8          # 3 indexes x (1 exit + 1 entry) = 6 on a normal day; 8 leaves room for one roll leg pair. More means something is wrong.
PRICE_BAND = 0.003               # limit may differ from the last price by at most 0.3 percent
SPREAD_MAX = 0.002               # bid-ask spread above 0.2 percent of the mid blocks the order
SESSION = ("09:15", "15:30")
STOP_FILE = "data/live/STOP"
STOP_SEEN = "data/live/stop_seen.txt"
LOTS = {"NSEI": 65, "BSESN": 20, "NSEBANK": 30}
CANDLES = "data/live/candles"
ALERTS = "data/live/alerts.csv"

# ---------- kill switch ----------
def poll_ntfy(topic, base="https://ntfy.sh", since="24h", timeout=8):
    """Read-only: list of dicts (id, time, message) from the topic. Raises on network failure."""
    url = topic if topic.startswith("http") else base.rstrip("/") + "/" + topic
    url = url.rstrip("/") + "/json?poll=1&since=" + since
    out = []
    with urllib.request.urlopen(url, timeout=timeout) as r:
        for line in r.read().decode("utf-8", "replace").splitlines():
            try: m = json.loads(line)
            except ValueError: continue
            if m.get("event") == "message": out.append(m)
    return out

def seen_stop_ids():
    if not os.path.exists(STOP_SEEN): return set()
    return {x.strip() for x in open(STOP_SEEN) if x.strip()}

def kill_switch(topic=None, base="https://ntfy.sh", fetch=None, now=None):
    """(active, reason, warning). The STOP file wins. A phone STOP that is new (id not seen before) creates the file."""
    now = now or dt.datetime.now()
    if os.path.exists(STOP_FILE):
        try: why = open(STOP_FILE).read().strip()[:120]
        except Exception: why = ""
        return True, "STOP file is present" + (" (%s)" % why if why else ""), ""
    warn = ""
    if topic:
        try:
            msgs = (fetch or poll_ntfy)(topic, base)
            seen = seen_stop_ids()
            new = [m for m in msgs if str(m.get("message", "")).strip().upper() == "STOP" and m.get("id") not in seen]
            if new:
                os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
                with open(STOP_FILE, "w") as f: f.write("STOP from phone at %s\n" % now.strftime("%Y-%m-%d %H:%M:%S"))
                with open(STOP_SEEN, "a") as f:
                    for m in new: f.write(str(m.get("id")) + "\n")
                return True, "STOP received from your phone", ""
        except Exception as e:
            warn = "phone kill switch could not be checked (%s); the STOP file still works" % type(e).__name__
    return False, "", warn

# ---------- daily loss ----------
def _rows(path):
    if not os.path.exists(path): return []
    with open(path, newline="") as f: return list(csv.DictReader(f))

def _closes(nm, candles=CANDLES):
    """{day: (first_close_ts, last_close)} from the 5-minute file; also returns the ordered day list."""
    last = {}
    for r in _rows("%s/%s_5m.csv" % (candles, nm)):
        t = r["ts_ist"]
        if "09:15" <= t[11:16] <= "15:30": last[t[:10]] = float(r["close"])
    return last

def daily_pnl(day, alerts=ALERTS, candles=CANDLES, lots=LOTS, last_price=None):
    """Mark-to-market profit of the day in rupees on the index price (one lot each): positions carried in count from the previous close,
    positions opened today from their entry price, trades closed today up to their exit price. `last_price` {code: price} overrides the last candle close.
    Only alerts that were actually sent count, because those are the ones you act on."""
    names = {"Nifty 50": "NSEI", "Sensex": "BSESN", "Bank Nifty": "NSEBANK"}
    pos = {}; total = 0.0; detail = {}
    for r in _rows(alerts):
        if r.get("alert") != "sent": continue
        code = names.get(r["index"]); 
        if not code: continue
        d = 1 if r["action"] == "BUY" else -1; px = float(r["price"]); rday = r["signal_bar"][:10]
        if rday > day: continue
        if r["event"] == "ENTRY": pos[code] = (d, px, rday)
        elif r["event"] == "EXIT":
            # an exit whose entry predates the alert file (the watcher's first days) is treated as a position carried in from the previous close
            pd_, ppx, pday = pos.pop(code) if code in pos else (-d, px, "0000-00-00")
            if rday == day:
                closes = _closes(code, candles); prev = [x for x in sorted(closes) if x < day]
                ref = ppx if pday >= day or not prev else closes[prev[-1]]
                v = pd_ * (px - ref) * lots[code]; total += v; detail[code] = detail.get(code, 0) + v
    for code, (pd_, ppx, pday) in pos.items():
        closes = _closes(code, candles); prev = [x for x in sorted(closes) if x < day]
        ref = ppx if pday >= day or not prev else closes[prev[-1]]
        last = (last_price or {}).get(code) or closes.get(day)
        if last is None: continue
        v = pd_ * (last - ref) * lots[code]; total += v; detail[code] = detail.get(code, 0) + v
    return total, detail

def daily_pnl_futures(day, tickets="data/live/tickets.csv", alerts=ALERTS, shadow="data/live/orders.csv", lots=LOTS, prev_close_fn=None, last_fn=None,
                      candles=CANDLES):
    """Same idea as daily_pnl, but on FUTURES prices, one lot each. Returns (total, detail, basis) where basis[code] is 'futures' or 'index' (the fallback
    when a futures price is missing, for example a position opened before tickets were logged).
      entry price  = what the shadow sender got, else the touch at the ticket (BUY at the ask, SELL at the bid);
      exit price   = the same, on the exit ticket;
      carried-in positions start from the previous session's futures close (prev_close_fn(symbol, day));
      open positions are marked at the futures last price (last_fn(symbol)).
    A rolled position is not handled (a roll leg is logged but its contract change is ignored); it falls into the index fallback only if a price is missing."""
    names = {"Nifty 50": "NSEI", "Sensex": "BSESN", "Bank Nifty": "NSEBANK"}
    T = {r["event_id"]: r for r in _rows(tickets) if r.get("kind") in ("ENTRY", "EXIT")}
    F = {r["event_id"]: float(r["fill_price"]) for r in _rows(shadow) if r.get("status") in ("FILLED", "REPRICED_FILLED", "FALLBACK_FILLED") and r.get("fill_price")}
    idx_total, idx_detail = daily_pnl(day, alerts, candles, lots)
    def px(eid, side):
        if eid in F: return F[eid]
        tk = T.get(eid)
        if not tk: return None
        v = tk.get("ask") if side == "BUY" else tk.get("bid")
        try: return float(v)
        except (TypeError, ValueError): return None
    pos = {}; detail = {}; basis = {}; bad = set()
    def add(code, v): detail[code] = detail.get(code, 0) + v; basis.setdefault(code, "futures")
    for r in _rows(alerts):
        if r.get("alert") != "sent": continue
        code = names.get(r["index"])
        if not code or r["signal_bar"][:10] > day: continue
        d = 1 if r["action"] == "BUY" else -1; eid = r["event_id"]; rday = r["signal_bar"][:10]
        if r["event"] == "ENTRY": pos[code] = (d, eid, rday)
        elif r["event"] == "EXIT":
            known = pos.pop(code, None); xtk = T.get(eid)
            if rday != day: continue
            pd_ = known[0] if known else -d; eday = known[2] if known else "0000-00-00"
            sym = (xtk or {}).get("contract"); xp = px(eid, r["action"])
            ref = None
            if known and eday >= day: ref = px(known[1], "BUY" if pd_ == 1 else "SELL")
            elif sym and prev_close_fn: ref = prev_close_fn(sym, day)
            if xp is None or ref is None: bad.add(code); continue
            add(code, pd_ * (xp - ref) * lots[code])
    for code, (pd_, eid, eday) in pos.items():
        tk = T.get(eid); sym = (tk or {}).get("contract")
        if not sym: bad.add(code); continue
        ref = px(eid, "BUY" if pd_ == 1 else "SELL") if eday >= day else (prev_close_fn(sym, day) if prev_close_fn else None)
        last = last_fn(sym) if last_fn else None
        if ref is None or last is None: bad.add(code); continue
        add(code, pd_ * (last - ref) * lots[code])
    for code in bad:                                   # fall back to the index figure for that index and say so
        detail[code] = idx_detail.get(code, 0); basis[code] = "index"
    return sum(detail.values()), detail, basis

# ---------- order checks ----------
def pre_trade_checks(kind, event_id, lots, qty, lot_size, limit, bid, ask, last, now, done_ids, stop_active, stop_reason, pnl_today, orders_today=0):
    """(ok, [reasons]). kind is ENTRY or EXIT (a ROLL leg counts as EXIT or ENTRY by its own kind)."""
    why = []
    if stop_active: why.append("kill switch: " + stop_reason)
    if event_id in done_ids: why.append("duplicate: this signal already produced an order")
    if lots > MAX_LOTS: why.append("lots %s above the cap of %d" % (lots, MAX_LOTS))
    if lot_size and qty != lots * lot_size: why.append("quantity %s is not %s lot x %s" % (qty, lots, lot_size))
    hm = now.strftime("%H:%M")
    if not (SESSION[0] <= hm <= SESSION[1]): why.append("outside market hours (%s)" % hm)
    if not limit or not bid or not ask or not last: why.append("no usable quote or limit price")
    else:
        if abs(limit - last) / last > PRICE_BAND: why.append("limit %.2f is more than %.1f%% from the last price %.2f" % (limit, PRICE_BAND * 100, last))
        mid = (bid + ask) / 2
        if (ask - bid) / mid > SPREAD_MAX: why.append("spread %.2f%% is abnormal" % ((ask - bid) / mid * 100))
    if orders_today >= MAX_ORDERS_PER_DAY: why.append("order cap: %d orders already placed today (cap %d)" % (orders_today, MAX_ORDERS_PER_DAY))
    if kind in ("ENTRY", "ROLL-OPEN") and pnl_today is not None and pnl_today <= -DAILY_LOSS_STOP:
        why.append("daily loss stop: the day is down Rs %s (limit Rs %s); new entries are blocked, exits still go" % (format(round(-pnl_today), ","), format(DAILY_LOSS_STOP, ",")))
    return (not why), why

def latch_stop(reason, now=None):
    """Creates the STOP file. Every automatic stop uses it, so the state is the same as a phone STOP: only deleting the file on the Mac resumes."""
    now = now or dt.datetime.now()
    os.makedirs(os.path.dirname(STOP_FILE), exist_ok=True)
    if not os.path.exists(STOP_FILE):
        with open(STOP_FILE, "w") as f: f.write("AUTO STOP %s: %s\n" % (now.strftime("%Y-%m-%d %H:%M:%S"), reason))

def expected_positions(alerts=ALERTS, tickets="data/live/tickets.csv", lots=LOTS, upto=None, before=None):
    """{futures symbol: signed quantity} that the signals say we hold: sent ENTRY alerts open a position on the contract of their ticket, EXIT alerts close it,
    ROLL tickets move it to the next-month contract. `upto` stops after that event id (inclusive); `before` stops just before it."""
    names = {"Nifty 50": "NSEI", "Sensex": "BSESN", "Bank Nifty": "NSEBANK"}
    T = {}
    for r in _rows(tickets): T.setdefault(r["event_id"], []).append(r)
    ev = []
    for r in _rows(alerts):
        if r.get("alert") == "sent" and r["event"] in ("ENTRY", "EXIT"): ev.append((r["logged_at_ist"], "A", r))
    for eid, rs in T.items():
        if eid.endswith("|ROLL") and len(rs) >= 2: ev.append((rs[0]["logged_at_ist"], "R", rs))
    ev.sort(key=lambda x: (x[0], 0 if x[1] == "R" else 1))
    pos = {}
    for _, kind, r in ev:
        if kind == "A":
            code = names.get(r["index"]); eid = r["event_id"]
            if before and eid == before: break
            if r["event"] == "ENTRY":
                tk = next((x for x in T.get(eid, []) if x["kind"] == "ENTRY"), None)
                if tk: pos[code] = (tk["contract"], (1 if r["action"] == "BUY" else -1) * lots[code])
            else: pos.pop(code, None)
            if upto and eid == upto: break
        else:
            code = r[0]["event_id"].split("|")[0]
            if code in pos: pos[code] = (r[1]["contract"], pos[code][1])
    return {sym: q for sym, q in pos.values()}

def position_check(kite_net, expected):
    """Compare Kite's net positions with what the signals say we hold. kite_net: list of dicts (tradingsymbol, quantity). expected: {symbol: signed qty}.
    Returns a list of mismatch texts, empty when they agree. Futures symbols only."""
    got = {p["tradingsymbol"]: int(p["quantity"]) for p in kite_net if p.get("tradingsymbol", "").endswith("FUT") and int(p["quantity"]) != 0}
    out = []
    for s in sorted(set(got) | set(expected)):
        if got.get(s, 0) != expected.get(s, 0): out.append("%s: Kite shows %d, signals say %d" % (s, got.get(s, 0), expected.get(s, 0)))
    return out
