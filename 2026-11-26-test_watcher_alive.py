"""Watcher stall alarm (26 Nov 2026): heartbeat writes an alive file; the live watchdog alerts when it goes quiet; the watcher plist restarts it after a crash."""
import sys, os, datetime as dt, tempfile, plistlib, unittest, importlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import watcher as W
WD = importlib.import_module("2026-11-25-bn_watchdog")
IST = W.IST
class Clk:
    def __init__(self, t): self.t = t
    def now(self): return self.t
    def sleep_until(self, x): self.t = max(self.t, x)
class Alive(unittest.TestCase):
    def test_heartbeat_writes_file_each_ping(self):
        d = tempfile.mkdtemp(); old = os.getcwd(); os.chdir(d); pings = []
        try:
            W.heartbeat("u", Clk(dt.datetime(2026, 11, 27, 9, 5, 30, tzinfo=IST)), ping_fn=lambda u: pings.append(u))
            self.assertTrue(len(pings) > 5); txt = open(W.ALIVE_FILE).read().strip()
            self.assertTrue(txt.startswith("2026-11-27 16:00"), txt)       # the last ping of the day
        finally: os.chdir(old)
    def test_touch_never_raises(self):
        old = os.getcwd(); os.chdir("/")
        try: W.touch_alive()
        finally: os.chdir(old)
class Watchdog(unittest.TestCase):
    def P(self, hm, age):
        h, m = map(int, hm.split(":")); return WD.problems(dt.datetime(2026, 11, 27, h, m), False, False, "", [], [], alive_age_min=age)
    def test_stalled_alerts(self):
        p = self.P("11:30", 35); self.assertEqual(len(p), 1); self.assertEqual(p[0][1], "WATCHER STOPPED"); self.assertIn("35 minutes", p[0][2])
    def test_fine_when_recent(self): self.assertEqual(self.P("11:30", 5), []); self.assertEqual(self.P("11:30", 8), [])
    def test_quiet_outside_window(self): self.assertEqual(self.P("09:16", 999), []); self.assertEqual(self.P("15:40", 999), [])
    def test_old_callers_unchanged(self): self.assertEqual(WD.problems(dt.datetime(2026, 11, 27, 11, 30), False, False, "", [], []), [])
    def test_one_key_per_day(self):
        self.assertEqual(self.P("11:30", 35)[0][0], self.P("12:30", 95)[0][0])
class Plist(unittest.TestCase):
    def test_restarts_after_crash_not_after_clean_exit(self):
        p = plistlib.load(open(os.path.join(HERE, "com.ibt.watcher.plist"), "rb"))
        self.assertEqual(p["KeepAlive"], {"SuccessfulExit": False}); self.assertTrue(p["RunAtLoad"]); self.assertGreaterEqual(p["ThrottleInterval"], 60)
        self.assertEqual(len(p["StartCalendarInterval"]), 5)
if __name__ == "__main__": unittest.main()
