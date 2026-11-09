"""Tests for the three way signal check in reconcile.py (dashboard base vs Mac watcher alerts vs GitHub paper ledger). Dummy data, no network, no git.
Run: ~/ibt-env/bin/python -m unittest test_signal_check"""
import os, sys, csv, json, shutil, tempfile, unittest, unittest.mock as mock, subprocess
REPO = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, REPO)
import reconcile as rc
DAY = "2026-11-11"
def trade(idx, side, status, no=1, et="11:15", ep=24000.0, xt="", xp=None, day=DAY): return dict(day=day, index=idx, trade_no=no, side=side, status=status, entry_time=et, entry_price=ep, exit_time=xt, exit_price=xp)
BASE = [trade("Nifty 50", "BUY", "Open (carry forward)"), trade("Bank Nifty", "SELL", "Skipped", no=1, et="10:00", ep=54000.0)]
AH = ["event_id", "logged_at_ist", "event", "index", "action", "signal_bar", "fill_time", "lot_size", "price", "stop", "reason", "closes_entry", "alert"]
LH = "event_id,recorded_at_ist,event,index,side,signal_bar,fill_time,index_price,fut_symbol,fut_price,fut_candle,qty,fut_bid,fut_ask,quote_delay_min,note"
def arow(ev, idx, act, fill, price, alert, bar=DAY + " 11:00"): return dict(event_id="x|%s|%s" % (bar, ev), logged_at_ist=DAY + " 11:15", event=ev, index=idx, action=act, signal_bar=bar, fill_time=DAY + " " + fill, lot_size=65, price=price, stop="none", reason="r", closes_entry="", alert=alert)
def lrow(ev, idx, side, fill, price, bar=DAY + " 11:00"): return "x|%s|%s,%s 11:20,%s,%s,%s,%s,%s,%s,FUT,1,c,65,,,,note" % (bar, ev, DAY, ev, idx, side, bar, fill, price)

