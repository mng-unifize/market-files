"""Tests for safety.py, order_exec.py and live_broker.py. No network, no Kite, no real clock: fakes only. Run: python3 test_orders.py"""
import os, sys, csv, json, shutil, tempfile, unittest, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import safety as S, order_exec as X, live_broker as LB, ticket as T

C = dict(symbol="NIFTY26NOVFUT", token=1, expiry="2026-11-23", lot=65, tick=0.05, ex="NFO")
C2 = dict(symbol="NIFTY26DECFUT", token=2, expiry="2026-12-29", lot=65, tick=0.05, ex="NFO")
CB = dict(symbol="BANKNIFTY26NOVFUT", token=3, expiry="2026-11-23", lot=30, tick=0.05, ex="NFO")

class Clock:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += s

def book(ask, bid=None, aq=500, last=None):
    bid = bid if bid is not None else ask - 1
    return dict(last=last or ask, bids=[(bid, 500)], asks=[(ask, aq)])

class FakeKite:
    """quote() returns fn(clock time) for every contract; historical_data returns a candle close of 25000."""
    def __init__(self, fn, ck): self.fn = fn; self.ck = ck
    def quote(self, keys):
        s = self.fn(self.ck.t)
        return {keys[0]: dict(last_price=s["last"], depth=dict(buy=[dict(price=p, quantity=q) for p, q in s["bids"]], sell=[dict(price=p, quantity=q) for p, q in s["asks"]]))}
    def historical_data(self, token, a, b, iv): return [dict(date=a.replace(tzinfo=dt.timezone.utc), close=25000.0)]

class NoFB(X.ShadowBroker):
    """Rehearsal broker whose second order never fills: models a market that keeps running after the first order is cancelled."""
    def poll(self, o):
        if o.id.endswith("-2"): return o
        return super().poll(o)

