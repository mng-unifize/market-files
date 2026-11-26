"""GitHub backup alert (26 Nov 2026): fresh Bank Nifty ENTRY and EXIT signals also go to the phone from GitHub. Never breaks the recording."""
import sys, os, datetime as dt, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper"))
import live as L
IST = L.IST; TODAY = "2026-11-27"
def row(event, index="Bank Nifty", side="SELL", bar="2026-11-27 11:00", fill="11:05", fp=64000.5):
    return ["NSEBANK|%s|%s" % (bar, event), "x", event, index, side, bar, fill, 63990.0, "BANKNIFTY26DECFUT", fp]
def at(h, m, s=20): return dt.datetime(2026, 11, 27, h, m, s, tzinfo=IST)
class Msgs(unittest.TestCase):
    def test_fresh_entry_and_exit(self):
        m = L.backup_messages([row("EXIT", side="BUY"), row("ENTRY", side="SELL")], TODAY, at(11, 5))
        self.assertEqual([t for t, _ in m], ["[BACKUP] EXIT BUY Bank Nifty", "[BACKUP] ENTRY SELL Bank Nifty"])
        self.assertIn("BANKNIFTY26DECFUT", m[0][1]); self.assertIn("ACT ONLY IF", m[0][1]); self.assertIn("by hand", m[0][1])
    def test_other_indexes_and_skips_ignored(self):
        self.assertEqual(L.backup_messages([row("ENTRY", index="Nifty 50"), row("SKIP"), row("ENTRY", index="Sensex")], TODAY, at(11, 5)), [])
    def test_stale_or_old_day_ignored(self):
        self.assertEqual(L.backup_messages([row("ENTRY")], TODAY, at(11, 25)), [])            # 20 min old
        self.assertEqual(L.backup_messages([row("ENTRY", bar="2026-11-26 11:00")], TODAY, at(11, 5)), [])
        self.assertEqual(len(L.backup_messages([row("ENTRY")], TODAY, at(11, 19))), 1)       # 14 min old still sends
    def test_missing_future_price(self):
        m = L.backup_messages([row("ENTRY", fp="")], TODAY, at(11, 5)); self.assertEqual(len(m), 1); self.assertNotIn("future", m[0][1])
    def test_bad_row_never_raises(self):
        self.assertEqual(L.backup_messages([row("ENTRY", fill="bad")], TODAY, at(11, 5)), [])
class Send(unittest.TestCase):
    def setUp(self): self.old = {k: os.environ.pop(k, None) for k in ("NTFY_TOPIC", "NTFY_SERVER")}
    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None)
            if v is not None: os.environ[k] = v
    def test_no_topic_sends_nothing(self):
        got = []; self.assertEqual(L.send_backup([("t", "b")], post=lambda *a: got.append(a)), 0); self.assertEqual(got, [])
    def test_sends_to_topic(self):
        os.environ["NTFY_TOPIC"] = "mytopic"; got = []
        self.assertEqual(L.send_backup([("t", "b")], post=lambda u, t, b: got.append((u, t, b))), 1); self.assertEqual(got, [("https://ntfy.sh/mytopic", "t", "b")])
    def test_failure_is_swallowed_and_others_still_go(self):
        os.environ["NTFY_TOPIC"] = "mytopic"; got = []
        def post(u, t, b):
            if t == "bad": raise RuntimeError("down")
            got.append(t)
        self.assertEqual(L.send_backup([("bad", "b"), ("good", "b")], post=post), 1); self.assertEqual(got, ["good"])
if __name__ == "__main__": unittest.main()