class SC(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.old = {k: getattr(rc, k) for k in ("ALERTS", "LEDGER", "SC_JSON", "SC_LOG", "HERE")}
        rc.ALERTS = os.path.join(self.d, "alerts.csv"); rc.LEDGER = os.path.join(self.d, "ledger.csv"); rc.SC_JSON = os.path.join(self.d, "signal_check.json"); rc.SC_LOG = os.path.join(self.d, "signal_check_log.csv"); rc.HERE = self.d
    def tearDown(self):
        for k, v in self.old.items(): setattr(rc, k, v)
        shutil.rmtree(self.d, ignore_errors=True)
    def alerts(self, rows):
        with open(rc.ALERTS, "w", newline="") as f:
            w = csv.DictWriter(f, AH); w.writeheader(); [w.writerow(r) for r in rows]
    def ledger(self, lines): open(rc.LEDGER, "w").write("\n".join([LH] + lines) + "\n")
    def good(self):
        self.alerts([arow("ENTRY", "Nifty 50", "BUY", "11:15", 24000.0, "sent"), arow("SKIP", "Bank Nifty", "SELL", "10:00", 54000.0, "log only", bar=DAY + " 09:45")])
        self.ledger([lrow("ENTRY", "Nifty 50", "BUY", "11:15", 24000.0), lrow("SKIP", "Bank Nifty", "SELL", "10:00", 54000.0, bar=DAY + " 09:45")])
    def st(self): return rc.signal_status(DAY, BASE, ledger_remote=False)

    def test_everything_agrees(self):
        self.good(); s = self.st()
        self.assertTrue(s["all_agree"]); self.assertEqual((s["expected"]["total"], s["watcher"]["matched"], s["github_paper"]["matched"], s["watcher"]["notifications_sent"], s["watcher"]["notifications_needed"]), (2, 2, 2, 1, 1))
    def test_watcher_missing_an_entry_is_flagged(self):
        self.good(); self.alerts([arow("SKIP", "Bank Nifty", "SELL", "10:00", 54000.0, "log only", bar=DAY + " 09:45")]); s = self.st()
        self.assertFalse(s["all_agree"]); self.assertEqual(len(s["watcher"]["missing"]), 1); self.assertIn("ENTRY BUY Nifty 50", s["watcher"]["missing"][0]); self.assertEqual(s["github_paper"]["missing"], [])
    def test_an_alert_that_was_logged_but_not_sent_to_the_phone_is_flagged(self):
        self.good(); self.alerts([arow("ENTRY", "Nifty 50", "BUY", "11:15", 24000.0, "not sent, stale (20 min old)"), arow("SKIP", "Bank Nifty", "SELL", "10:00", 54000.0, "log only", bar=DAY + " 09:45")]); s = self.st()
        self.assertFalse(s["all_agree"]); self.assertEqual(s["watcher"]["matched"], 2); self.assertEqual(s["watcher"]["notifications_sent"], 0); self.assertIn("stale", s["watcher"]["not_sent"][0])
    def test_github_paper_extra_or_wrong_price_is_flagged(self):
        self.good(); self.ledger([lrow("ENTRY", "Nifty 50", "BUY", "11:15", 24000.0), lrow("SKIP", "Bank Nifty", "SELL", "10:00", 54000.0, bar=DAY + " 09:45"), lrow("ENTRY", "Sensex", "BUY", "12:00", 70000.0, bar=DAY + " 11:45")])
        s = self.st(); self.assertFalse(s["all_agree"]); self.assertEqual(len(s["github_paper"]["extra"]), 1)
        self.ledger([lrow("ENTRY", "Nifty 50", "BUY", "11:15", 24100.0), lrow("SKIP", "Bank Nifty", "SELL", "10:00", 54000.0, bar=DAY + " 09:45")]); s = self.st(); self.assertEqual(len(s["github_paper"]["missing"]), 1)
    def test_no_signals_anywhere_is_clean(self):
        self.alerts([]); self.ledger([])
        s = rc.signal_status(DAY, [], ledger_remote=False); self.assertTrue(s["all_agree"]); self.assertEqual(s["expected"]["total"], 0)
    def test_missing_alerts_file_with_expected_signals_is_a_failure(self):
        self.ledger([lrow("ENTRY", "Nifty 50", "BUY", "11:15", 24000.0), lrow("SKIP", "Bank Nifty", "SELL", "10:00", 54000.0, bar=DAY + " 09:45")])
        s = self.st(); self.assertFalse(s["all_agree"]); self.assertEqual(len(s["watcher"]["missing"]), 2)
    def test_unreadable_ledger_is_a_failure(self):
        self.good(); os.remove(rc.LEDGER); s = self.st(); self.assertFalse(s["all_agree"]); self.assertFalse(s["github_paper"]["readable"])
    def test_log_upserts_the_day_and_counts_clean_days(self):
        self.good(); s, paths = rc.write_signal_status(self.st()); self.assertEqual((s["days_checked"], s["days_clean"]), (1, 1))
        s, _ = rc.write_signal_status(self.st()); self.assertEqual(s["days_checked"], 1)
        self.alerts([]); bad = rc.signal_status(DAY, BASE, ledger_remote=False); s, _ = rc.write_signal_status(bad); self.assertEqual((s["days_checked"], s["days_clean"]), (1, 0))
        self.assertEqual(json.load(open(rc.SC_JSON))["day"], DAY); self.assertEqual(len(paths), 2)
    def test_publish_touches_only_the_two_files_and_reports_failure(self):
        calls = []
        def fake(cmd, **kw):
            calls.append((cmd, kw.get("env", {}).get("GIT_AUTHOR_NAME"))); rc_ = 1 if (cmd[3] == "diff") else 0
            return subprocess.CompletedProcess(cmd, rc_, "", "")
        with mock.patch("subprocess.run", fake):
            res = rc.publish([rc.SC_JSON, rc.SC_LOG], DAY)
        self.assertEqual(res, "published")
        adds = [c for c, _ in calls if c[3] == "add"]; self.assertEqual(adds[0][5:], ["signal_check.json", "signal_check_log.csv"])
        self.assertTrue(all(a == "IBT signal check" for _, a in calls))
        commit = [c for c, _ in calls if c[3] == "commit"][0]; self.assertEqual(commit[-2:], ["signal_check.json", "signal_check_log.csv"])
        def failing(cmd, **kw): return subprocess.CompletedProcess(cmd, 1 if cmd[3] in ("diff", "push") else 0, "", "rejected")
        with mock.patch("subprocess.run", failing): self.assertTrue(rc.publish([rc.SC_JSON, rc.SC_LOG], DAY).startswith("push failed"))
        def clean(cmd, **kw): return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch("subprocess.run", clean): self.assertEqual(rc.publish([rc.SC_JSON, rc.SC_LOG], DAY), "nothing new to publish")
if __name__ == "__main__": unittest.main()