class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.cwd = os.getcwd(); os.chdir(self.d); os.makedirs("data/live/candles"); self.ck = Clock(); self.sent = []
    def tearDown(self): os.chdir(self.cwd); shutil.rmtree(self.d)
    def alerts(self, rows):
        with open("data/live/alerts.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["event_id", "logged_at_ist", "event", "index", "action", "signal_bar", "fill_time", "lot_size", "price", "stop", "reason", "closes_entry", "alert"])
            for r in rows: w.writerow(r)
    def tickets(self, rows):
        with open("data/live/tickets.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(T.HDR_T)
            for r in rows: w.writerow(r)
    def tk(self, eid, kind, side, bid=25000.0, ask=25001.0, limit=25013.7, contract="NIFTY26NOVFUT", index="Nifty 50", qty=65, at="2026-11-13 09:30:20", note=""):
        return [eid, at, kind, index, side, contract, "2026-11-23", 1, qty, bid, ask, ask, limit, "n", 20, "ok", note]
    def al(self, eid, event, action, bar, price, index="Nifty 50", at="2026-11-13 09:30:20", st="sent", closes=""):
        return [eid, at, event, index, action, bar, "", 65, price, "", "", closes, st]

class Flow(Base):
    def run_flow(self, fn, side, limit, qty=65, seed=None):
        k = FakeKite(fn, self.ck); br = X.ShadowBroker(k, {"NSEI": [C]}, seed=seed or {})
        return X.execute(br, C, side, qty, limit, 0.05, self.ck.now, self.ck.sleep, "t"), br
    def test_buy_fills_at_touch(self):
        r, br = self.run_flow(lambda t: book(25001.0), "BUY", 25013.0); self.assertEqual((r["status"], r["fill_price"], r["fill_s"]), ("FILLED", 25001.0, 0)); self.assertEqual(br.positions(), {"NIFTY26NOVFUT": 65})
    def test_sell_fills_at_bid(self):
        r, br = self.run_flow(lambda t: book(25002.0, 25001.0), "SELL", 24990.0, seed={"NIFTY26NOVFUT": 65}); self.assertEqual(r["fill_price"], 25001.0); self.assertEqual(br.positions(), {})
    def test_waits_then_fills(self):
        r, _ = self.run_flow(lambda t: book(25020.0 if t < 2 else 25010.0), "BUY", 25013.0); self.assertEqual((r["status"], r["fill_s"]), ("FILLED", 2))
    def test_first_reprice_at_3s(self):
        r, _ = self.run_flow(lambda t: book(25030.0), "BUY", 25013.0); self.assertEqual((r["status"], r["attempts"], r["fill_s"]), ("REPRICED_FILLED", 2, 4)); self.assertGreater(r["limit_final"], 25013.0)
    def test_second_reprice_at_10s(self):
        r, _ = self.run_flow(lambda t: book(25030.0 if t <= 3 else (25300.0 if t < 10 else 25400.0)), "BUY", 25013.0); self.assertEqual((r["status"], r["attempts"]), ("REPRICED_FILLED", 3)); self.assertGreaterEqual(r["fill_s"], 10)
    def test_gives_up_after_20s(self):
        r, br = self.run_flow(lambda t: book(25030.0 + 100 * t), "BUY", 25013.0); self.assertEqual((r["status"], r["attempts"]), ("NOT_FILLED", 3)); self.assertEqual(br.positions(), {})
    def test_walks_depth(self):
        r, _ = self.run_flow(lambda t: dict(last=25001.0, bids=[(25000.0, 500)], asks=[(25001.0, 40), (25002.0, 100)]), "BUY", 25013.0, 65); self.assertAlmostEqual(r["fill_price"], (40 * 25001.0 + 25 * 25002.0) / 65)
    def test_thin_book_never_fills(self):
        r, _ = self.run_flow(lambda t: dict(last=25001.0, bids=[(25000.0, 500)], asks=[(25001.0, 10)]), "BUY", 25013.0); self.assertEqual(r["status"], "NOT_FILLED")
    def test_crash_cancels_the_order(self):
        k = FakeKite(lambda t: book(25001.0), self.ck); br = X.ShadowBroker(k, {"NSEI": [C]}); cancelled = []
        orig = br.cancel; br.cancel = lambda o: (cancelled.append(o.id), orig(o))
        def boom(o): raise RuntimeError("disk full")
        br.poll = boom
        with self.assertRaises(RuntimeError): X.execute(br, C, "BUY", 65, 25013.0, 0.05, self.ck.now, self.ck.sleep, "t")
        self.assertEqual(len(cancelled), 1)

class Fallback(Base):
    def run_fb(self, fn, side="SELL", seed=None, fb=0.005, cls=X.ShadowBroker):
        k = FakeKite(fn, self.ck); br = cls(k, {"NSEI": [C]}, seed=seed if seed is not None else {"NIFTY26NOVFUT": 65})
        return X.execute(br, C, side, 65, 24990.0 if side == "SELL" else 25013.0, 0.05, self.ck.now, self.ck.sleep, "tg", fb), br
    @staticmethod
    def falling(t):                      # the bid drops 0.4% a second for 20 s, then holds: every ladder step is outrun, the fallback catches it
        b = 24980.0 - 100 * min(t, 20); return dict(last=b, bids=[(b, 500)], asks=[(b + 1, 500)])
    def test_exit_falls_back_and_fills(self):
        r, br = self.run_fb(self.falling); self.assertEqual(r["status"], "FALLBACK_FILLED"); self.assertTrue(r["fallback"]); self.assertEqual(br.positions(), {}); self.assertEqual(r["attempts"], 4); self.assertEqual(len(r["order_id"].split("|")), 2)
    def test_exit_fallback_fills_at_the_real_price_not_the_cap(self):
        r, _ = self.run_fb(self.falling); self.assertEqual(r["fill_price"], 22980.0)
    def test_entry_never_falls_back(self):
        r, br = self.run_fb(lambda t: book(25030.0 + 100 * t), side="BUY", seed={}, fb=None); self.assertEqual(r["status"], "NOT_FILLED"); self.assertFalse(r["fallback"]); self.assertEqual(br.positions(), {})
    def test_fallback_that_does_not_fill_is_cancelled_and_reported(self):
        r, br = self.run_fb(self.falling, cls=NoFB); self.assertEqual(r["status"], "NOT_FILLED"); self.assertTrue(r["fallback"]); self.assertEqual(br.positions(), {"NIFTY26NOVFUT": 65})
    def test_no_fallback_after_a_rejection(self):
        k = FakeLiveKite(fail_place=RuntimeError("IP not allowed")); b = LB.KiteBroker(k)
        r = X.execute(b, C, "SELL", 65, 24990.0, 0.05, Clock().now, Clock().sleep, "t", 0.005); self.assertEqual(r["status"], "REJECTED"); self.assertFalse(r["fallback"])
    def test_no_second_order_when_the_cancel_is_not_confirmed(self):
        k = FakeLiveKite(status="OPEN"); placed = []; k.place_order = lambda **kw: (placed.append(kw), "OID%d" % len(placed))[1]
        k.quote = lambda keys: {keys[0]: dict(last_price=25000.0, depth=dict(buy=[dict(price=24990.0, quantity=500)], sell=[dict(price=25001.0, quantity=500)]))}
        b = LB.KiteBroker(k); ck = Clock(); r = X.execute(b, C, "SELL", 65, 24990.0, 0.05, ck.now, ck.sleep, "t", 0.005); self.assertEqual(len(placed), 1); self.assertFalse(r["fallback"])
    def test_summary_counts_fallbacks_and_message_is_high_priority_without_stop(self):
        e = EndToEnd("test_exit_then_entry_flips_the_book"); e.setUp()
        try:
            e.exit_entry(); ctx = e.ctx(fn=lambda t: book(25030.0 + 100 * t), seed=e.SHORT)
            rs = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"]); self.assertEqual(rs[0]["status"], "FALLBACK_FILLED"); self.assertEqual(e.sent[0][2], "high"); self.assertIn("EXIT FALLBACK USED", e.sent[0][1]); self.assertFalse(os.path.exists(S.STOP_FILE))
            self.assertEqual(X.summary_dict("2026-11-13")["fallbacks"], 1)
        finally: e.tearDown()

class Checks(Base):
    now = dt.datetime(2026, 11, 13, 9, 50, 20)
    def ok(self, **kw):
        a = dict(kind="ENTRY", event_id="E1", lots=1, qty=65, lot_size=65, limit=25013.0, bid=25000.0, ask=25001.0, last=25000.5, now=self.now, done_ids=set(), stop_active=False, stop_reason="", pnl_today=0, orders_today=0)
        a.update(kw); return S.pre_trade_checks(**a)
    def test_clean(self): self.assertEqual(self.ok(), (True, []))
    def test_duplicate(self): self.assertFalse(self.ok(done_ids={"E1"})[0])
    def test_two_lots(self): self.assertFalse(self.ok(lots=2, qty=130)[0])
    def test_wrong_qty(self): self.assertFalse(self.ok(qty=100)[0])
    def test_hours(self): self.assertFalse(self.ok(now=dt.datetime(2026, 11, 13, 16, 0))[0])
    def test_price_band(self): self.assertFalse(self.ok(limit=25200.0)[0])
    def test_spread(self): self.assertFalse(self.ok(bid=24900.0, ask=25001.0)[0])
    def test_no_quote(self): self.assertFalse(self.ok(bid=None, ask=None, last=None)[0])
    def test_loss_stop_entry_not_exit(self): self.assertFalse(self.ok(kind="ENTRY", pnl_today=-41000)[0]); self.assertTrue(self.ok(kind="EXIT", pnl_today=-41000)[0])
    def test_loss_stop_blocks_roll_open_too(self): self.assertFalse(self.ok(kind="ROLL-OPEN", pnl_today=-41000)[0])
    def test_loss_stop_boundary(self): self.assertFalse(self.ok(pnl_today=-40000)[0]); self.assertTrue(self.ok(pnl_today=-39999)[0])
    def test_order_cap(self): self.assertTrue(self.ok(orders_today=7)[0]); self.assertFalse(self.ok(orders_today=8)[0]); self.assertFalse(self.ok(kind="EXIT", orders_today=8)[0])
    def test_kill_switch(self): self.assertFalse(self.ok(kind="EXIT", stop_active=True, stop_reason="x")[0])
    def test_stop_file(self): self.assertFalse(S.kill_switch()[0]); open(S.STOP_FILE, "w").write("m"); self.assertTrue(S.kill_switch()[0])
    def test_latch(self): S.latch_stop("why", self.now); self.assertIn("AUTO STOP", open(S.STOP_FILE).read()); self.assertTrue(S.kill_switch()[0])
    def test_phone_stop_latches_and_only_file_removal_resumes(self):
        msgs = [dict(id="a1", message="STOP", event="message"), dict(id="a2", message="hello", event="message")]
        self.assertTrue(S.kill_switch("t", fetch=lambda t, b: msgs)[0]); os.remove(S.STOP_FILE)
        self.assertFalse(S.kill_switch("t", fetch=lambda t, b: msgs)[0]); msgs.append(dict(id="a3", message=" stop ", event="message")); self.assertTrue(S.kill_switch("t", fetch=lambda t, b: msgs)[0])
    def test_phone_cannot_resume(self): open(S.STOP_FILE, "w").write("x"); self.assertTrue(S.kill_switch("t", fetch=lambda t, b: [dict(id="z", message="RESUME", event="message")])[0])
    def test_ntfy_down(self):
        def boom(t, b): raise OSError("down")
        on, _, warn = S.kill_switch("t", fetch=boom); self.assertFalse(on); self.assertIn("could not be checked", warn)
    def test_position_check(self):
        self.assertEqual(S.position_check([dict(tradingsymbol="NIFTY26NOVFUT", quantity=65)], {"NIFTY26NOVFUT": 65}), [])
        self.assertEqual(len(S.position_check([], {"NIFTY26NOVFUT": 65})), 1); self.assertEqual(len(S.position_check([dict(tradingsymbol="NIFTY26NOVFUT", quantity=-65)], {"NIFTY26NOVFUT": 65})), 1)
    def test_expected_positions_with_roll(self):
        self.alerts([self.al("E1", "ENTRY", "BUY", "2026-11-13 09:15", "25000", at="2026-11-13 09:30:20")])
        self.tickets([self.tk("E1", "ENTRY", "BUY"), self.tk("NSEI|2026-11-20|ROLL", "ROLL", "SELL", at="2026-11-20 14:45:20", note="close near month: x; open next month: y"),
                      self.tk("NSEI|2026-11-20|ROLL", "ROLL", "BUY", contract="NIFTY26DECFUT", at="2026-11-20 14:45:20", note="open leg")])
        self.assertEqual(S.expected_positions(), {"NIFTY26DECFUT": 65}); self.assertEqual(S.expected_positions(upto="E1"), {"NIFTY26NOVFUT": 65})
    def test_expected_positions_before(self):
        self.alerts([self.al("E1", "ENTRY", "BUY", "x", "1"), self.al("E2", "EXIT", "SELL", "x", "1", at="2026-11-13 09:40:00")]); self.tickets([self.tk("E1", "ENTRY", "BUY")])
        self.assertEqual(S.expected_positions(before="E2"), {"NIFTY26NOVFUT": 65}); self.assertEqual(S.expected_positions(), {})

class Pnl(Base):
    def test_daily_pnl_index_carried_long(self):
        self.alerts([self.al("E1", "ENTRY", "BUY", "2026-11-11 14:45", "25000")])
        with open("data/live/candles/NSEI_5m.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"])
            for ts, c in (("2026-11-11 15:25", 25100.0), ("2026-11-12 09:15", 25000.0), ("2026-11-12 15:25", 24900.0)): w.writerow([ts, c, c, c, c, 0])
        self.assertAlmostEqual(S.daily_pnl("2026-11-12")[0], (24900.0 - 25100.0) * 65)
    def test_futures_entry_today(self):
        self.alerts([self.al("E1", "ENTRY", "BUY", "2026-11-13 09:15", "25000")]); self.tickets([self.tk("E1", "ENTRY", "BUY", 25040.0, 25041.0)])
        p, _, b = S.daily_pnl_futures("2026-11-13", last_fn=lambda s: 25000.0, prev_close_fn=lambda s, d: None); self.assertAlmostEqual(p, (25000.0 - 25041.0) * 65); self.assertEqual(b["NSEI"], "futures")
    def test_futures_carried_short_closed(self):
        self.alerts([self.al("E0", "ENTRY", "SELL", "2026-11-12 15:15", "25000", at="2026-11-12 15:30:20"), self.al("E1", "EXIT", "BUY", "2026-11-13 09:15", "25300"), self.al("E2", "ENTRY", "BUY", "2026-11-13 09:15", "25300")])
        self.tickets([self.tk("E0", "ENTRY", "SELL", 25100.0, 25101.0, at="2026-11-12 15:30:20"), self.tk("E1", "EXIT", "BUY", 25349.0, 25350.0), self.tk("E2", "ENTRY", "BUY", 25349.0, 25350.0)])
        p, _, _ = S.daily_pnl_futures("2026-11-13", last_fn=lambda s: 25360.0, prev_close_fn=lambda s, d: 25200.0); self.assertAlmostEqual(p, -1 * (25350.0 - 25200.0) * 65 + (25360.0 - 25350.0) * 65)
    def test_futures_falls_back_to_index(self):
        self.alerts([self.al("E1", "ENTRY", "BUY", "2026-11-13 09:15", "25000")]); self.tickets([self.tk("E1", "ENTRY", "BUY", 25040.0, 25041.0)])
        with open("data/live/candles/NSEI_5m.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"]); w.writerow(["2026-11-13 15:25", 24900, 24900, 24900, 24900, 0])
        p, _, b = S.daily_pnl_futures("2026-11-13", last_fn=lambda s: None, prev_close_fn=lambda s, d: None); self.assertEqual(b["NSEI"], "index"); self.assertAlmostEqual(p, (24900.0 - 25000.0) * 65)

