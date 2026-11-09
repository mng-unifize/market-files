"""Tests for fill_logger.py. Dummy data, no network. The fake Kite only has trades() and orders(); any other call fails the test (so it can never place an order).
Run: ~/ibt-env/bin/python -m unittest test_fills"""
import os, sys, csv, io, shutil, tempfile, unittest, contextlib, datetime as dt
REPO = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, REPO)
import fill_logger as F, ticket as T

class FakeKite:
    def __init__(self, trades, orders=()): self._t = list(trades); self._o = list(orders); self.calls = []
    def trades(self): self.calls.append("trades"); return self._t
    def orders(self): self.calls.append("orders"); return self._o
    def __getattr__(self, n): raise AssertionError("unexpected Kite call: " + n)

def tr(oid, sym, side, q, px, t): return dict(order_id=oid, tradingsymbol=sym, transaction_type=side, quantity=q, average_price=px, fill_timestamp=dt.datetime.fromisoformat(t), order_timestamp=dt.datetime.fromisoformat(t) - dt.timedelta(seconds=2))
def tk(eid, sym, side, t, bid=24099.5, ask=24100.5, qty=65, kind="ENTRY", limit=24112.6): return dict(event_id=eid, logged_at_ist=t, kind=kind, index="Nifty 50", side=side, contract=sym, expiry="2026-11-23", lots=1, qty=qty, bid=bid, ask=ask, last=24100, limit=limit, stop="none", seconds_after_candle=20, quote_status="ok", note="")

class Fills(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd(); self.d = tempfile.mkdtemp(); os.chdir(self.d); os.makedirs("data/live")
    def tearDown(self): os.chdir(self.old); shutil.rmtree(self.d, ignore_errors=True)
    def tickets(self, rows):
        with open(F.TICKETS, "w", newline="") as f:
            w = csv.DictWriter(f, T.HDR_T); w.writeheader(); [w.writerow(r) for r in rows]
    def fills(self): return list(csv.DictReader(open(F.FILLS))) if os.path.exists(F.FILLS) else []
    def run_day(self, trades, day="2026-11-12"): k = FakeKite(trades); r = F.log_day(k, day); return k, r

    def test_buy_above_mid_costs_and_sell_below_mid_costs(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20"), tk("b", "NIFTY26NOVFUT", "SELL", "2026-11-12 12:15:20", kind="EXIT")])
        self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24102.0, "2026-11-12T11:16:00"), tr("2", "NIFTY26NOVFUT", "SELL", 65, 24098.0, "2026-11-12T12:16:00")])
        r = self.fills(); self.assertEqual([x["status"] for x in r], ["matched", "matched"])
        self.assertAlmostEqual(float(r[0]["slippage_pct"]), (24102 - 24100) / 24100 * 100, 3); self.assertGreater(float(r[1]["slippage_pct"]), 0)
        self.assertEqual(int(r[0]["slippage_rs"]), 2 * 65)
    def test_a_better_than_mid_fill_is_negative_slippage(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")]); self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24099.0, "2026-11-12T11:16:00")])
        self.assertLess(float(self.fills()[0]["slippage_pct"]), 0)
    def test_partial_fills_become_one_order_at_the_volume_weighted_price(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20", qty=130)])
        self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24101.0, "2026-11-12T11:16:00"), tr("1", "NIFTY26NOVFUT", "BUY", 65, 24103.0, "2026-11-12T11:16:02")])
        r = self.fills(); self.assertEqual((len(r), r[0]["qty"], float(r[0]["avg_price"])), (1, "130", 24102.0))
    def test_order_before_the_ticket_or_long_after_it_is_not_matched(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")])
        self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24100.0, "2026-11-12T11:00:00"), tr("2", "NIFTY26NOVFUT", "BUY", 65, 24100.0, "2026-11-12T12:30:00")])
        self.assertEqual(sorted(x["status"] for x in self.fills()), ["no ticket (manual or untracked)", "no ticket (manual or untracked)", "ticket not traded"])
    def test_wrong_contract_or_side_is_not_matched(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")])
        self.run_day([tr("1", "NIFTY26DECFUT", "BUY", 65, 24100.0, "2026-11-12T11:16:00"), tr("2", "NIFTY26NOVFUT", "SELL", 65, 24100.0, "2026-11-12T11:16:00")])
        self.assertEqual(sum(1 for x in self.fills() if x["status"].startswith("matched")), 0)
    def test_a_ticket_with_no_trade_is_logged_as_not_traded(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")]); self.run_day([])
        r = self.fills(); self.assertEqual((len(r), r[0]["status"], r[0]["qty"]), (1, "ticket not traded", "0"))
    def test_running_twice_does_not_duplicate(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")]); t = [tr("1", "NIFTY26NOVFUT", "BUY", 65, 24101.0, "2026-11-12T11:16:00")]
        self.run_day(t); _, (n, _, _) = self.run_day(t); self.assertEqual((n, len(self.fills())), (0, 1))
    def test_other_days_trades_are_ignored(self):
        self.tickets([]); self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24101.0, "2026-11-11T11:16:00")]); self.assertEqual(self.fills(), [])
    def test_only_read_calls_are_made(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")]); k, _ = self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24101.0, "2026-11-12T11:16:00")])
        self.assertTrue(set(k.calls) <= {"trades", "orders"})
    def test_quantity_not_a_multiple_of_the_ticket_is_flagged(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")]); self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 100, 24101.0, "2026-11-12T11:16:00")])
        self.assertIn("quantity differs", self.fills()[0]["status"])
    def test_ticket_without_a_quote_matches_but_has_no_slippage(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20", bid="", ask="", limit="")]); self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24101.0, "2026-11-12T11:16:00")])
        r = self.fills()[0]; self.assertEqual((r["status"], r["slippage_pct"]), ("matched", ""))
    def test_report_flags_slippage_above_the_alert_level(self):
        self.tickets([tk("a", "NIFTY26NOVFUT", "BUY", "2026-11-12 11:15:20")]); self.run_day([tr("1", "NIFTY26NOVFUT", "BUY", 65, 24112.0, "2026-11-12T11:16:00")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out): F.report()
        self.assertIn("ABOVE", out.getvalue())
if __name__ == "__main__": unittest.main()
