"""Tests for ticket.py and the watcher's expiry-week block, order ticket and roll alert. Dummy data, no network, no orders.
Run from this folder:  ~/ibt-env/bin/python -m unittest -v test_ticket"""
import os, csv, unittest, unittest.mock
import test_watcher as TW
from test_watcher import Base, FakeKite, ev, ist, watcher, setUpModule, tearDownModule
import ticket as T

HOL = {"2026-11-24"}
DEFAULT_BLOCK = dict(T.BLOCK_ON)   # the shipped setting, read before the tests switch the block on to test its mechanics
def fut(sym, exp, ex="NFO", lot=65, tick=0.1, tok=1): return dict(symbol=sym, token=tok, expiry=exp, lot=lot, tick=tick, ex=ex)
FUTS = {"NSEI": [fut("NIFTY26NOVFUT", "2026-11-23"), fut("NIFTY26DECFUT", "2026-12-29")],
        "BSESN": [fut("SENSEX26NOVFUT", "2026-11-26", "BFO", 20, 0.2), fut("SENSEX26DECFUT", "2026-12-31", "BFO", 20, 0.2)]}

class Pure(unittest.TestCase):
    def setUp(self):
        _, self.D = watcher.engine(); p = unittest.mock.patch.dict(T.BLOCK_ON, {k: True for k in T.BLOCK_ON}); p.start(); self.addCleanup(p.stop)
    def test_shipped_setting_is_unblocked_for_every_index(self): self.assertEqual(DEFAULT_BLOCK, {"NSEI": False, "NSEBANK": False, "BSESN": False})
    def test_limit_price_buy_above_ask_sell_below_bid_on_the_tick(self):
        self.assertEqual(T.limit_price("BUY", 24000.0, 24000.5, 0.1), 24012.6)
        self.assertEqual(T.limit_price("SELL", 24000.0, 24000.5, 0.1), 23988.0)
        self.assertGreater(T.limit_price("BUY", 100.0, 100.05, 0.05), 100.05)
        self.assertLess(T.limit_price("SELL", 100.0, 100.05, 0.05), 100.0)
    def test_block_covers_last_two_sessions_up_to_expiry_with_a_holiday(self):
        b = lambda d: T.entry_blocked(self.D, "NSEI", d, FUTS, HOL)
        self.assertEqual([b(d) for d in ("2026-11-19", "2026-11-20", "2026-11-23")], [False, True, True])
    def test_sensex_thursday_expiry(self):
        b = lambda d: T.entry_blocked(self.D, "BSESN", d, FUTS, HOL)
        self.assertEqual([b(d) for d in ("2026-11-23", "2026-11-25", "2026-11-26")], [False, True, True])  # 24th is a holiday
    def test_after_expiry_the_next_month_counts(self):
        self.assertFalse(T.entry_blocked(self.D, "NSEI", "2026-11-25", FUTS, HOL))
    def test_fallback_calendar_matches_kite_when_no_list(self):
        self.assertEqual(T.near_expiry(self.D, "NSEI", "2026-11-10", {}, HOL), "2026-11-23")
        self.assertEqual(T.near_expiry(self.D, "BSESN", "2026-11-10", {}, HOL), "2026-11-26")
    def test_contract_rolls_to_next_month_within_four_days(self):
        c = lambda d: T.contract_for("NSEI", d, FUTS, HOL, self.D)["symbol"]
        self.assertEqual((c("2026-11-18"), c("2026-11-19"), c("2026-11-10")), ("NIFTY26NOVFUT", "NIFTY26DECFUT", "NIFTY26NOVFUT"))
    def test_roll_day_is_the_session_before_expiry(self):
        self.assertEqual(T.roll_day("2026-11-23", HOL), "2026-11-20")
        self.assertEqual(T.roll_day("2026-11-26", HOL), "2026-11-25")
    def test_held_contract_moves_to_next_month_after_the_roll_day(self):
        h = lambda today: T.held_contract("NSEI", "2026-11-10", today, FUTS, HOL, self.D)["symbol"]
        self.assertEqual((h("2026-11-20"), h("2026-11-23"), h("2026-11-25")), ("NIFTY26NOVFUT", "NIFTY26DECFUT", "NIFTY26DECFUT"))   # after the roll alert the position is in December
    def test_ticket_without_quote_says_so_and_has_no_limit(self):
        row, lines = T.build("ENTRY", "NSEI", "Nifty 50", "BUY", FUTS["NSEI"][0], 1, 65, (None, None, None, "quote failed (X)"), "none", 20, "id", ist("2026-11-10 11:15:20"))
        self.assertEqual(row["limit"], ""); self.assertIn("no live quote", "\n".join(lines)); self.assertEqual(row["qty"], 65)