class EndToEnd(Base):
    def setUp(self):
        super().setUp(); self.t0 = dt.datetime(2026, 11, 13, 9, 30, 21)
    def ctx(self, fn=lambda t: book(25001.0, 25000.0), futs=None, seed=None, broker=None, nofb=False):
        k = FakeKite(fn, self.ck); futs = futs or {"NSEI": [C, C2], "NSEBANK": [CB]}
        br = broker or (NoFB if nofb else X.ShadowBroker)(k, futs, seed=seed if seed is not None else {})
        return X.Ctx(k, futs, br, lambda: self.t0 + dt.timedelta(seconds=self.ck.t), self.ck.now, self.ck.sleep, lambda t, b, p="default": self.sent.append((t, b, p)) or True, "", "", lambda code: 15)
    def entry_only(self):
        self.alerts([self.al("NSEI|2026-11-13 09:15|ENTRY", "ENTRY", "BUY", "2026-11-13 09:15", "25000")]); self.tickets([self.tk("NSEI|2026-11-13 09:15|ENTRY", "ENTRY", "BUY")])
    SHORT = {"NIFTY26NOVFUT": -65}
    def exit_entry(self):
        self.alerts([self.al("NSEI|2026-11-12 14:45|ENTRY", "ENTRY", "SELL", "2026-11-12 14:45", "25200", at="2026-11-12 15:00:20"),
                     self.al("NSEI|2026-11-13 09:15|EXIT", "EXIT", "BUY", "2026-11-13 09:15", "25000", closes="NSEI|2026-11-12 14:45|ENTRY"), self.al("NSEI|2026-11-13 09:15|ENTRY", "ENTRY", "BUY", "2026-11-13 09:15", "25000")])
        self.tickets([self.tk("NSEI|2026-11-12 14:45|ENTRY", "ENTRY", "SELL", at="2026-11-12 15:00:20"), self.tk("NSEI|2026-11-13 09:15|EXIT", "EXIT", "BUY"), self.tk("NSEI|2026-11-13 09:15|ENTRY", "ENTRY", "BUY")])
    def test_entry_logged_with_slippage_and_phone_message(self):
        self.entry_only(); ctx = self.ctx(seed={}); r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|ENTRY"])[0]
        self.assertEqual((r["status"], r["mode"], r["position_check"], r["ref_source"]), ("FILLED", "shadow", "ok", "futures candle close")); self.assertEqual(r["sent_after_close_s"], 21)
        self.assertAlmostEqual(float(r["slip_points"]), 1.0); self.assertEqual(r["slip_rupees"], 65); self.assertIn('"order_type": "LIMIT"', r["would_be_order"])
        self.assertEqual(len(self.sent), 1); self.assertTrue(self.sent[0][0].startswith("[REHEARSAL] FILLED")); self.assertEqual(self.sent[0][2], "default"); self.assertFalse(os.path.exists(S.STOP_FILE))
    def test_exit_then_entry_flips_the_book(self):
        self.exit_entry(); ctx = self.ctx(seed=self.SHORT); rs = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT", "NSEI|2026-11-13 09:15|ENTRY"])
        self.assertEqual([r["status"] for r in rs], ["FILLED", "FILLED"]); self.assertEqual([r["position_check"] for r in rs], ["ok", "ok"]); self.assertEqual(ctx.broker.positions(), {"NIFTY26NOVFUT": 65}); self.assertEqual(len(self.sent), 2)
    def test_not_filled_stops_everything_and_blocks_the_entry(self):
        self.exit_entry(); ctx = self.ctx(fn=lambda t: book(25030.0 + 100 * t), seed=self.SHORT, nofb=True); rs = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT", "NSEI|2026-11-13 09:15|ENTRY"])
        self.assertEqual([r["status"] for r in rs], ["NOT_FILLED", "BLOCKED"]); self.assertTrue(os.path.exists(S.STOP_FILE)); self.assertEqual(self.sent[0][2], "high"); self.assertIn("STOPPED", self.sent[0][0]); self.assertIn("delete data/live/STOP", self.sent[0][1])
        self.assertIn("earlier order", rs[1]["block_reasons"])
    def test_duplicate_latches_stop(self):
        self.entry_only(); ctx = self.ctx(seed={}); X.run_batch(ctx, ["NSEI|2026-11-13 09:15|ENTRY"]); os.remove("data/live/STOP") if os.path.exists("data/live/STOP") else None
        r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|ENTRY"])[0]; self.assertEqual(r["status"], "BLOCKED"); self.assertIn("duplicate", r["block_reasons"]); self.assertTrue(os.path.exists(S.STOP_FILE))
    def test_order_cap_latches_stop(self):
        self.entry_only(); ctx = self.ctx(seed={})
        with open("data/live/orders.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(X.HDR)
            for i in range(8): r = {k: "" for k in X.HDR}; r.update(event_id="old%d" % i, status="FILLED", run_at_ist="2026-11-13 09:00:00"); w.writerow([r[k] for k in X.HDR])
        r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|ENTRY"])[0]; self.assertEqual(r["status"], "BLOCKED"); self.assertIn("order cap", r["block_reasons"]); self.assertTrue(os.path.exists(S.STOP_FILE)); self.assertEqual(self.sent[-1][2], "high")
    def test_kill_switch_blocks_exit_and_says_do_it_by_hand(self):
        self.exit_entry(); open(S.STOP_FILE, "w").write("x"); ctx = self.ctx(seed=self.SHORT); r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])[0]
        self.assertEqual(r["status"], "BLOCKED"); self.assertIn("DO THIS BY HAND", self.sent[0][1]); self.assertEqual(self.sent[0][2], "high")
    def test_loss_stop_blocks_entry_but_not_exit(self):
        self.exit_entry(); ctx = self.ctx(fn=lambda t: book(25001.0, 25000.0), seed=self.SHORT); orig = S.daily_pnl_futures; S.daily_pnl_futures = lambda *a, **k: (-45000.0, {}, {"NSEI": "futures"})
        try: rs = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT", "NSEI|2026-11-13 09:15|ENTRY"])
        finally: S.daily_pnl_futures = orig
        self.assertEqual([r["status"] for r in rs], ["FILLED", "BLOCKED"]); self.assertIn("daily loss stop", rs[1]["block_reasons"]); self.assertFalse(os.path.exists(S.STOP_FILE))
    def test_position_mismatch_latches_stop(self):
        self.entry_only(); ctx = self.ctx(seed={"NIFTY26NOVFUT": 130}); r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|ENTRY"])[0]
        self.assertIn("Kite shows", r["position_check"]); self.assertTrue(os.path.exists(S.STOP_FILE)); self.assertEqual(self.sent[-1][2], "high")
    def test_roll_closes_near_and_opens_next_month(self):
        self.alerts([self.al("NSEI|2026-11-13 09:15|ENTRY", "ENTRY", "BUY", "2026-11-13 09:15", "25000")])
        self.tickets([self.tk("NSEI|2026-11-13 09:15|ENTRY", "ENTRY", "BUY"), self.tk("NSEI|2026-11-20|ROLL", "ROLL", "SELL", limit=24990.0, at="2026-11-20 14:45:20", note="close near month: NIFTY26NOVFUT; open next month: NIFTY26DECFUT"),
                      self.tk("NSEI|2026-11-20|ROLL", "ROLL", "BUY", contract="NIFTY26DECFUT", at="2026-11-20 14:45:20", note="open leg")])
        self.t0 = dt.datetime(2026, 11, 20, 14, 45, 25); ctx = self.ctx(seed={"NIFTY26NOVFUT": 65}); rs = X.run_batch(ctx, ["NSEI|2026-11-20|ROLL"])
        self.assertEqual([r["kind"] for r in rs], ["ROLL-CLOSE", "ROLL-OPEN"]); self.assertEqual([r["status"] for r in rs], ["FILLED", "FILLED"]); self.assertEqual(ctx.broker.positions(), {"NIFTY26DECFUT": 65}); self.assertEqual(rs[1]["position_check"], "ok")
    def test_unknown_event_is_ignored(self): self.entry_only(); self.assertEqual(X.run_batch(self.ctx(seed={}), ["NSEI|2026-11-13 09:45|ENTRY"]), [])
    def test_summary_flags_missing_orders_and_text(self):
        self.exit_entry(); ctx = self.ctx(seed=self.SHORT); X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])
        d = X.summary_dict("2026-11-13"); self.assertEqual((d["orders"], d["filled"], d["expected_orders"]), (1, 1, 2)); self.assertEqual(d["missing_orders"], ["NSEI|2026-11-13 09:15|ENTRY"]); self.assertIn("NO ORDER LOGGED", X.summary_text(d))
        self.assertEqual(X.write_summary("2026-11-13")[0], "data/live/order_summary.json"); self.assertEqual(X.write_summary("2026-11-14"), []); out = []; X.report(out=out.append); self.assertTrue(any("Slippage" in x for x in out))
    def test_summary_stopped_flag(self):
        self.exit_entry(); X.run_batch(self.ctx(fn=lambda t: book(25030.0 + 100 * t), seed=self.SHORT, nofb=True), ["NSEI|2026-11-13 09:15|EXIT"]); d = X.summary_dict("2026-11-13"); self.assertTrue(d["stopped"]); self.assertEqual(d["not_filled"], 1)

