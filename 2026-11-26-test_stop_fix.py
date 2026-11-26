"""Tests for the 26 Nov 2026 stop fix: a rehearsal fault must never stop live trading, and an AUTOMATIC stop must never block a live exit.
No network, no Kite, no real clock, runs in a temp folder. Run: python3 2026-11-26-test_stop_fix.py"""
import os, sys, json, unittest, datetime as dt, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
import test_orders as TO, order_exec as X, safety as S, ticket as T
spec = importlib.util.spec_from_file_location("bntest", os.path.join(HERE, "2026-11-22-test_order_exec_bn.py")); BNT = importlib.util.module_from_spec(spec); spec.loader.exec_module(BNT)
BN = BNT.BN
SYM = "BANKNIFTY26DECFUT"; ENTRY_ID = "NSEBANK|2026-11-24 10:30|ENTRY"; EXIT_ID = "NSEBANK|2026-11-25 11:00|EXIT"
NOW = dt.datetime(2026, 11, 25, 11, 0)

class Live(TO.Base):
    ctx = BNT.T_.ctx; bn_rows = BNT.T_.bn_rows; rows = BNT.T_.rows
    def open_long(self):
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(); BN.run_events(c, [ENTRY_ID])
        self.assertEqual(BN.live_state()["side"], "BUY"); self.assertEqual(c.broker.positions(), {SYM: 30}); return c
    def exit_rows(self):
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", ""), ("2026-11-25 11:00", "EXIT", "SELL", ENTRY_ID)]); self.t0 = dt.datetime(2026, 11, 25, 11, 16)
    def stop(self, text=None):
        if text is None: S.latch_stop("test: simulated automatic stop", NOW)
        else: open(S.STOP_FILE, "w").write(text)
    def run_exit(self, c): n = len(self.rows()); BN.run_events(c, [EXIT_ID]); return self.rows()[n:]

    # ---- the point of the fix ----
    def test_live_exit_goes_through_an_automatic_stop(self):
        c = self.open_long(); self.exit_rows(); self.stop(); new = self.run_exit(c)
        self.assertEqual([(r["kind"], r["status"]) for r in new], [("EXIT", "FILLED")]); self.assertEqual(new[0]["position_check"], "ok")
        self.assertIsNone(BN.live_state()); self.assertEqual(c.broker.positions(), {}); self.assertTrue(os.path.exists(S.STOP_FILE))     # flat, and the stop is still on for entries
        self.assertIn("automatic STOP", new[0]["note"]); self.assertTrue(any("automatic STOP" in s[1] for s in self.sent)); self.assertTrue(self.sent[-1][0].startswith("[LIVE]"))
    def test_live_exit_is_blocked_by_a_stop_made_by_hand(self):
        for text in ("x", "", "STOP from phone at 2026-11-25 10:00:00"):
            with self.subTest(text=text):
                c = self.open_long(); self.exit_rows(); self.stop(text); new = self.run_exit(c)
                self.assertEqual([(r["kind"], r["status"]) for r in new], [("EXIT", "BLOCKED")]); self.assertIn("kill switch", new[0]["block_reasons"]); self.assertEqual(c.broker.positions(), {SYM: 30})
                self.assertIn("DO THIS BY HAND", self.sent[-1][1]); self.assertEqual(self.sent[-1][2], "high")
                os.remove(S.STOP_FILE); os.remove(BN.OUT); os.remove("data/live/sim_positions.json"); self.sent.clear()
    def test_live_entry_is_blocked_by_an_automatic_stop(self):
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(); self.stop(); BN.run_events(c, [ENTRY_ID])
        r = self.rows(); self.assertEqual([x["status"] for x in r], ["BLOCKED"]); self.assertIn("kill switch", r[0]["block_reasons"]); self.assertEqual(c.broker.positions(), {}); self.assertIsNone(BN.live_state())
    def test_flat_account_with_automatic_stop_never_sells(self):
        self.exit_rows(); c = self.ctx(); self.stop(); new = self.run_exit(c)
        self.assertEqual([r["status"] for r in new], ["SKIPPED"]); self.assertEqual(c.broker.positions(), {})                  # nothing sold, no short opened
    def test_kite_shows_more_than_the_log_sends_nothing(self):
        c = self.open_long(); self.exit_rows(); json.dump({SYM: 60}, open("data/live/sim_positions.json", "w")); self.stop(); new = self.run_exit(c)
        self.assertEqual(new, []); self.assertEqual(c.broker.positions(), {SYM: 60}); self.assertTrue(any(s[2] == "high" and "disagree" in s[1] for s in self.sent))
    def test_kite_flat_but_log_long_sends_nothing_and_opens_no_short(self):
        c = self.open_long(); self.exit_rows(); json.dump({}, open("data/live/sim_positions.json", "w")); self.stop(); new = self.run_exit(c)
        self.assertEqual(new, []); self.assertEqual(c.broker.positions(), {}); self.assertTrue(any(s[2] == "high" for s in self.sent))
    def test_the_rehearsal_runner_still_obeys_an_automatic_stop_on_exits(self):
        c = self.open_long(); self.exit_rows(); c.broker.mode = "shadow"; self.stop(); new = self.run_exit(c)
        self.assertEqual([r["status"] for r in new], ["BLOCKED"]); self.assertIn("kill switch", new[0]["block_reasons"])
    def test_live_runner_ignores_the_rehearsal_stop(self):
        self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(); S.latch_stop("rehearsal fault", NOW, shadow=True); BN.run_events(c, [ENTRY_ID])
        self.assertEqual([r["status"] for r in self.rows()], ["FILLED"]); self.assertFalse(os.path.exists(S.STOP_FILE))

    def test_decision_table_live(self):
        """Every combination of order kind and stop kind, against the rule: only an EXIT may pass an AUTOMATIC stop; a stop made by hand blocks everything."""
        want = {("ENTRY", "none"): "FILLED", ("ENTRY", "auto"): "BLOCKED", ("ENTRY", "phone"): "BLOCKED", ("ENTRY", "hand"): "BLOCKED",
                ("EXIT", "none"): "FILLED", ("EXIT", "auto"): "FILLED", ("EXIT", "phone"): "BLOCKED", ("EXIT", "hand"): "BLOCKED"}
        text = {"phone": "STOP from phone at 2026-11-25 10:00:00", "hand": "x"}
        for (kind, st), status in sorted(want.items()):
            with self.subTest(kind=kind, stop=st):
                for p in ("data/live/STOP", BN.OUT, "data/live/sim_positions.json"):
                    if os.path.exists(p): os.remove(p)
                self.sent.clear()
                if kind == "ENTRY":
                    self.bn_rows([("2026-11-24 10:30", "ENTRY", "BUY", "")]); c = self.ctx(); ids = [ENTRY_ID]
                else:
                    c = self.open_long(); self.exit_rows(); ids = [EXIT_ID]
                if st == "auto": self.stop()
                elif st != "none": self.stop(text[st])
                n = len(self.rows()); BN.run_events(c, ids); new = self.rows()[n:]
                self.assertEqual([r["status"] for r in new], [status])

    # ---- the real incident, replayed ----
    def test_26_nov_incident_replay(self):
        """Live is long 30. The rehearsal book is wrong (60), so the rehearsal exit raises a mismatch. Live must still be able to exit."""
        c = self.open_long(); self.exit_rows()
        rows_a = [self.al(ENTRY_ID, "ENTRY", "BUY", "2026-11-24 10:30", 62000.0, index="Bank Nifty"), self.al(EXIT_ID, "EXIT", "SELL", "2026-11-25 11:00", 62000.0, index="Bank Nifty", closes=ENTRY_ID)]
        rows_t = [self.tk(ENTRY_ID, "ENTRY", "BUY", 61999.0, 62000.0, 62031.0, SYM, "Bank Nifty", 30), self.tk(EXIT_ID, "EXIT", "SELL", 61999.0, 62000.0, 61968.0, SYM, "Bank Nifty", 30)]
        self.alerts(rows_a); self.tickets(rows_t)
        k = TO.FakeKite(lambda t: TO.book(62000.0), self.ck); shadow_book = "data/live/sim_rehearsal.json"; json.dump({SYM: 60}, open(shadow_book, "w"))
        sb = X.ShadowBroker(k, BNT.FUTS, book=shadow_book); self.assertEqual(sb.mode, "shadow")
        sctx = X.Ctx(k, BNT.FUTS, sb, c.now_fn, self.ck.now, self.ck.sleep, lambda t, b, p="default": self.sent.append((t, b, p)) or True, "", "", lambda code: 5)
        X.run_batch(sctx, [EXIT_ID])                                                                                             # the rehearsal runner goes first, as on 26 Nov
        self.assertTrue(os.path.exists(S.STOP_SHADOW)); self.assertFalse(os.path.exists(S.STOP_FILE)); self.assertTrue(any("STOPPED" in s[0] and s[0].startswith("[REHEARSAL]") for s in self.sent))
        new = self.run_exit(c)                                                                                                   # then the live runner
        self.assertEqual([(r["kind"], r["status"]) for r in new], [("EXIT", "FILLED")]); self.assertIsNone(BN.live_state()); self.assertEqual(c.broker.positions(), {})

