"""Order tickets, the expiry-week rule and the roll alert for watcher.py. Pure logic plus two read-only Kite calls (instruments, quote). Places no orders.

Rules (backtested 2026-11-09, see expiry_backtest.py):
  1. No new entry on the last 2 trading sessions up to and including expiry day. The alert is logged as "not sent, expiry week".
  2. A trade entered in the last 4 calendar days before expiry goes on the next-month contract (same as the dashboard's contract()).
  3. A trade still open on the session before expiry gets a ROLL alert at 14:45: close the near month, open the next month, same side and lots.
Limit price = best ask plus an allowance (BUY) or best bid minus an allowance (SELL), rounded to the contract tick. A marketable limit, never a market order."""
import math, datetime as dt

BLOCK_SESSIONS = 2          # entry blocked on this many sessions up to and including expiry day
NEXT_MONTH_DAYS = 4         # an entry this close to expiry uses the next-month contract
SLIP_PCT = 0.0005           # 0.05 percent allowance on the limit price
ROLL_FROM = "14:45"
LOTS_DEFAULT = 1
HDR_T = ["event_id", "logged_at_ist", "kind", "index", "side", "contract", "expiry", "lots", "qty", "bid", "ask", "last", "limit", "stop", "seconds_after_candle", "quote_status", "note"]

def load_futures(kite, L):
    """{index: [dict(symbol, token, expiry, lot, tick)] sorted by expiry}. Empty on any failure."""
    out = {}
    try:
        for ex in ("NFO", "BFO"):
            for ins in kite.instruments(ex):
                for nm in L.FUT_EX:
                    if L.FUT_EX[nm] == ex and ins.get("name") == L.FUT_NAME[nm] and ins.get("instrument_type") == "FUT":
                        out.setdefault(nm, []).append(dict(symbol=ins["tradingsymbol"], token=ins["instrument_token"], expiry=str(ins["expiry"])[:10],
                                                          lot=ins.get("lot_size"), tick=float(ins.get("tick_size") or 0.05), ex=ex))
    except Exception:
        return {}
    for v in out.values(): v.sort(key=lambda f: f["expiry"])
    return out

def sessions(a, b, holidays):
    """Trading sessions from day a to day b inclusive (weekdays that are not listed holidays)."""
    d = dt.date.fromisoformat(a); e = dt.date.fromisoformat(b); n = 0
    while d <= e:
        if d.weekday() < 5 and d.isoformat() not in holidays: n += 1
        d += dt.timedelta(1)
    return n

def calendar_expiry(D, nm, day, holidays):
    """Fallback when Kite gives no contract list: last expiry weekday of the month, moved back past holidays."""
    d = dt.date.fromisoformat(day); y, m = d.year, d.month
    for _ in range(2):
        e = D.last_weekday(y, m, D.EXP_WD[nm])
        while e.weekday() >= 5 or e.isoformat() in holidays: e -= dt.timedelta(1)
        if e >= d: return e.isoformat()
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

def near_expiry(D, nm, day, futs, holidays):
    for f in futs.get(nm, []):
        if f["expiry"] >= day: return f["expiry"]
    return calendar_expiry(D, nm, day, holidays)

def entry_blocked(D, nm, day, futs, holidays):
    """True when `day` is one of the last BLOCK_SESSIONS sessions up to and including the expiry of the near-month contract."""
    e = near_expiry(D, nm, day, futs, holidays)
    return bool(e) and sessions(day, e, holidays) <= BLOCK_SESSIONS

def contract_for(nm, day, futs, holidays, D):
    """Contract to trade for an entry made on `day`: near month, or next month when within NEXT_MONTH_DAYS of expiry. None if Kite gave no list."""
    fl = futs.get(nm, [])
    live = [f for f in fl if f["expiry"] >= day]
    if not live: return None
    c = live[0]
    if (dt.date.fromisoformat(c["expiry"]) - dt.date.fromisoformat(day)).days <= NEXT_MONTH_DAYS and len(live) > 1: c = live[1]
    return c

def roll_day(expiry, holidays):
    """The session before expiry day."""
    d = dt.date.fromisoformat(expiry) - dt.timedelta(1)
    while d.weekday() >= 5 or d.isoformat() in holidays: d -= dt.timedelta(1)
    return d.isoformat()

def held_contract(nm, entry_day, today, futs, holidays, D):
    """Contract a position opened on entry_day is in on `today`: the entry contract, or the next month once the roll day has passed."""
    c = contract_for(nm, entry_day, futs, holidays, D)
    if not c: return None
    if today >= roll_day(c["expiry"], holidays):
        later = [f for f in futs[nm] if f["expiry"] > c["expiry"]]
        # on the roll day itself the near contract is still held until the roll alert is done; after it, the next month
        if today > roll_day(c["expiry"], holidays) and later: return later[0]
    return c

def tick_round(x, tick, up):
    n = x / tick; r = round(n)
    k = r if abs(n - r) < 1e-6 else (math.ceil(n) if up else math.floor(n))
    return round(k * tick, 2)

def limit_price(side, bid, ask, tick):
    """BUY: ask plus allowance, rounded up. SELL: bid minus allowance, rounded down."""
    if side == "BUY": return tick_round(ask * (1 + SLIP_PCT), tick, True)
    return tick_round(bid * (1 - SLIP_PCT), tick, False)

def get_quote(kite, c):
    """(bid, ask, last, status). Status 'ok' or the reason it is not."""
    key = "%s:%s" % (c["ex"], c["symbol"])
    try:
        q = kite.quote([key])[key]; d = q.get("depth") or {}
        bid = (d.get("buy") or [{}])[0].get("price"); ask = (d.get("sell") or [{}])[0].get("price"); last = q.get("last_price")
        if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid: return None, None, last, "no usable bid/ask"
        return float(bid), float(ask), last, "ok"
    except Exception as e:
        return None, None, None, "quote failed (%s)" % type(e).__name__

def build(kind, nm, name, side, c, lots, lot_size, quote, stop_text, lag_seconds, eid, t):
    """Ticket dict for tickets.csv and the text lines for the alert. c is a contract dict or None."""
    bid, ask, last, status = quote if quote else (None, None, None, "not requested")
    lim = limit_price(side, bid, ask, c["tick"]) if (c and status == "ok") else None
    qty = lots * (c["lot"] if c and c.get("lot") else lot_size)
    row = dict(event_id=eid, logged_at_ist=t.strftime("%Y-%m-%d %H:%M:%S"), kind=kind, index=name, side=side, contract=c["symbol"] if c else "unknown",
               expiry=c["expiry"] if c else "", lots=lots, qty=qty, bid=bid or "", ask=ask or "", last=last or "", limit=lim or "", stop=stop_text,
               seconds_after_candle=int(lag_seconds) if lag_seconds is not None else "", quote_status=status, note="")
    lines = ["Order ticket (paper signal, place it yourself):"]
    if c: lines.append("  Contract: %s, expires %s" % (c["symbol"], c["expiry"]))
    else: lines.append("  Contract: not available from Kite, pick the matching month yourself")
    lines.append("  %s %d lot (%d units)" % (side, lots, qty))
    if lim: lines.append("  Limit %.2f  (bid %.2f, ask %.2f; marketable limit, not a market order)" % (lim, bid, ask))
    else: lines.append("  Limit: no live quote (%s). Check the screen before ordering." % status)
    lines.append("  Stop: " + stop_text)
    if lag_seconds is not None: lines.append("  Candle closed %d seconds ago" % lag_seconds)
    return row, lines