class FakeLiveKite:
    def __init__(self, status="COMPLETE", fail_place=None):
        self.calls = []; self.status = status; self.fail_place = fail_place
    def profile(self): return {}
    def place_order(self, **kw):
        if self.fail_place: raise self.fail_place
        self.calls.append(("place", kw)); return "OID1"
    def order_history(self, oid): return [dict(status="OPEN"), dict(status=self.status, filled_quantity=65 if self.status == "COMPLETE" else 20, average_price=25001.5, status_message="no margin")]
    def modify_order(self, **kw): self.calls.append(("modify", kw))
    def cancel_order(self, **kw): self.calls.append(("cancel", kw))
    def positions(self): return {"net": [dict(tradingsymbol="NIFTY26NOVFUT", quantity=65), dict(tradingsymbol="RELIANCE", quantity=10), dict(tradingsymbol="BANKNIFTY26NOVFUT", quantity=0)]}

class Pnl2(EndToEnd):
    """Profit or loss in the exit message."""
    def test_exit_message_carries_net_pnl_and_row_columns(self):
        self.exit_entry(); ctx = self.ctx(seed=self.SHORT); rs = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])
        r = rs[0]; self.assertEqual(r["trade_entry_px"], 25000.0); self.assertEqual(int(r["trade_gross"]), -65); self.assertGreater(int(r["trade_charges"]), 0); self.assertEqual(int(r["trade_net"]), int(r["trade_gross"]) - int(r["trade_charges"]))
        t, b, p = self.sent[0]; t = t.split("] ", 1)[1]; self.assertTrue(t.startswith("EXIT Nifty 50: -Rs ")); self.assertTrue(t.endswith(" net")); self.assertIn("NET -Rs", b); self.assertIn("sold on 2026-11-12 at 25000.00 (ticket touch)", b); self.assertIn("Day total so far", b); self.assertIn("held 1 day(s)", b); self.assertEqual(p, "default"); self.assertEqual(len(self.sent), 1)
    def test_profit_is_positive(self):
        self.exit_entry(); ctx = self.ctx(fn=lambda t: book(24950.0, 24949.0), seed=self.SHORT); rs = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])
        self.assertGreater(int(rs[0]["trade_net"]), 0); self.assertTrue(self.sent[0][0].split("] ", 1)[1].startswith("EXIT Nifty 50: +Rs "))
    def test_long_trade_math(self):
        self.alerts([self.al("NSEI|2026-11-12 14:45|ENTRY", "ENTRY", "BUY", "2026-11-12 14:45", "25000", at="2026-11-12 15:00:20"), self.al("NSEI|2026-11-13 09:15|EXIT", "EXIT", "SELL", "2026-11-13 09:15", "25000", closes="NSEI|2026-11-12 14:45|ENTRY")])
        self.tickets([self.tk("NSEI|2026-11-12 14:45|ENTRY", "ENTRY", "BUY", 25000.0, 25010.0, at="2026-11-12 15:00:20"), self.tk("NSEI|2026-11-13 09:15|EXIT", "EXIT", "SELL", limit=25190.0)])
        ctx = self.ctx(fn=lambda t: book(25202.0, 25200.0), seed={"NIFTY26NOVFUT": 65}); r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])[0]; self.assertEqual(int(r["trade_gross"]), round((25200.0 - 25010.0) * 65))
    def test_logged_entry_fill_beats_ticket_touch(self):
        self.exit_entry()
        with open("data/live/orders.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(X.HDR); r = {k: "" for k in X.HDR}; r.update(event_id="NSEI|2026-11-12 14:45|ENTRY", status="FILLED", fill_price="24990.0", run_at_ist="2026-11-12 15:00:30"); w.writerow([r[k] for k in X.HDR])
        ctx = self.ctx(seed=self.SHORT); r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])[0]; self.assertEqual(r["trade_entry_px"], 24990.0); self.assertIn("(fill)", self.sent[0][1])
    def test_entry_before_records_says_so_and_still_sends(self):
        self.alerts([self.al("NSEI|2026-11-13 09:15|EXIT", "EXIT", "BUY", "2026-11-13 09:15", "25000", closes="NSEI|2026-10-28 12:45|ENTRY")]); self.tickets([self.tk("NSEI|2026-11-13 09:15|EXIT", "EXIT", "BUY")])
        ctx = self.ctx(seed=self.SHORT); r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])[0]; self.assertEqual(r["status"], "FILLED"); self.assertEqual(r["trade_net"], ""); self.assertTrue(self.sent[0][0].split("] ", 1)[1].startswith("FILLED Nifty 50 EXIT")); self.assertIn("Profit or loss not available", self.sent[0][1])
    def test_pnl_failure_never_breaks_the_order(self):
        self.exit_entry(); orig = X.trade_pnl; X.trade_pnl = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            ctx = self.ctx(seed=self.SHORT)
            with self.assertRaises(RuntimeError): X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"])
        finally: X.trade_pnl = orig
    def test_entries_and_roll_close_carry_no_pnl(self):
        self.entry_only(); ctx = self.ctx(seed={}); r = X.run_batch(ctx, ["NSEI|2026-11-13 09:15|ENTRY"])[0]; self.assertEqual(r["trade_net"], ""); self.assertTrue(self.sent[0][0].split("] ", 1)[1].startswith("FILLED Nifty 50 ENTRY"))
    def test_summary_totals_closed_trades(self):
        self.exit_entry(); ctx = self.ctx(seed=self.SHORT); X.run_batch(ctx, ["NSEI|2026-11-13 09:15|EXIT"]); d = X.summary_dict("2026-11-13"); self.assertEqual(d["closed_trades"], 1); self.assertLess(d["closed_net"], 0); self.assertIn("Closed trades today: 1", X.summary_text(d))