class Safety(TO.Base):
    def test_rehearsal_stop_is_seen_by_rehearsal_only(self):
        S.latch_stop("r", NOW, shadow=True); self.assertTrue(os.path.exists(S.STOP_SHADOW)); self.assertFalse(os.path.exists(S.STOP_FILE))
        self.assertFalse(S.kill_switch()[0]); on, why, _ = S.kill_switch(shadow=True); self.assertTrue(on); self.assertTrue(why.startswith("rehearsal STOP file is present")); self.assertFalse(S.stop_is_auto(why))
    def test_main_stop_stops_the_rehearsal_too(self):
        S.latch_stop("live fault", NOW); self.assertTrue(S.kill_switch()[0]); self.assertTrue(S.kill_switch(shadow=True)[0]); self.assertFalse(os.path.exists(S.STOP_SHADOW))
    def test_latch_keeps_the_first_reason(self):
        S.latch_stop("first", NOW); S.latch_stop("second", NOW); self.assertIn("first", open(S.STOP_FILE).read()); self.assertNotIn("second", open(S.STOP_FILE).read())
    def test_stop_is_auto_only_for_automatic_latches(self):
        S.latch_stop("why", NOW); self.assertTrue(S.stop_is_auto(S.kill_switch()[1])); os.remove(S.STOP_FILE)
        open(S.STOP_FILE, "w").write("x"); self.assertFalse(S.stop_is_auto(S.kill_switch()[1])); open(S.STOP_FILE, "w").write(""); self.assertFalse(S.stop_is_auto(S.kill_switch()[1])); os.remove(S.STOP_FILE)
        msgs = [dict(id="p1", message="STOP", event="message")]; self.assertFalse(S.stop_is_auto(S.kill_switch("t", fetch=lambda t, b: msgs)[1])); self.assertFalse(S.stop_is_auto(S.kill_switch()[1]))   # phone stop, first and later reads
        self.assertFalse(S.stop_is_auto("")); self.assertFalse(S.stop_is_auto("daily loss stop: ..."))
    def test_automatic_stop_text_keeps_its_prefix_when_long(self):
        S.latch_stop("y" * 400, NOW); self.assertTrue(S.stop_is_auto(S.kill_switch()[1]))

class Summary(TO.EndToEnd):
    def test_rehearsal_stop_shows_in_the_daily_summary_with_the_right_file(self):
        self.entry_only(); X.run_batch(self.ctx(seed={"NIFTY26NOVFUT": 130}), ["NSEI|2026-11-13 09:15|ENTRY"]); d = X.summary_dict("2026-11-13")
        self.assertTrue(d["stopped"]); self.assertIn("AUTO STOP", d["stop_reason"]); self.assertTrue(any("STOP_SHADOW" in t["detail"] for t in d["triggers"])); self.assertFalse(os.path.exists(S.STOP_FILE))
    def test_rehearsal_fault_message_says_live_is_not_affected(self):
        self.entry_only(); X.run_batch(self.ctx(seed={"NIFTY26NOVFUT": 130}), ["NSEI|2026-11-13 09:15|ENTRY"]); m = self.sent[-1]
        self.assertEqual(m[2], "high"); self.assertIn("live trading is not affected", m[1]); self.assertIn("STOP_SHADOW", m[1])

if __name__ == "__main__": unittest.main(verbosity=1)
