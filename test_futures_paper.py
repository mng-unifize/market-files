"""Tests for futures_paper.py (futures model: premium decay, slippage, roll, expiry rule). Run: ~/ibt-env/bin/python -m unittest test_futures_paper"""
import os, sys, unittest, datetime as dt
REPO = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "paper"))
import futures_paper as F
from mock_trades import tcost

def days(a, b):
    d = dt.date.fromisoformat(a); out = []
    while d.isoformat() <= b:
        if d.weekday() < 5 and d.isoformat() not in F.HOL: out.append(d.isoformat())
        d += dt.timedelta(1)
    return out
CAL = F.Cal("NSEI", days("2026-10-01", "2026-12-31"))
FLAT = {d: 24000.0 for d in CAL.days}

class T(unittest.TestCase):
    def test_no_carry_no_slip_is_index_pnl_minus_charges(self):
        r = F.model_trade(CAL, FLAT, 1, "2026-11-02", 24000.0, "2026-11-05", 24100.0, 65, False, carry=0, slip=0)
        self.assertAlmostEqual(r["pnl"], 100 * 65 - tcost(24000.0, 24100.0, 65, 1, "2026-11-02", "2026-11-05", False), 4); self.assertEqual(r["rolled"], 0)
    def test_slippage_always_costs(self):
        a = F.model_trade(CAL, FLAT, 1, "2026-11-02", 24000.0, "2026-11-05", 24100.0, 65, False, carry=0, slip=0)["pnl"]
        b = F.model_trade(CAL, FLAT, 1, "2026-11-02", 24000.0, "2026-11-05", 24100.0, 65, False, carry=0, slip=0.0001)["pnl"]
        self.assertGreater(a - b, 0.9 * 0.0001 * 48100 * 65)
        s = F.model_trade(CAL, FLAT, -1, "2026-11-02", 24000.0, "2026-11-05", 23900.0, 65, False, carry=0, slip=0.0001)["pnl"]
        s0 = F.model_trade(CAL, FLAT, -1, "2026-11-02", 24000.0, "2026-11-05", 23900.0, 65, False, carry=0, slip=0)["pnl"]
        self.assertGreater(s0 - s, 0)
    def test_premium_decay_hurts_longs_and_helps_shorts_on_a_flat_index(self):
        lg = F.model_trade(CAL, FLAT, 1, "2026-11-02", 24000.0, "2026-11-16", 24000.0, 65, False, carry=0.065, slip=0)["gross"]
        sh = F.model_trade(CAL, FLAT, -1, "2026-11-02", 24000.0, "2026-11-16", 24000.0, 65, False, carry=0.065, slip=0)["gross"]
        self.assertLess(lg, 0); self.assertGreater(sh, 0); self.assertAlmostEqual(lg, -sh, 4)
    def test_trade_carried_past_the_roll_day_is_rolled_into_december(self):
        r = F.model_trade(CAL, FLAT, 1, "2026-11-10", 24000.0, "2026-11-25", 24000.0, 65, False)
        self.assertEqual((r["rolled"], r["contract"]), (1, "2026-12-29"))
    def test_exit_on_the_roll_day_does_not_roll(self):
        self.assertEqual(F.model_trade(CAL, FLAT, 1, "2026-11-10", 24000.0, "2026-11-20", 24000.0, 65, False)["rolled"], 0)
    def test_entry_within_four_days_of_expiry_goes_on_next_month_without_a_roll(self):
        r = F.model_trade(CAL, FLAT, 1, "2026-11-19", 24000.0, "2026-11-30", 24000.0, 65, False)
        self.assertEqual((r["rolled"], r["contract"]), (0, "2026-12-29"))
    def test_expiry_rule_window(self):
        self.assertEqual([CAL.blocked(d) for d in ("2026-11-19", "2026-11-20", "2026-11-23")], [False, True, True])
    def test_expiry_moves_back_for_the_24_nov_holiday(self):
        self.assertEqual(CAL.expiry(2026, 11), "2026-11-23")
if __name__ == "__main__": unittest.main()