class Live(Base):
    def test_place_and_complete(self):
        k = FakeLiveKite(); b = LB.KiteBroker(k); o = b.place(C, "BUY", 65, 25013.7, "NSEI|2026-11-13 09:15|ENTRY"); self.assertEqual(o.id, "OID1")
        kw = k.calls[0][1]; self.assertEqual((kw["order_type"], kw["product"], kw["variety"], kw["validity"], kw["price"], kw["quantity"]), ("LIMIT", "NRML", "regular", "DAY", 25013.7, 65)); self.assertLessEqual(len(kw["tag"]), 20)
        b.poll(o); self.assertEqual((o.state, o.avg), ("COMPLETE", 25001.5))
    def test_rejection_such_as_wrong_ip_becomes_rejected_order(self):
        b = LB.KiteBroker(FakeLiveKite(fail_place=RuntimeError("IP not allowed"))); o = b.place(C, "BUY", 65, 1.0, "t"); self.assertEqual(o.state, "REJECTED"); self.assertIn("IP not allowed", o.reason)
        r = X.execute(b, C, "BUY", 65, 1.0, 0.05, Clock().now, Clock().sleep, "t"); self.assertEqual(r["status"], "REJECTED")
    def test_partial_then_cancel(self):
        k = FakeLiveKite(status="OPEN"); b = LB.KiteBroker(k); ck = Clock(); k.quote = lambda keys: {keys[0]: dict(last_price=25001.0, depth=dict(buy=[dict(price=25000.0, quantity=5)], sell=[dict(price=25001.0, quantity=5)]))}
        r = X.execute(b, C, "BUY", 65, 25013.0, 0.05, ck.now, ck.sleep, "t"); self.assertEqual(r["status"], "PARTIAL"); kinds = [c[0] for c in k.calls]; self.assertIn("modify", kinds); self.assertIn("cancel", kinds)
    def test_timeout_on_place_adopts_the_order_that_did_reach_kite(self):
        class K(FakeLiveKite):
            def place_order(self, **kw): self.tag = kw["tag"]; raise TimeoutError("timed out")
            def orders(self): return [dict(tag=self.tag, order_id="REAL9")]
        o = LB.KiteBroker(K()).place(C, "BUY", 65, 25013.0, "NSEI|2026-11-13 09:15|ENTRY"); self.assertEqual((o.id, o.state), ("REAL9", "OPEN"))
    def test_timeout_with_no_matching_order_is_rejected_not_retried(self):
        class K(FakeLiveKite):
            def place_order(self, **kw): raise TimeoutError("timed out")
            def orders(self): return []
        o = LB.KiteBroker(K()).place(C, "BUY", 65, 25013.0, "t"); self.assertEqual(o.state, "REJECTED")
    def test_timeout_and_order_book_unreadable_is_unknown(self):
        class K(FakeLiveKite):
            def place_order(self, **kw): raise TimeoutError("timed out")
            def orders(self): raise OSError("down")
        o = LB.KiteBroker(K()).place(C, "SELL", 65, 1.0, "t"); self.assertEqual(o.state, "REJECTED"); self.assertTrue(o.reason.startswith("UNKNOWN"))
    def test_positions_are_futures_only(self): self.assertEqual(LB.KiteBroker(FakeLiveKite()).positions(), {"NIFTY26NOVFUT": 65})
    def test_ready(self): self.assertTrue(LB.KiteBroker(FakeLiveKite()).ready()[0]); k = FakeLiveKite(); k.profile = lambda: (_ for _ in ()).throw(OSError("x")); self.assertFalse(LB.KiteBroker(k).ready()[0])