class QKite(FakeKite):
    def __init__(self, *a, fail_quote=False, **k): super().__init__(*a, **k); self.fail_quote = fail_quote
    def instruments(self, ex):
        if ex in ("NFO", "BFO"):
            self.calls.append("instruments " + ex)
            if ex == "NFO":
                return [dict(instrument_token=21, tradingsymbol="NIFTY26NOVFUT", name="NIFTY", instrument_type="FUT", expiry="2026-11-23", lot_size=65, tick_size=0.1),
                        dict(instrument_token=22, tradingsymbol="NIFTY26DECFUT", name="NIFTY", instrument_type="FUT", expiry="2026-12-29", lot_size=65, tick_size=0.1),
                        dict(instrument_token=23, tradingsymbol="BANKNIFTY26NOVFUT", name="BANKNIFTY", instrument_type="FUT", expiry="2026-11-23", lot_size=30, tick_size=0.2)]
            return [dict(instrument_token=31, tradingsymbol="SENSEX26NOVFUT", name="SENSEX", instrument_type="FUT", expiry="2026-11-26", lot_size=20, tick_size=0.2)]
        return super().instruments(ex)
    def quote(self, keys):
        self.calls.append("quote")
        if self.fail_quote: raise Exception("network down")
        return {k: dict(last_price=24100.0, depth=dict(buy=[dict(price=24099.5, quantity=65)], sell=[dict(price=24100.5, quantity=65)])) for k in keys}

