"""Fast trigger (26 Nov 2026): wake 2 s after the mark, wait for a stable Bank Nifty candle, Bank Nifty first, warm-up before the first signal."""
import sys, os, datetime as dt, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watcher as W
IST = W.IST
def T(h, m, s=0): return dt.datetime(2026, 11, 27, h, m, s, tzinfo=IST)   # a Friday
class Clk:
    def __init__(self, t): self.t = t
    def now(self): return self.t
    def sleep(self, x): self.t += dt.timedelta(seconds=x)
    def sleep_until(self, x):
        if x > self.t: self.t = x
class Schedule(unittest.TestCase):
    def test_marks_plus_two(self):
        self.assertEqual(W.next_fast_tick(T(9, 0)), T(9, 20, 2))
        self.assertEqual(W.next_fast_tick(T(9, 20, 2)), T(9, 25, 2))
        self.assertEqual(W.next_fast_tick(T(9, 24, 59)), T(9, 25, 2))
        self.assertEqual(W.next_fast_tick(T(15, 30, 2)), None)
        self.assertEqual(W.next_fast_tick(T(15, 29)), T(15, 30, 2))
    def test_weekend_none(self): self.assertIsNone(W.next_fast_tick(dt.datetime(2026, 11, 28, 9, 0, tzinfo=IST)))
    def test_old_schedule_untouched(self): self.assertEqual(W.next_tick(T(9, 0)), T(9, 20, 20))
class Wait(unittest.TestCase):
    BAR = "2026-11-27 09:15"
    def run_w(self, seq, deadline_s=20):
        c = Clk(T(9, 20, 2)); it = iter(seq); calls = []
        def fetch(): calls.append(c.now()); r = next(it, seq[-1]); return r
        rows, info = W.wait_for_bar(fetch, self.BAR, c.now, c.sleep, T(9, 20) + dt.timedelta(seconds=deadline_s), step=1.0)
        return rows, info, calls
    def bar(self, close): return {self.BAR: [self.BAR, 1, 2, 0, close, 5]}
    def test_two_equal_reads_stop(self):
        rows, info, calls = self.run_w([self.bar(100), self.bar(100), self.bar(999)])
        self.assertEqual(len(calls), 2); self.assertEqual(info["stable"], 1); self.assertEqual(rows[self.BAR][4], 100)
    def test_changing_close_keeps_waiting(self):
        rows, info, calls = self.run_w([self.bar(100), self.bar(101), self.bar(101)])
        self.assertEqual(len(calls), 3); self.assertEqual(rows[self.BAR][4], 101)
    def test_missing_bar_waits_then_gives_up_at_deadline(self):
        rows, info, calls = self.run_w([{}], deadline_s=20)
        self.assertEqual(info["found"], 0); self.assertEqual(info["stable"], 0); self.assertLessEqual(calls[-1], T(9, 20, 20)); self.assertGreater(len(calls), 10)
    def test_bar_appears_late(self):
        rows, info, calls = self.run_w([{}, {}, self.bar(5), self.bar(5)])
        self.assertEqual(info["stable"], 1); self.assertEqual(len(calls), 4)
    def test_never_stable_returns_latest_at_deadline(self):
        seq = [self.bar(i) for i in range(100)]
        rows, info, calls = self.run_w(seq, deadline_s=10)
        self.assertEqual(info["stable"], 0); self.assertEqual(info["found"], 1)
class Warm(unittest.TestCase):
    def test_no_token_returns_false(self):
        st = W.State(); old = W.load_token; W.load_token = lambda d: (None, "missing")
        try: self.assertFalse(W.warm_up(st, T(9, 5)))
        finally: W.load_token = old
    def test_never_raises(self):
        st = W.State(); old = W.load_token; W.load_token = lambda d: ("tok", "ok")
        try: self.assertFalse(W.warm_up(st, T(9, 5), make_kite_fn=lambda t: (_ for _ in ()).throw(RuntimeError("boom"))))
        finally: W.load_token = old
class Loop(unittest.TestCase):
    def test_bank_nifty_first_then_others_at_plus_20(self):
        calls = []
        class C(Clk):
            pass
        c = C(T(15, 29, 50)); st = W.State(); st.tokens = {"x": 1}; st.futs = {}
        def tick(st, t, **kw): calls.append((sorted(kw), t)); return "ok"
        old = W.warm_up; W.warm_up = lambda *a, **k: True
        try: W.trading_loop(c, st, tick=tick, fast=True)
        finally: W.warm_up = old
        kinds = [k for k, _ in calls if k in (["only", "poll"], ["skip"])]
        self.assertIn(["only", "poll"], [k for k, _ in calls]); self.assertIn(["skip"], [k for k, _ in calls])
        first = [t for k, t in calls if k == ["only", "poll"]][-1]; second = [t for k, t in calls if k == ["skip"]][-1]
        self.assertEqual(first, T(15, 30, 2)); self.assertEqual(second, T(15, 30, 20))
if __name__ == "__main__": unittest.main()