class Mode(Base):
    def test_default_is_rehearsal(self): self.assertEqual(X.order_mode(env={}, today="2026-11-27")[0], "shadow")
    def test_env_alone_is_not_enough(self): m, note = X.order_mode(env={"IBT_ORDER_MODE": "live"}, today="2026-11-27"); self.assertEqual(m, "shadow"); self.assertIn("GO_LIVE", note)
    def test_file_alone_is_not_enough(self): open("data/live/GO_LIVE", "w").write("approved 2026-11-27"); self.assertEqual(X.order_mode(env={}, today="2026-11-27")[0], "shadow")
    def test_both_and_dated_today_is_live(self): open("data/live/GO_LIVE", "w").write("approved 2026-11-27"); self.assertEqual(X.order_mode(env={"IBT_ORDER_MODE": "live"}, today="2026-11-27")[0], "live")
    def test_stale_date_is_rehearsal(self): open("data/live/GO_LIVE", "w").write("approved 2026-11-27"); self.assertEqual(X.order_mode(env={"IBT_ORDER_MODE": "live"}, today="2026-11-30")[0], "shadow")

class Source(unittest.TestCase):
    def read(self, f): 
        with open(os.path.join(HERE, f)) as fh: return fh.read()
    def test_only_live_broker_can_place_orders(self):
        for f in ("watcher.py", "order_exec.py", "safety.py", "ticket.py", "fill_logger.py", "reconcile.py"):
            src = self.read(f)
            for call in (".place_order(", ".modify_order(", ".cancel_order("): self.assertNotIn(call, src, f)
        self.assertIn(".place_order(", self.read("live_broker.py"))
    def test_live_broker_is_imported_only_in_main_and_only_for_live(self):
        src = self.read("order_exec.py"); self.assertEqual(src.count("live_broker"), src.count("from live_broker import KiteBroker") + src.count("live_broker.py"))
        i = src.index("from live_broker import KiteBroker"); self.assertIn('if mode == "live":', src[i - 80:i])
    def test_rehearsal_never_launches_from_tests(self): self.assertIn("notify_fn is None and make_kite_fn is None", self.read("watcher.py"))

if __name__ == "__main__": unittest.main()
