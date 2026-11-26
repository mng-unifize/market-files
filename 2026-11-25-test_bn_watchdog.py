import importlib, datetime as dt, unittest
W = importlib.import_module("2026-11-25-bn_watchdog")
NOW = dt.datetime(2026, 11, 25, 9, 50)
def al(eid="NSEBANK|2026-11-25 09:35|ENTRY", ev="ENTRY", at="2026-11-25 09:40", alert="sent"): return dict(event_id=eid, event=ev, action="BUY", index="Bank Nifty", signal_bar="2026-11-25 09:35", logged_at_ist=at, alert=alert)
def out(eid="NSEBANK|2026-11-25 09:35|ENTRY", status="FILLED", kind="ENTRY", run="2026-11-25 09:40:26", why=""): return dict(event_id=eid, status=status, kind=kind, side="BUY", contract="BANKNIFTY26DECFUT", run_at_ist=run, block_reasons=why, note="")
class T(unittest.TestCase):
    def keys(self, *a): return [p[0].split("|")[-1] for p in W.problems(*a)]
    def test_rejected_order_alerts(self): self.assertEqual(self.keys(NOW, True, False, "", [al()], [out(status="REJECTED")]), ["REJECTED"])
    def test_message_has_reason_and_time(self):
        p = W.problems(NOW, True, False, "", [al()], [out(status="REJECTED", why="PermissionException: IP not allowed")])[0][2]
        self.assertIn("09:40:26", p); self.assertIn("PermissionException: IP not allowed", p); self.assertIn("09:35", p)
    def test_filled_is_quiet(self): self.assertEqual(self.keys(NOW, True, False, "", [al()], [out()]), [])
    def test_skipped_is_quiet(self): self.assertEqual(self.keys(NOW, True, False, "", [al()], [out(status="SKIPPED")]), [])
    def test_blocked_not_filled_partial_alert(self):
        for s in ("BLOCKED", "NOT_FILLED", "PARTIAL"): self.assertEqual(self.keys(NOW, True, False, "", [al()], [out(status=s)]), [s])
    def test_no_result_after_grace(self): self.assertEqual(self.keys(NOW, True, False, "", [al()], []), ["none"])
    def test_no_result_inside_grace_is_quiet(self): self.assertEqual(self.keys(dt.datetime(2026, 11, 25, 9, 42), True, False, "", [al()], []), [])
    def test_stop_file_alerts(self): self.assertEqual(len(W.problems(NOW, True, False, "AUTO STOP x", [], [])), 1)
    def test_non_gate_day_flat_ignores_signals(self): self.assertEqual(self.keys(NOW, False, False, "", [al()], []), [])
    def test_open_position_on_non_gate_day_still_watched(self): self.assertEqual(self.keys(NOW, False, True, "", [al(eid="x|EXIT", ev="EXIT")], []), ["none"])
    def test_log_only_rows_ignored(self): self.assertEqual(self.keys(NOW, True, False, "", [al(alert="log only")], []), [])
    def test_nothing_filled_after_backup_window(self): self.assertEqual(self.keys(dt.datetime(2026, 11, 25, 15, 14), True, False, "", [], []), ["nofill"])
    def test_filled_today_no_nofill(self): self.assertEqual(self.keys(dt.datetime(2026, 11, 25, 15, 14), True, False, "", [], [out()]), [])
    def test_old_day_signal_ignored(self): self.assertEqual(self.keys(NOW, True, False, "", [al(at="2026-11-24 09:40")], []), [])
if __name__ == "__main__": unittest.main()
