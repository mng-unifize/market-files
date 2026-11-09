"""Fill logger (step 4). READ ONLY: it calls Kite's trades() and orders() lists and nothing else. It never places, changes or cancels an order.

Matches the futures trades you placed by hand (Kite trade book) to the order tickets the watcher sent (data/live/tickets.csv), and writes one row per order to
data/live/fills.csv: intended price band vs the price you got, slippage against the quote midpoint in percent (positive = it cost you), and the delays.
The paper model assumes 0.01 percent a side. The report says whether real fills are above or below that.

  python fill_logger.py --today [--notify]          log today's fills (the Mac runs this at 17:50 on weekdays)
  python fill_logger.py --day 2026-11-12            log an earlier day (needs a Kite token valid for the day you run it; Kite keeps only the current day's trade book)
  python fill_logger.py --report                    summary of everything in fills.csv, no Kite call"""
import os, sys, csv, datetime as dt, collections
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE)
import watcher
TICKETS = watcher.TICKETS; FILLS = "data/live/fills.csv"; ASSUMED_PCT = 0.01; ALERT_PCT = 0.02
MATCH_WINDOW_S = 45 * 60
HDR_F = ["key", "day", "order_time", "fill_time", "contract", "side", "qty", "avg_price", "ticket_event", "ticket_kind", "ticket_time", "bid", "ask", "mid", "limit", "slippage_pct", "slippage_rs", "ticket_to_order_s", "order_to_fill_s", "status"]

def ts(x):
    if x in (None, ""): return None
    if isinstance(x, dt.datetime): return x.replace(tzinfo=None)
    return dt.datetime.fromisoformat(str(x)[:19].replace("T", " "))

def aggregate(trades, orders):
    """Kite trade rows (one per partial fill) -> one dict per order, volume weighted price."""
    by = collections.OrderedDict(); omap = {o.get("order_id"): o for o in (orders or [])}
    for r in trades:
        k = r.get("order_id"); g = by.setdefault(k, dict(order_id=k, contract=r["tradingsymbol"], side=r["transaction_type"], qty=0, value=0.0, fill=None, order_time=None))
        q = int(r["quantity"]); g["qty"] += q; g["value"] += q * float(r["average_price"]); f = ts(r.get("fill_timestamp") or r.get("exchange_timestamp"))
        if f and (g["fill"] is None or f > g["fill"]): g["fill"] = f
        g["order_time"] = ts(r.get("order_timestamp")) or ts((omap.get(k) or {}).get("order_timestamp")) or g["order_time"]
        g["limit"] = (omap.get(k) or {}).get("price")
    out = []
    for g in by.values():
        g["avg"] = g["value"] / g["qty"] if g["qty"] else 0.0; g["order_time"] = g["order_time"] or g["fill"]; out.append(g)
    return sorted(out, key=lambda g: g["order_time"] or dt.datetime.min)

def load_tickets(day):
    if not os.path.exists(TICKETS): return []
    with open(TICKETS, newline="") as f: return [r for r in csv.DictReader(f) if r["logged_at_ist"][:10] == day]

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def match(fills, tickets):
    """One to one: each fill takes the earliest unused ticket with the same contract and side that was sent up to 45 minutes before the order."""
    used = set(); rows = []
    for g in fills:
        hit = None
        for i, tk in enumerate(tickets):
            if i in used or tk["contract"] != g["contract"] or tk["side"] != g["side"]: continue
            gap = (g["order_time"] - ts(tk["logged_at_ist"])).total_seconds()
            if 0 <= gap <= MATCH_WINDOW_S: hit = i; break
        if hit is not None: used.add(hit)
        rows.append((g, tickets[hit] if hit is not None else None))
    return rows, [tk for i, tk in enumerate(tickets) if i not in used]

def row_for(day, g, tk):
    sign = 1 if g["side"] == "BUY" else -1; base = dict(key="O|%s" % g["order_id"], day=day, order_time=g["order_time"], fill_time=g["fill"], contract=g["contract"], side=g["side"], qty=g["qty"], avg_price=round(g["avg"], 2))
    if not tk: return dict(base, status="no ticket (manual or untracked)")
    bid, ask = fnum(tk["bid"]), fnum(tk["ask"]); mid = (bid + ask) / 2 if bid and ask else None
    sl = sl_rs = None
    if mid: sl = sign * (g["avg"] - mid) / mid * 100; sl_rs = sign * (g["avg"] - mid) * g["qty"]
    one = fnum(tk["qty"]); status = "matched" if one and g["qty"] % int(one) == 0 else "matched, quantity differs from ticket (%s)" % tk["qty"]
    return dict(base, ticket_event=tk["event_id"], ticket_kind=tk["kind"], ticket_time=tk["logged_at_ist"], bid=bid or "", ask=ask or "", mid=round(mid, 2) if mid else "", limit=tk["limit"],
                slippage_pct=round(sl, 4) if sl is not None else "", slippage_rs=round(sl_rs) if sl_rs is not None else "",
                ticket_to_order_s=int((g["order_time"] - ts(tk["logged_at_ist"])).total_seconds()), order_to_fill_s=int((g["fill"] - g["order_time"]).total_seconds()) if g["fill"] else "", status=status)