class TicketTick(Base):
    def setUp(self):
        super().setUp(); p = unittest.mock.patch.dict(T.BLOCK_ON, {k: True for k in T.BLOCK_ON}); p.start(); self.addCleanup(p.stop)
    def go(self, t, events, kite=None, st=None, sent=None):
        st = st or watcher.State(); kite = kite or QKite(t.strftime("%Y-%m-%d")); sent = sent if sent is not None else []
        L, D = watcher.engine()
        with TW.mock.patch.object(L, "recipe_events", lambda nm, work: events if nm == "NSEI" else []):
            res = watcher.run_tick(st, t, make_kite_fn=lambda tok: kite, notify_fn=lambda a, b: sent.append((a, b)) or True)
        return st, kite, res, sent
    def tickets(self): return list(csv.DictReader(open(watcher.TICKETS))) if os.path.exists(watcher.TICKETS) else []
    def test_normal_entry_carries_contract_limit_and_lag_and_is_logged_in_tickets(self):
        self.token("2026-11-12")
        _, kite, _, sent = self.go(ist("2026-11-12 11:15:20"), [ev("ENTRY", "BUY", "2026-11-12 11:00", "11:15", 24100.0, day="2026-11-12")])
        body = sent[0][1]
        for need in ("NIFTY26NOVFUT", "expires 2026-11-23", "BUY 1 lot (65 units)", "Limit 24112.60", "bid 24099.50, ask 24100.50", "Candle closed 20 seconds ago"): self.assertIn(need, body)
        r = self.tickets(); self.assertEqual((len(r), r[0]["contract"], r[0]["limit"], r[0]["seconds_after_candle"], r[0]["qty"]), (1, "NIFTY26NOVFUT", "24112.6", "20", "65"))
        self.assertNotIn("order", [c for c in kite.calls if c.startswith("order")])
    def test_with_the_block_off_an_expiry_week_entry_is_alerted_on_the_next_month_contract(self):
        self.token("2026-11-20")
        with unittest.mock.patch.dict(T.BLOCK_ON, {"NSEI": False}):
            _, _, _, sent = self.go(ist("2026-11-20 11:15:20"), [ev("ENTRY", "BUY", "2026-11-20 11:00", "11:15", 24100.0, day="2026-11-20")])
        self.assertEqual(len(sent), 1); self.assertIn("NIFTY26DECFUT", sent[0][1])
    def test_entry_in_expiry_week_is_logged_not_alerted(self):
        self.token("2026-11-20")
        _, _, _, sent = self.go(ist("2026-11-20 11:15:20"), [ev("ENTRY", "BUY", "2026-11-20 11:00", "11:15", 24100.0, day="2026-11-20")])
        self.assertEqual(sent, []); r = self.rows(); self.assertEqual(len(r), 1); self.assertTrue(r[0]["alert"].startswith("not sent, expiry week")); self.assertEqual(self.tickets(), [])
    def test_exit_of_a_blocked_entry_is_not_alerted_but_a_normal_exit_is(self):
        self.token("2026-11-26")
        blocked = [ev("ENTRY", "BUY", "2026-11-20 11:00", "11:15", 24100.0, day="2026-11-20"), ev("EXIT", "SELL", "2026-11-26 11:00", "11:15", 24200.0, day="2026-11-26")]
        _, _, _, sent = self.go(ist("2026-11-26 11:15:20"), blocked); self.assertEqual(sent, [])
        self.assertIn("entry was blocked", self.rows()[0]["alert"])
    def test_exit_after_roll_day_uses_the_next_month_contract(self):
        self.token("2026-11-25")
        evs = [ev("ENTRY", "BUY", "2026-11-12 11:00", "11:15", 24100.0, day="2026-11-12"), ev("EXIT", "SELL", "2026-11-25 11:00", "11:15", 24200.0, day="2026-11-25")]
        _, _, _, sent = self.go(ist("2026-11-25 11:15:20"), evs)
        self.assertIn("NIFTY26DECFUT", sent[-1][1])
    def test_quote_failure_still_sends_the_alert_without_a_limit(self):
        self.token("2026-11-12")
        _, _, _, sent = self.go(ist("2026-11-12 11:15:20"), [ev("ENTRY", "SELL", "2026-11-12 11:00", "11:15", 24100.0, day="2026-11-12")], kite=QKite("2026-11-12", fail_quote=True))
        self.assertEqual(len(sent), 1); self.assertIn("no live quote", sent[0][1]); self.assertEqual(self.tickets()[0]["limit"], "")
    def test_no_futures_list_still_sends_with_a_warning(self):
        self.token("2026-11-12"); kite = FakeKite("2026-11-12"); sent = []
        _, _, _, sent = self.go(ist("2026-11-12 11:15:20"), [ev("ENTRY", "BUY", "2026-11-12 11:00", "11:15", 24100.0, day="2026-11-12")], kite=kite)
        self.assertEqual(len(sent), 1); self.assertIn("not available from Kite", sent[0][1])
    def test_roll_alert_on_roll_day_once_two_legs_not_before_1445(self):
        self.token("2026-11-20"); open_pos = [ev("ENTRY", "BUY", "2026-11-12 11:00", "11:15", 24100.0, day="2026-11-12")]
        st, kite, _, sent = self.go(ist("2026-11-20 14:30:20"), open_pos); self.assertEqual([s for s in sent if s[0].startswith("ROLL")], [])
        _, _, _, sent = self.go(ist("2026-11-20 14:50:20"), open_pos, kite=kite, st=st)
        rolls = [s for s in sent if s[0].startswith("ROLL")]; self.assertEqual(len(rolls), 1)
        b = rolls[0][1]
        for need in ("close the near month", "NIFTY26NOVFUT", "SELL 1 lot", "open the next month", "NIFTY26DECFUT", "BUY 1 lot"): self.assertIn(need, b)
        _, _, _, sent2 = self.go(ist("2026-11-20 15:30:20"), open_pos, kite=kite, st=st)
        self.assertEqual([s for s in sent2 if s[0].startswith("ROLL")], [])
        self.assertEqual([r["kind"] for r in self.tickets()], ["ROLL", "ROLL"])
    def test_no_roll_when_flat_or_entry_was_blocked_or_already_next_month(self):
        self.token("2026-11-20")
        closed = [ev("ENTRY", "BUY", "2026-11-12 11:00", "11:15", 24100.0, day="2026-11-12"), ev("EXIT", "SELL", "2026-11-19 11:00", "11:15", 24200.0, day="2026-11-19")]
        _, _, _, s1 = self.go(ist("2026-11-20 14:50:20"), closed); self.assertEqual([s for s in s1 if s[0].startswith("ROLL")], [])
        nextmonth = [ev("ENTRY", "BUY", "2026-11-19 11:00", "11:15", 24100.0, day="2026-11-19")]   # entered 4 days before expiry, so on the December contract; but also blocked? 19th is 3 sessions out, allowed
        _, _, _, s2 = self.go(ist("2026-11-20 14:50:20"), nextmonth); self.assertEqual([s for s in s2 if s[0].startswith("ROLL")], [])

if __name__ == "__main__": unittest.main()
