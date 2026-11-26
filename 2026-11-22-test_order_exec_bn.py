"""Tests for 2026-11-22-order_exec_bn.py. No network, no Kite, no real clock. Run: python3 2026-11-22-test_order_exec_bn.py"""
import os, sys, csv, unittest, datetime as dt, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import test_orders as TO, order_exec as X, safety as S, ticket as T
spec = importlib.util.spec_from_file_location("bn", os.path.join(HERE, "2026-11-22-order_exec_bn.py")); BN = importlib.util.module_from_spec(spec); spec.loader.exec_module(BN)
CBN = dict(symbol="BANKNIFTY26DECFUT", token=9, expiry="2026-12-29", lot=30, tick=0.05, ex="NFO")
FUTS = {"NSEBANK": [CBN], "NSEI": [TO.C]}
BN.BASELINE = "2026-11-24 09:15"          # tests use 24 Nov dates; the real baseline is 25 Nov

class T_(TO.Base):
    def test_rejected_order_does_not_block_backup(self):
        c = self.ctx(start="2026-11-24 15:00:05"); BN.run_backup(c)
        t = open(BN.OUT).read().replace(",FILLED,", ",REJECTED,"); open(BN.OUT, "w").write(t); self.assertIsNone(BN.live_state())
        c2 = self.ctx(start="2026-11-24 15:00:05"); self.assertEqual(BN.run_backup(c2), "backup round trip done")        # a rejected order opened nothing, so the backup may still run
    def test_ipv4_only(self):
        import socket, urllib3.util.connection as c
        self.assertEqual(c.allowed_gai_family(), socket.AF_INET)
    def ctx(self, start="2026-11-24 10:46:00", seed=None, fn=None):
        self.t0 = dt.datetime.fromisoformat(start); k = TO.FakeKite(fn or (lambda t: TO.book(62000.0)), self.ck); br = X.ShadowBroker(k, FUTS, seed=seed or {}); br.mode = "live"
        os.makedirs("data/live/bn_live", exist_ok=True)
        if not os.path.exists(BN.GO_FILE): open(BN.GO_FILE, "w").write("2026-11-24\n2026-11-25\n")      # gate dates used by the tests
        return X.Ctx(k, FUTS, br, lambda: self.t0 + dt.timedelta(seconds=self.ck.t), self.ck.now, self.ck.sleep, lambda t, b, p="default": self.sent.append((t, b, p)) or True, "", "", lambda c: 5,
                     alerts=BN.ALERTS_F, tickets=BN.TICKETS_F, out=BN.OUT)
    def bn_rows(self, specs):          # specs: (bar, kind, side, closes)
        al = []; tk = []
        for bar, kind, side, closes in specs:
            eid = "NSEBANK|%s|%s" % (bar, kind); al.append(self.al(eid, kind, side, bar, 62000.0, index="Bank Nifty", closes=closes)); tk.append(self.tk(eid, kind, side, 61999.0, 62000.0, (62031.0 if side == "BUY" else 61968.0), "BANKNIFTY26DECFUT", "Bank Nifty", 30))
        os.makedirs("data/live/bn_live", exist_ok=True)
        with open(BN.ALERTS_F, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["event_id", "logged_at_ist", "event", "index", "action", "signal_bar", "fill_time", "lot_size", "price", "stop", "reason", "closes_entry", "alert"]); [w.writerow(r) for r in al]
        with open(BN.TICKETS_F, "w", newline="") as f:
            w = csv.writer(f); w.writerow(T.HDR_T); [w.writerow(r) for r in tk]
    def rows(self): return BN._read(BN.OUT)

    def test_go_gate(self):
        self.assertFalse(BN.go_ok("2026-11-24")[0])                                   # no file
        open(BN.GO_FILE, "w").write("2026-11-23"); self.assertFalse(BN.go_ok("2026-11-24")[0])    # old date
        open(BN.GO_FILE, "w").write("2026-11-24"); self.assertTrue(BN.go_ok("2026-11-24")[0])
        self.assertFalse(BN.go_ok("2026-11-24", enabled=False)[0])                    # switch off
    def test_main_does_nothing_without_go_file(self):
        self.assertEqual(BN.main(["--events", "NSEBANK|2026-11-24 10:30|ENTRY"]), 0); self.assertFalse(os.path.exists(BN.OUT))
    def test_filtered_files_keep_only_bank_nifty_after_baseline(self):
        with open("data/live/alerts.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["event_id", "logged_at_ist", "event", "index", "action", "signal_bar", "fill_time", "lot_size", "price", "stop", "reason", "closes_entry", "alert"])
            w.writerow(self.al("NSEI|2026-11-24 10:30|ENTRY", "ENTRY", "BUY", "2026-11-24 10:30", 25000.0)); w.writerow(self.al("NSEBANK|2026-11-19 10:30|ENTRY", "ENTRY", "BUY", "2026-11-19 10:30", 62000.0, index="Bank Nifty"))
            w.writerow(self.al("NSEBANK|2026-11-24 10:30|ENTRY", "ENTRY", "SELL", "2026-11-24 10:30", 62000.0, index="Bank Nifty"))
        self.tickets([self.tk("NSEBANK|2026-11-24 10:30|ENTRY", "ENTRY", "SELL", index="Bank Nifty"), self.tk("NSEBANK|2026-11-19 10:30|ENTRY", "ENTRY", "BUY", index="Bank Nifty")])
        n = BN.filtered_files(); self.assertEqual(n, (1, 1)); self.assertEqual([r["event_id"] for r in BN._read(BN.ALERTS_F)], ["NSEBANK|2026-11-24 10:30|ENTRY"])
    def test_reversal_skips_exit_of_old_position_and_takes_entry(self):
        self.bn_rows([("2026-11-24 10:30", "EXIT", "SELL", "NSEBANK|2026-11-19 10:30|ENTRY"), ("2026-11-24 10:30", "ENTRY", "SELL", "")]); c = self.ctx(); S.os.makedirs("data/live", exist_ok=True)
        BN.run_events(c, ["NSEBANK|2026-11-24 10:30|EXIT", "NSEBANK|2026-11-24 10:30|ENTRY"])
        r = {(x["kind"]): x for x in self.rows()}; self.assertEqual(r["EXIT"]["status"], "SKIPPED"); self.assertEqual(r["ENTRY"]["status"], "FILLED")
        self.assertEqual(BN.live_state()["side"], "SELL"); self.assertEqual(c.broker.positions(), {"BANKNIFTY26DECFUT": -30}); self.assertEqual(r["ENTRY"]["position_check"], "ok"); self.assertFalse(os.path.exists("data/live/STOP"))
    def test_entry_before_baseline_is_ignored(self):
        self.bn_rows([("2026-11-23 10:30", "ENTRY", "BUY", "")]); c = self.ctx(); BN.run_events(c, ["NSEBANK|2026-11-23 10:30|ENTRY"])
        self.assertEqual([x["status"] for x in self.rows()], ["SKIPPED"]); self.assertEqual(c.broker.positions(), {})
    def test_exit_taken_when_live_position_exists_and_no_second_entry(self):
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(); BN.run_events(c, ["NSEBANK|2026-11-24 10:30|ENTRY"]); self.assertEqual(BN.live_state()["side"], "BUY")
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", ""), ("2026-11-25 11:00", "EXIT", "SELL", "NSEBANK|2026-11-24 10:30|ENTRY"), ("2026-11-25 11:00", "ENTRY", "SELL", ""), ("2026-11-25 11:05", "ENTRY", "BUY", "")])
        self.t0 = dt.datetime(2026, 11, 25, 11, 16)
        BN.run_events(c, ["NSEBANK|2026-11-25 11:00|EXIT", "NSEBANK|2026-11-25 11:00|ENTRY"])
        k = [(x["kind"], x["status"]) for x in self.rows()]; self.assertEqual(k, [("ENTRY", "FILLED"), ("EXIT", "FILLED"), ("ENTRY", "SKIPPED")]); self.assertIsNone(BN.live_state())
        self.assertIn("already taken", self.rows()[-1]["block_reasons"]); self.assertEqual(c.broker.positions(), {})      # one live test trade in total: no reversal entry after the exit
    def test_exit_allowed_on_non_gate_day_but_entry_is_not(self):
        open(BN.GO_FILE, "w").write("2026-11-24\n"); self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(); BN.run_events(c, ["NSEBANK|2026-11-24 10:30|ENTRY"]); self.assertEqual(BN.live_state()["side"], "BUY")
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", ""), ("2026-11-25 11:00", "EXIT", "SELL", "NSEBANK|2026-11-24 10:30|ENTRY")]); self.t0 = dt.datetime(2026, 11, 25, 11, 16)
        BN.run_events(c, ["NSEBANK|2026-11-25 11:00|EXIT"]); self.assertEqual(self.rows()[-1]["status"], "FILLED"); self.assertIsNone(BN.live_state())
    def test_entry_on_non_gate_day_is_skipped_when_flat(self):
        open(BN.GO_FILE, "w").write("2026-11-24\n"); self.bn_rows([("2026-11-25 10:30", "ENTRY", "BUY", "")]); c = self.ctx(start="2026-11-25 10:46:00"); BN.run_events(c, ["NSEBANK|2026-11-25 10:30|ENTRY"])
        self.assertEqual(self.rows()[-1]["status"], "SKIPPED"); self.assertIn("not a live entry day", self.rows()[-1]["block_reasons"]); self.assertEqual(c.broker.positions(), {})
    def test_main_exit_runs_on_non_gate_day_but_backup_does_not(self):
        self.assertEqual(BN.main(["--backup"]), 0); self.assertFalse(os.path.exists(BN.OUT))
    def test_other_indexes_never_traded(self):
        c = self.ctx(); BN.run_events(c, ["NSEI|2026-11-24 10:30|ENTRY"]); self.assertEqual(self.rows(), []); self.assertEqual(c.broker.positions(), {})
    def test_stray_kite_position_stops_everything(self):
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(seed={"BANKNIFTY26DECFUT": 30}); BN.run_events(c, ["NSEBANK|2026-11-24 10:30|ENTRY"])
        self.assertEqual(self.rows(), []); self.assertTrue(os.path.exists("data/live/STOP")); self.assertTrue(any(s[2] == "high" for s in self.sent)); self.assertEqual(c.broker.positions(), {"BANKNIFTY26DECFUT": 30})
    def test_backup_round_trip_logs_both_legs_against_candle_close(self):
        c = self.ctx(start="2026-11-24 15:00:05"); out = BN.run_backup(c); self.assertEqual(out, "backup round trip done")
        r = self.rows(); self.assertEqual([(x["kind"], x["status"]) for x in r], [("ENTRY", "FILLED"), ("EXIT", "FILLED")]); self.assertTrue(all(x["ref_source"] for x in r))
        self.assertEqual(c.broker.positions(), {}); self.assertIsNone(BN.live_state()); self.assertFalse(os.path.exists("data/live/STOP")); self.assertGreater(self.ck.t, 290)
        self.assertIn("already placed", BN.run_backup(c))                                  # never twice in a day
    def test_backup_not_run_outside_window_or_after_a_real_trade(self):
        c = self.ctx(start="2026-11-24 14:30:00"); self.assertIn("outside", BN.run_backup(c)); self.assertEqual(self.rows(), [])
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(start="2026-11-24 10:46:00"); BN.run_events(c, ["NSEBANK|2026-11-24 10:30|ENTRY"])
        self.t0 = dt.datetime(2026, 11, 24, 15, 0, 5); self.ck.t = 0; self.assertIn("already placed", BN.run_backup(c))
    def test_backup_not_run_when_kill_switch_is_on(self):
        S.latch_stop("test", dt.datetime(2026, 11, 24, 9, 0)); c = self.ctx(start="2026-11-24 15:00:05"); BN.run_backup(c)
        self.assertEqual([x["status"] for x in self.rows()], ["BLOCKED"])
if __name__ == "__main__": unittest.main(verbosity=1)
