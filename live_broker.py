"""Live broker for order_exec.py. The ONLY file that can place real orders. It is imported only when order_exec.order_mode() returns "live", which needs
IBT_ORDER_MODE=live in the environment AND data/live/GO_LIVE containing today's date. In the rehearsal this file is never imported.

Same interface as ShadowBroker: ready, depth, place, poll, modify, cancel, positions. Product NRML (futures held overnight), regular variety, LIMIT orders only, DAY validity.
Every Kite error becomes a REJECTED order with the reason, never an exception into the order flow."""
import order_exec as X

class KiteBroker:
    mode = "live"
    def __init__(self, kite): self.kite = kite
    def ready(self):
        try: self.kite.profile(); return True, ""
        except Exception as e: return False, "Kite profile call failed (%s)" % type(e).__name__
    def depth(self, c): return X.depth(self.kite, c)
    def place(self, c, side, qty, limit, tag):
        o = X.Order(None, c, side, qty, limit); tg = ("ibt" + "".join(ch for ch in tag if ch.isalnum())[-14:])[:20]
        try:
            o.id = str(self.kite.place_order(variety="regular", exchange=c["ex"], tradingsymbol=c["symbol"], transaction_type=side, quantity=qty, product="NRML",
                                             order_type="LIMIT", price=limit, validity="DAY", tag=tg))
        except Exception as e:
            o.state = "REJECTED"; o.reason = "%s: %s" % (type(e).__name__, str(e)[:120])
            if not isinstance(e, (ValueError, TypeError)):          # the call may have reached Kite before it failed (timeout, dropped connection): look before anything else is sent
                try:
                    hit = [x for x in self.kite.orders() if x.get("tag") == tg]
                    if hit: o.id = str(hit[-1]["order_id"]); o.state = "OPEN"; o.reason = ""
                except Exception: o.reason = "UNKNOWN: place call failed and the order book could not be read; check Kite. " + o.reason
        return o
    def poll(self, o):
        if o.state not in ("OPEN",) or not o.id: return o
        try: h = self.kite.order_history(o.id)[-1]
        except Exception: return o              # a failed look is not a state change; the loop looks again
        st = str(h.get("status", "")).upper(); o.filled = int(h.get("filled_quantity") or 0)
        if o.filled: o.avg = float(h.get("average_price") or 0) or o.avg
        if st == "COMPLETE": o.state = "COMPLETE"; o.filled = o.qty
        elif st == "REJECTED": o.state = "REJECTED"; o.reason = str(h.get("status_message") or "")[:120]
        elif st == "CANCELLED": o.state = "CANCELLED"
        return o
    def modify(self, o, limit):
        try: self.kite.modify_order(variety="regular", order_id=o.id, price=limit); o.limit = limit
        except Exception as e: o.reason = "modify failed: %s" % type(e).__name__
    def cancel(self, o):
        if o.state == "OPEN" and o.id:
            try: self.kite.cancel_order(variety="regular", order_id=o.id)
            except Exception as e: o.reason = "cancel failed: %s" % type(e).__name__
    def positions(self):
        net = self.kite.positions().get("net", [])
        return {p["tradingsymbol"]: int(p["quantity"]) for p in net if str(p.get("tradingsymbol", "")).endswith("FUT") and int(p["quantity"]) != 0}