def missed_row(day, tk): return dict(key="T|%s|%s|%s" % (tk["event_id"], tk["contract"], tk["side"]), day=day, contract=tk["contract"], side=tk["side"], qty=0, ticket_event=tk["event_id"], ticket_kind=tk["kind"], ticket_time=tk["logged_at_ist"], bid=tk["bid"], ask=tk["ask"], limit=tk["limit"], status="ticket not traded")

def existing_keys():
    if not os.path.exists(FILLS): return set()
    with open(FILLS, newline="") as f: return {r["key"] for r in csv.DictReader(f)}

def write_rows(rows):
    new = [r for r in rows if r["key"] not in existing_keys()]
    if not new: return 0
    first = not os.path.exists(FILLS); os.makedirs(os.path.dirname(FILLS), exist_ok=True)
    with open(FILLS, "a", newline="") as f:
        w = csv.DictWriter(f, HDR_F)
        if first: w.writeheader()
        for r in new: w.writerow({k: r.get(k, "") for k in HDR_F})
    return len(new)

def log_day(kite, day):
    """Returns (rows written, one line summary). Only trades() and orders() are called on kite."""
    trades = [t for t in kite.trades() if (ts(t.get("fill_timestamp") or t.get("exchange_timestamp")) or dt.datetime.min).strftime("%Y-%m-%d") == day]
    fills = aggregate(trades, kite.orders() if trades else []); pairs, missed = match(fills, load_tickets(day))
    rows = [row_for(day, g, tk) for g, tk in pairs] + [missed_row(day, tk) for tk in missed]
    n = write_rows(rows); m = [r for r in rows if r["status"].startswith("matched") and r.get("slippage_pct") != ""]
    avg = sum(float(r["slippage_pct"]) for r in m) / len(m) if m else None
    s = "Fills %s: %d orders (%d matched to a ticket, %d with no ticket), %d tickets not traded" % (day, len(fills), len(pairs) - sum(1 for _, t in pairs if not t), sum(1 for _, t in pairs if not t), len(missed))
    if avg is not None: s += "; average slippage %.3f%% a side (model assumes %.2f%%)" % (avg, ASSUMED_PCT)
    return n, s, bool(fills or missed)

def report():
    if not os.path.exists(FILLS): print("No fills logged yet."); return 0
    with open(FILLS, newline="") as f: R = list(csv.DictReader(f))
    m = [r for r in R if r["status"].startswith("matched") and r["slippage_pct"] != ""]; none = [r for r in R if r["status"].startswith("no ticket")]; miss = [r for r in R if r["status"] == "ticket not traded"]
    print("Fills logged: %d matched, %d without a ticket, %d tickets not traded." % (len(m), len(none), len(miss)))
    if not m: return 0
    sl = sorted(float(r["slippage_pct"]) for r in m); avg = sum(sl) / len(sl); med = sl[len(sl) // 2]
    print("Slippage per side vs quote midpoint: average %.3f%%, median %.3f%%, worst %.3f%% (model assumes %.2f%%)." % (avg, med, sl[-1], ASSUMED_PCT))
    print("Total slippage cost: Rs %s over %d fills." % (format(sum(int(float(r["slippage_rs"])) for r in m), ","), len(m)))
    d = [float(r["ticket_to_order_s"]) for r in m]; print("Seconds from alert to your order: median %d, worst %d." % (sorted(d)[len(d) // 2], max(d)))
    print("VERDICT: " + ("real slippage is ABOVE the %.2f%% alert level, re-run futures_paper.py with --slip %.5f before trusting the edge." % (ALERT_PCT, avg / 100) if avg > ALERT_PCT else
          "real slippage is above the model's %.2f%% but under %.2f%%; edge shrinks, re-run futures_paper.py --slip %.5f." % (ASSUMED_PCT, ALERT_PCT, avg / 100) if avg > ASSUMED_PCT else "real slippage is at or below the model's assumption."))
    return 0

def main(a):
    if "--report" in a: return report()
    day = a[a.index("--day") + 1] if "--day" in a else None
    if "--today" in a or day is None: day = watcher.RealClock().now().strftime("%Y-%m-%d") if hasattr(watcher, "RealClock") else dt.date.today().isoformat()
    if not watcher.load_env(("KITE_API_KEY",)): return 2
    token, why = watcher.load_token(watcher.RealClock().now().strftime("%Y-%m-%d"))
    if not token: print("No Kite token for today (%s). Run login.py." % why); return 2
    n, s, any_activity = log_day(watcher.make_kite(token), day); print(s + " | rows written: %d" % n)
    if "--notify" in a and any_activity and watcher.load_env(("NTFY_TOPIC",)): watcher.ntfy("Fill log " + day, s, priority="default")
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
