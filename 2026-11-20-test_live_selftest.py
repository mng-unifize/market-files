"""Tests for 2026-11-20-live_selftest.py with a fake broker. Run: python3 2026-11-20-test_live_selftest.py  (no network, no orders, temp files only)"""
import os, sys, tempfile, importlib.util, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
spec = importlib.util.spec_from_file_location("selftest", os.path.join(HERE, "2026-11-20-live_selftest.py")); ST = importlib.util.module_from_spec(spec); spec.loader.exec_module(ST)
import safety as S, order_exec as X, csv

C = dict(symbol="NIFTY26DECFUT", ex="NFO", tick=0.05)
class FB:
    def __init__(self, mode="live", sell_fills=True, buy_fills=True, pos=None, bid=25400.0, ask=25402.0):
        self.mode = mode; self.sell_fills = sell_fills; self.buy_fills = buy_fills; self.pos = dict(pos or {}); self.bid = bid; self.ask = ask; self.placed = 0
    def ready(self): return True, ""
    def depth(self, c): return [(self.bid, 500)], [(self.ask, 500)], (self.bid + self.ask) / 2
    def place(self, c, side, qty, limit, tag): self.placed += 1; return X.Order("T%d" % self.placed, c, side, qty, limit)
    def poll(self, o):
        if o.state != "OPEN": return o
        if (o.side == "BUY" and self.buy_fills) or (o.side == "SELL" and self.sell_fills):
            o.state = "COMPLETE"; o.filled = o.qty; o.avg = self.ask if o.side == "BUY" else self.bid
            self.pos[o.c["symbol"]] = self.pos.get(o.c["symbol"], 0) + (o.qty if o.side == "BUY" else -o.qty)
        return o
    def modify(self, o, limit): o.limit = limit
    def cancel(self, o):
        if o.state == "OPEN": o.state = "CANCELLED"
    def positions(self): return {k: v for k, v in self.pos.items() if v}

def setup():
    d = tempfile.mkdtemp(); S.STOP_FILE = os.path.join(d, "STOP")
    t = [0.0]; sleep = lambda s: t.__setitem__(0, t[0] + s); clock = lambda: t[0]; msgs = []
    return d, os.path.join(d, "selftest.csv"), os.path.join(d, "orders.csv"), clock, sleep, (lambda ti, bo, pr="default": msgs.append((ti, bo, pr))), msgs

def go(broker, hm="11:00", free=500000, again=False, **kw):
    d, out, orders, clock, sleep, notify, msgs = setup(); now = lambda: dt.datetime(2026, 11, 24, int(hm[:2]), int(hm[3:]), 0)
    if kw.get("stop"): open(S.STOP_FILE, "w").write("x")
    if kw.get("prior"): ST.write(dict(run_at_ist="2026-11-24 10:30:00", mode="live", leg="SUMMARY", result="OK"), out)
    r = ST.run(broker, C, now, clock, sleep, notify, 60, free, again, out, None, orders)
    return r, broker, out, msgs

def test_happy_path():
    r, b, out, msgs = go(FB())
    assert r["result"] == "OK", r; assert b.positions() == {}; assert not os.path.exists(S.STOP_FILE)
    rows = list(csv.DictReader(open(out))); assert [x["leg"] for x in rows] == ["BUY", "SELL", "SUMMARY"] and rows[-1]["result"] == "OK"
    assert rows[0]["fill_price"] == "25402.0" and rows[1]["fill_price"] == "25400.0" and rows[-1]["position_after"] == "0"
    assert float(rows[-1]["round_trip_gross"]) == -130 and rows[-1]["round_trip_charges"] != ""      # -2 points x 65, charges computed
    assert any("SELF-TEST OK" in m[0] for m in msgs)

def test_sell_never_fills_latches_stop():
    r, b, out, msgs = go(FB(sell_fills=False))
    assert r["result"] == "STOPPED"; assert os.path.exists(S.STOP_FILE)
    hi = [m for m in msgs if m[2] == "high"]; assert hi and "HELD" in hi[0][1] and "SELL it by hand" in hi[0][1]
    assert b.positions() == {"NIFTY26DECFUT": 65}      # the test never hides a held position

def test_buy_not_filled_is_clean():
    r, b, out, msgs = go(FB(buy_fills=False)); assert r["result"] == "NOT_FILLED" and not os.path.exists(S.STOP_FILE) and b.positions() == {}

def test_refusals_send_nothing():
    for kw, hm, free in [({}, "09:30", 500000), ({}, "14:45", 500000), ({"stop": True}, "11:00", 500000), ({}, "11:00", 100000)]:
        r, b, out, msgs = go(FB(), hm, free, **kw); assert r["result"] == "REFUSED" and b.placed == 0, (kw, hm, free, r)
    r, b, out, msgs = go(FB(pos={"NIFTY26DECFUT": 65})); assert r["result"] == "REFUSED" and b.placed == 0
    r, b, out, msgs = go(FB(bid=25400.0, ask=25600.0)); assert r["result"] == "REFUSED" and b.placed == 0     # abnormal spread

def test_second_live_run_needs_again():
    r, b, out, msgs = go(FB(), prior=True); assert r["result"] == "REFUSED" and b.placed == 0
    r, b, out, msgs = go(FB(), prior=True, again=True); assert r["result"] == "OK"

def test_dry_run_never_latches_stop():
    r, b, out, msgs = go(FB(mode="shadow", sell_fills=False)); assert r["result"] == "STOPPED" and not os.path.exists(S.STOP_FILE)

if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"): v(); n += 1; print("ok", k)
    print(n, "tests passed")
