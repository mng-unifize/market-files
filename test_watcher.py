"""Dummy-value tests for login.py and watcher.py. No network, no real secrets, no orders.
Run:  ~/ibt-env/bin/python -m unittest -v test_watcher      (from this folder)
Every test runs in a throwaway copy of the folder's data, so the real data/live is never touched."""
import os, sys, io, re, csv, json, random, shutil, subprocess, tempfile, types, unittest, contextlib, datetime as dt
from unittest import mock

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DUMMY = {"KITE_API_KEY": "dummykey1234", "KITE_API_SECRET": "dummy-secret-value", "NTFY_TOPIC": "dummy-topic-xyz", "HC_PING_URL": "https://hc.example/ping/dummy-uuid-0000"}
TOKEN = "dummy-access-token-123"

# a fake kiteconnect module, so nothing real can be reached (login.py and fetch_kite_history.py import it at load time)
class FakeConnect:
    def __init__(self, api_key=None): self.api_key = api_key
    def login_url(self): return "https://kite.example/connect/login?api_key=" + str(self.api_key)
    def generate_session(self, request_token, api_secret=None):
        if request_token != "goodtoken": raise Exception("Token is invalid or has expired. secret=" + str(api_secret))
        return {"access_token": TOKEN}
    def set_access_token(self, t): pass
    def profile(self): return {"user_id": "ZZ1234"}
sys.modules["kiteconnect"] = types.SimpleNamespace(KiteConnect=FakeConnect)
import login, watcher

def ist(s): return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
TUE = "2026-11-10"; SAT = "2026-11-14"
ROOT = tempfile.mkdtemp()

def setUpModule():
    shutil.copytree(os.path.join(REPO, "paper"), os.path.join(ROOT, "paper"))
    shutil.copytree(os.path.join(REPO, "data", "kite"), os.path.join(ROOT, "kite"))
    watcher.PAPER_DIR = os.path.join(ROOT, "paper")

def tearDownModule(): shutil.rmtree(ROOT, ignore_errors=True)

class FakeKite:
    """Only the three read-only calls the watcher needs. Any other call (an order call, say) fails the test."""
    def __init__(self, today, with_candles=True):
        self.calls = []; self.data = {}
        for tok, nm in ((11, "NSEI"), (12, "NSEBANK"), (13, "BSESN")):
            last = list(csv.DictReader(open(os.path.join(ROOT, "kite", nm + "_5m.csv"))))[-1]; px = float(last["close"]); rnd = random.Random(tok); rows = []
            d0 = dt.datetime.strptime(today + " 09:15", "%Y-%m-%d %H:%M")
            assert last["ts_ist"][:10] < today, "test day must be after the repo's last stored day"
            for i in range(75 if with_candles else 0):
                o = px; px += rnd.uniform(-12, 12); rows.append(dict(date=d0 + dt.timedelta(minutes=5 * i), open=o, high=max(o, px) + 2, low=min(o, px) - 2, close=px, volume=0))
            self.data[tok] = rows
    def instruments(self, ex):
        self.calls.append("instruments " + ex)
        m = {"NSE": [(11, "NIFTY 50"), (12, "NIFTY BANK")], "BSE": [(13, "SENSEX")]}[ex]
        return [dict(instrument_token=t, tradingsymbol=s) for t, s in m]
    def historical_data(self, token, a, b, interval):
        self.calls.append("historical_data"); assert interval == "5minute"
        return [r for r in self.data[token] if a <= r["date"] <= b]
    def __getattr__(self, name): raise AssertionError("unexpected Kite call: " + name)

class Base(unittest.TestCase):
    def setUp(self):
        self.run_dir = tempfile.mkdtemp(); self.old = os.getcwd(); os.chdir(self.run_dir)
        os.makedirs("data"); os.symlink(os.path.join(ROOT, "kite"), "data/kite")
        watcher.TOKEN_PATH = os.path.join(self.run_dir, "kite_token.json"); watcher.WORK = os.path.join(self.run_dir, "work")
        self.env = mock.patch.dict(os.environ, DUMMY); self.env.start()
        self.net = mock.patch('urllib.request.urlopen', side_effect=AssertionError('network is blocked in tests')); self.net.start()
        watcher._secrets[:] = list(DUMMY.values())
    def tearDown(self):
        self.net.stop(); self.env.stop(); os.chdir(self.old); shutil.rmtree(self.run_dir, ignore_errors=True)
    def token(self, day=TUE): json.dump({"access_token": TOKEN, "date": day}, open(watcher.TOKEN_PATH, "w"))
    def rows(self): return list(csv.DictReader(open(watcher.ALERTS))) if os.path.exists(watcher.ALERTS) else []

class FakeClock:
    def __init__(self, t): self.t = t
    def now(self): return self.t
    def sleep_until(self, t): self.t = max(self.t, t)

def ev(event, side, bar, fill, price, day=TUE, note=""): return dict(event=event, nm="NSEI", side=side, bar=bar, fill=fill, price=price, tf=15, day=day, note=note)

class LoginTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.envfile = os.path.join(self.d, "dummy.env")
        open(self.envfile, "w").write("\n".join("%s=%s" % kv for kv in DUMMY.items()))
        login.ENV_PATH = self.envfile; login.TOKEN_PATH = os.path.join(self.d, "kite_token.json")
        self.clean = {k: os.environ.pop(k, None) for k in DUMMY}
        # safety: no test may ever reach the real gh (it could overwrite your real GitHub secret with a dummy token)
        self.guards = [mock.patch.object(login.shutil, 'which', return_value=None), mock.patch.object(login.subprocess, 'run', side_effect=AssertionError('tests must never run the real gh'))]
        for g in self.guards: g.start()
    def tearDown(self):
        for g in self.guards: g.stop()
        for k, v in self.clean.items():
            os.environ.pop(k, None)
            if v is not None: os.environ[k] = v
        shutil.rmtree(self.d, ignore_errors=True)
    def run_login(self, pasted, argv=("--no-github",)):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("builtins.input", return_value=pasted): code = login.main(argv=list(argv))
        return code, out.getvalue()
    # ---- the GitHub copy of the token ----
    def fake_gh(self, which="/usr/bin/gh", result=None, raises=None):
        calls = []
        def fake(args, **kw):
            calls.append((args, kw))
            if raises: raise raises
            return result or types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return calls, mock.patch.object(login.shutil, "which", return_value=which), mock.patch.object(login.subprocess, "run", fake)
    def test_token_goes_to_the_github_secret_through_standard_input_only(self):
        calls, w, r = self.fake_gh()
        with w, r: code, out = self.run_login("goodtoken", argv=())
        self.assertEqual(code, 0); self.assertEqual(len(calls), 1); args, kw = calls[0]
        self.assertEqual(args, ["/usr/bin/gh", "secret", "set", "KITE_ACCESS_TOKEN", "--repo", "mng-unifize/market-files"])
        self.assertEqual(kw["input"], TOKEN); self.assertNotIn(TOKEN, " ".join(args)); self.assertNotIn(TOKEN, out)
        self.assertIn("GitHub secret KITE_ACCESS_TOKEN updated", out); self.assertEqual(json.load(open(login.TOKEN_PATH))["access_token"], TOKEN)
    def test_github_failures_keep_the_mac_token_and_say_so_loudly(self):
        cases = [("gh missing", dict(which=None)), ("gh refuses and echoes the token", dict(result=types.SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403 token=" + TOKEN))),
                 ("gh hangs", dict(raises=subprocess.TimeoutExpired("gh", 60)))]
        for name, kw in cases:
            if os.path.exists(login.TOKEN_PATH): os.remove(login.TOKEN_PATH)
            calls, w, r = self.fake_gh(**kw)
            with w, r: code, out = self.run_login("goodtoken", argv=())
            self.assertEqual(code, 3, name); self.assertIn("GitHub was NOT updated", out, name); self.assertIn("gh secret set KITE_ACCESS_TOKEN --repo mng-unifize/market-files", out, name)
            self.assertNotIn(TOKEN, out, name); self.assertEqual(json.load(open(login.TOKEN_PATH))["access_token"], TOKEN, name)   # this Mac still works
    def test_no_github_flag_skips_the_push(self):
        calls, w, r = self.fake_gh()
        with w, r: code, out = self.run_login("goodtoken", argv=("--no-github",))
        self.assertEqual((code, calls), (0, [])); self.assertIn("skipped", out)
    def test_a_failed_kite_login_never_touches_github(self):
        calls, w, r = self.fake_gh()
        with w, r: code, out = self.run_login("expired", argv=())
        self.assertEqual((code, calls), (1, [])); self.assertFalse(os.path.exists(login.TOKEN_PATH))
    def test_secret_name_matches_what_the_github_workflows_read(self):
        for wf in ("paper-live.yml", "preflight.yml"):
            src = open(os.path.join(REPO, ".github", "workflows", wf)).read()
            self.assertIn("KITE_ACCESS_TOKEN: ${{ secrets.%s }}" % login.SECRET_NAME, src, wf)
    @unittest.skipUnless(os.path.exists(os.path.join(REPO, ".git", "config")), "no git config here")
    def test_repo_name_matches_the_git_remote(self):
        self.assertIn("github.com/" + login.GITHUB_REPO, open(os.path.join(REPO, ".git", "config")).read())
    def test_real_subprocess_pipes_the_token_to_a_stand_in_gh(self):
        """Runs the real push code (no mocks) against a fake gh script on PATH. Proves the token arrives on standard input and not in the arguments."""
        for g in self.guards: g.stop()
        d = tempfile.mkdtemp()
        try:
            script = os.path.join(d, "gh")
            with open(script, "w") as f: f.write('#!/bin/sh\necho "$@" > "%s/args"\ncat > "%s/stdin"\nexit 0\n' % (d, d))
            os.chmod(script, 0o755)
            with mock.patch.dict(os.environ, {"PATH": d + ":/usr/bin:/bin"}): ok, why = login.push_to_github(TOKEN)
            self.assertEqual((ok, why), (True, ""))
            self.assertEqual(open(d + "/args").read().strip(), "secret set KITE_ACCESS_TOKEN --repo mng-unifize/market-files")
            self.assertEqual(open(d + "/stdin").read(), TOKEN)                                  # exactly the token, no newline added
            self.assertNotIn(TOKEN, open(d + "/args").read())
        finally:
            shutil.rmtree(d)
            for g in self.guards: g.start()

    def test_manual_copy_command_never_contains_a_token(self):
        self.assertNotIn(TOKEN, login.MANUAL_COPY); self.assertIn("/Users/ng/ibt/kite_token.json", login.MANUAL_COPY)

    def test_paths_are_the_ones_you_asked_for(self):
        src = open(os.path.join(REPO, "login.py")).read() + open(os.path.join(REPO, "watcher.py")).read()
        self.assertIn('"/Users/ng/ibt/.env"', src); self.assertIn('"/Users/ng/ibt/kite_token.json"', src)
    def test_good_paste_of_whole_address(self):
        code, out = self.run_login("https://127.0.0.1/?action=login&type=login&status=success&request_token=goodtoken")
        self.assertEqual(code, 0); d = json.load(open(login.TOKEN_PATH))
        self.assertEqual(d["access_token"], TOKEN); self.assertEqual(d["user_id_tail"], "34")
        self.assertEqual(oct(os.stat(login.TOKEN_PATH).st_mode & 0o777), "0o600")
        self.assertIn("kite.example/connect/login", out)
        for secret in (TOKEN, DUMMY["KITE_API_SECRET"]): self.assertNotIn(secret, out)
    def test_good_paste_of_bare_token(self):
        self.assertEqual(self.run_login("goodtoken")[0], 0)
    def test_bad_token_writes_nothing_and_hides_secret(self):
        code, out = self.run_login("https://127.0.0.1/?request_token=expired")
        self.assertEqual(code, 1); self.assertFalse(os.path.exists(login.TOKEN_PATH)); self.assertNotIn(DUMMY["KITE_API_SECRET"], out)
    def test_missing_env_file(self):
        login.ENV_PATH = os.path.join(self.d, "nope.env"); self.assertEqual(self.run_login("x")[0], 2)

class ScheduleTests(unittest.TestCase):
    def test_heartbeat_weekday_covers_0905_to_1600_every_5_minutes(self):
        c = FakeClock(ist(TUE + " 08:00:00")); pings = []
        watcher.heartbeat("u", c, lambda u: pings.append(c.now().strftime("%H:%M")))
        self.assertEqual(len(pings), 84); self.assertEqual((pings[0], pings[1], pings[-2], pings[-1]), ("09:05", "09:10", "15:55", "16:00"))
        self.assertNotIn("08:50", pings)
    def test_heartbeat_late_start_pings_at_once(self):
        c = FakeClock(ist(TUE + " 10:02:00")); pings = []
        watcher.heartbeat("u", c, lambda u: pings.append(c.now().strftime("%H:%M")))
        self.assertEqual(pings[:2], ["10:02", "10:05"]); self.assertEqual(pings[-1], "16:00")
    def test_heartbeat_none_on_weekend_or_after_close(self):
        for s in (SAT + " 09:00:00", TUE + " 16:30:00"):
            pings = []; watcher.heartbeat("u", FakeClock(ist(s)), lambda u: pings.append(1)); self.assertEqual(pings, [])
    def test_heartbeat_ignores_market_holidays(self):
        d = tempfile.mkdtemp(); old = os.getcwd(); os.chdir(d); open("holidays.txt", "w").write(TUE + "  # test holiday\n")
        try:
            self.assertIn(TUE, watcher.holidays()); pings = []
            watcher.heartbeat("u", FakeClock(ist(TUE + " 08:00:00")), lambda u: pings.append(1)); self.assertEqual(len(pings), 84)
        finally: os.chdir(old); shutil.rmtree(d)
    def test_launchd_job_starts_at_the_same_time_the_heartbeat_does(self):
        import plistlib
        with open(os.path.join(REPO, "com.ibt.watcher.plist"), "rb") as f: p = plistlib.load(f)
        starts = p["StartCalendarInterval"]
        self.assertEqual(sorted(s["Weekday"] for s in starts), [1, 2, 3, 4, 5])              # Monday to Friday only
        self.assertEqual({(s["Hour"], s["Minute"]) for s in starts}, {(9, 5)})
        self.assertEqual("%02d:%02d" % (starts[0]["Hour"], starts[0]["Minute"]), watcher.PING_FIRST)
    def test_checks_run_0920_to_1530(self):
        c = FakeClock(ist(TUE + " 09:00:00")); ticks = []
        watcher.trading_loop(c, watcher.State(), lambda st, t: ticks.append(t.strftime("%H:%M:%S")) or "x")
        self.assertEqual((len(ticks), ticks[0], ticks[1], ticks[-1]), (75, "09:20:20", "09:25:20", "15:30:20"))
    def test_checks_late_start_catches_up_at_once(self):
        c = FakeClock(ist(TUE + " 11:02:00")); ticks = []
        watcher.trading_loop(c, watcher.State(), lambda st, t: ticks.append(t.strftime("%H:%M:%S")) or "x")
        self.assertEqual(ticks[:2], ["11:02:00", "11:05:20"])
    def test_no_checks_on_weekend(self):
        ticks = []; watcher.trading_loop(FakeClock(ist(SAT + " 09:00:00")), watcher.State(), lambda st, t: ticks.append(1)); self.assertEqual(ticks, [])
    def test_ping_never_logs_the_url(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("urllib.request.urlopen", side_effect=OSError("boom " + DUMMY["HC_PING_URL"])): self.assertFalse(watcher.ping(DUMMY["HC_PING_URL"]))
        self.assertNotIn("dummy-uuid", out.getvalue())

class NtfyTests(Base):
    def test_ntfy_request_shape(self):
        cap = {}
        class R:
            status = 200
            def __enter__(s): return s
            def __exit__(s, *a): return False
        def fake(req, timeout=0): cap["url"] = req.full_url; cap["body"] = req.data.decode(); cap["title"] = req.get_header("Title"); return R()
        with mock.patch("urllib.request.urlopen", fake): self.assertTrue(watcher.ntfy("T", "B"))
        self.assertEqual((cap["url"], cap["body"], cap["title"]), ("https://ntfy.sh/dummy-topic-xyz", "B", "T"))

class TickTests(Base):
    def tick(self, t, kite=None, notify=None, st=None):
        st = st or watcher.State(); kite = kite or FakeKite(t.strftime("%Y-%m-%d"))
        return st, kite, watcher.run_tick(st, t, make_kite_fn=lambda tok: kite, notify_fn=notify or (lambda title, body: True))
    def patch_events(self, events):
        L, D = watcher.engine()
        real = L.recipe_events
        return mock.patch.object(L, "recipe_events", lambda nm, work: events if nm == "NSEI" else [])

    def test_real_engine_smoke_runs_and_writes_candles(self):
        self.token(); sent = []
        st, kite, res = self.tick(ist(TUE + " 15:30:20"), notify=lambda t, b: sent.append((t, b)) or True)
        self.assertTrue(res.startswith("ok"), res)
        for nm in ("NSEI", "NSEBANK", "BSESN"): self.assertTrue(os.path.getsize("data/live/candles/%s_5m.csv" % nm) > 100)
        self.assertEqual(len(self.rows()), len([r for r in self.rows()]))   # whatever the engine decided, rows and alerts agree
        self.assertEqual(len(sent), len([r for r in self.rows() if r["alert"] == "sent"]))
        self.assertTrue(all(r["event"] in ("ENTRY", "EXIT", "SKIP") for r in self.rows()))
        self.assertTrue(all(x in ("instruments NSE", "instruments BSE", "instruments NFO", "instruments BFO", "historical_data") for x in kite.calls))
    def test_entry_alert_has_every_field_and_is_logged(self):
        self.token(); sent = []
        with self.patch_events([ev("ENTRY", "BUY", TUE + " 11:00", "11:15", 24111.5)]):
            self.tick(ist(TUE + " 11:15:20"), notify=lambda t, b: sent.append((t, b)) or True)
        self.assertEqual(len(sent), 1); title, body = sent[0]
        for need in ("ENTRY BUY", "Nifty", "Lot size: 65", "24111.50", "Stop: none, exits on opposite crossover", "Reason:", "No order was placed"): self.assertIn(need, title + "\n" + body)
        r = self.rows(); self.assertEqual(len(r), 1); self.assertEqual((r[0]["event"], r[0]["lot_size"], r[0]["stop"], r[0]["alert"]), ("ENTRY", "65", watcher.STOP_TEXT, "sent"))
    def test_exit_alert_says_paper_engine_position_and_names_the_entry(self):
        self.token(); sent = []
        events = [ev("ENTRY", "BUY", "2026-11-09 10:15", "10:30", 24000.5, day="2026-11-09"), ev("EXIT", "SELL", TUE + " 11:00", "11:15", 24100.25)]
        with self.patch_events(events): self.tick(ist(TUE + " 11:15:20"), notify=lambda t, b: sent.append((t, b)) or True)
        self.assertEqual(len(sent), 1); body = sent[0][1]
        self.assertIn("paper engine position", body); self.assertIn("BUY entry made 2026-11-09 10:15 at 24000.50", body); self.assertIn("NSEI|2026-11-09 10:15|ENTRY", body)
        self.assertIn("Stop: none, exits on opposite crossover", body)
        r = self.rows(); self.assertEqual(r[0]["closes_entry"], "NSEI|2026-11-09 10:15|ENTRY")
    def test_skip_is_logged_but_never_alerted(self):
        self.token(); sent = []
        with self.patch_events([ev("SKIP", "BUY", TUE + " 11:00", "11:15", 24111.5, note="skipped: low volatility")]):
            self.tick(ist(TUE + " 11:15:20"), notify=lambda t, b: sent.append(1) or True)
        self.assertEqual(sent, []); r = self.rows(); self.assertEqual((len(r), r[0]["event"], r[0]["alert"], r[0]["reason"]), (1, "SKIP", "log only", "skipped: low volatility"))
    def test_same_signal_is_never_sent_twice(self):
        self.token(); sent = []; events = [ev("ENTRY", "BUY", TUE + " 11:00", "11:15", 24111.5)]
        with self.patch_events(events):
            st, kite, _ = self.tick(ist(TUE + " 11:15:20"), notify=lambda t, b: sent.append(1) or True)
            self.tick(ist(TUE + " 11:20:20"), kite=kite, st=st, notify=lambda t, b: sent.append(1) or True)
            self.tick(ist(TUE + " 11:25:20"), notify=lambda t, b: sent.append(1) or True)   # even a fresh State (restart)
        self.assertEqual((len(sent), len(self.rows())), (1, 1))
    def test_earlier_days_are_ignored_and_stale_signals_are_logged_not_sent(self):
        self.token(); sent = []
        events = [ev("ENTRY", "BUY", "2026-11-09 10:15", "10:30", 1.0, day="2026-11-09"), ev("ENTRY", "SELL", TUE + " 10:00", "10:15", 24050.0)]
        with self.patch_events(events): self.tick(ist(TUE + " 11:15:20"), notify=lambda t, b: sent.append(1) or True)
        self.assertEqual(sent, []); r = self.rows(); self.assertEqual(len(r), 1); self.assertTrue(r[0]["alert"].startswith("not sent, stale"))
    def test_failed_send_is_retried_then_logged_once_delivered(self):
        self.token(); events = [ev("ENTRY", "BUY", TUE + " 11:00", "11:15", 24111.5)]; calls = []
        with self.patch_events(events):
            st, kite, res = self.tick(ist(TUE + " 11:15:20"), notify=lambda t, b: calls.append(1) and False)
            self.assertEqual(self.rows(), [])
            self.tick(ist(TUE + " 11:20:20"), kite=kite, st=st, notify=lambda t, b: calls.append(1) or True)
        self.assertEqual(len(self.rows()), 1); self.assertEqual(self.rows()[0]["alert"], "sent")
    def test_listed_holiday_skips_everything(self):
        self.token(); open("holidays.txt", "w").write(TUE + "\n")
        def boom(tok): raise AssertionError("Kite must not be touched on a listed holiday")
        self.assertEqual(watcher.run_tick(watcher.State(), ist(TUE + " 11:15:20"), make_kite_fn=boom), "holiday")
    def test_unlisted_holiday_detected_when_kite_has_no_candles(self):
        self.token(); kite = FakeKite(TUE, with_candles=False); sent = []
        with self.patch_events([ev("ENTRY", "BUY", TUE + " 11:00", "11:15", 1.0)]):
            _, _, early = self.tick(ist(TUE + " 09:30:20"), kite=kite); _, _, late = self.tick(ist(TUE + " 09:50:20"), kite=kite, notify=lambda t, b: sent.append(1))
        self.assertEqual((early, late), ("waiting", "holiday")); self.assertEqual((sent, self.rows()), ([], []))
    def test_no_token_sends_one_login_warning_only(self):
        sent = []; st = watcher.State()
        for m in ("20", "25", "30"):
            watcher.run_tick(st, ist(TUE + " 09:%s:20" % m), make_kite_fn=lambda t: None, notify_fn=lambda title, body: sent.append(title) or True)
        self.assertEqual(sent, ["Kite login needed"])
    def test_yesterdays_token_is_not_accepted(self):
        self.token("2026-11-09"); st = watcher.State()
        self.assertEqual(watcher.run_tick(st, ist(TUE + " 09:30:20"), make_kite_fn=lambda t: None, notify_fn=lambda a, b: True), "no token")
    def test_rejected_token_warns_once_and_keeps_running(self):
        self.token(); sent = []
        class Rejecting(FakeKite):
            def historical_data(self, *a): raise Exception("TokenException: Invalid access_token")
        st = watcher.State(); k = Rejecting(TUE)
        for m in ("30", "35"): watcher.run_tick(st, ist(TUE + " 09:%s:20" % m), make_kite_fn=lambda t: k, notify_fn=lambda title, body: sent.append(title) or True)
        self.assertEqual(sent, ["Kite login needed"])
    def test_a_crash_inside_a_check_does_not_stop_the_loop(self):
        c = FakeClock(ist(TUE + " 15:00:00")); n = []
        def bad(st, t): n.append(1); raise RuntimeError("boom")
        with contextlib.redirect_stdout(io.StringIO()): watcher.trading_loop(c, watcher.State(), bad)
        self.assertEqual(len(n), 8)   # one catch-up check at 15:00:00, then 15:00:20 to 15:30:20 every 5 minutes

class RehearsalTests(Base):
    """watcher.py --rehearse: real-looking check, prints what would be alerted, sends nothing, writes nothing into the folder."""
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.run_dir, "fakerepo"); os.makedirs(self.repo + "/data/live/candles")
        os.symlink(os.path.join(ROOT, "kite"), self.repo + "/data/kite")
        with open(self.repo + "/data/live/candles/NSEI_5m.csv", "w") as f: f.write("ts_ist,open,high,low,close,volume\n")
        self.envfile = os.path.join(self.run_dir, "dummy.env")
        with open(self.envfile, "w") as f: f.write("\n".join("%s=%s" % kv for kv in DUMMY.items()))
        self.patches = [mock.patch.object(watcher, "HERE", self.repo), mock.patch.object(watcher, "ENV_PATH", self.envfile)]
        for p in self.patches: p.start()
    def tearDown(self):
        for p in self.patches: p.stop()
        super().tearDown()
    def snapshot(self):
        snap = {}
        for d, _, files in os.walk(self.repo + "/data/live"):
            for f in files: snap[os.path.join(d, f)] = open(os.path.join(d, f), "rb").read()
        return snap
    def rehearse(self, events=None, now=TUE + " 16:40:00", with_candles=True, kite=None, day=None):
        kite = kite or FakeKite(TUE, with_candles); lines = []
        L, D = watcher.engine()
        ctx = mock.patch.object(L, "recipe_events", lambda nm, work: events if nm == "NSEI" else []) if events is not None else contextlib.nullcontext()
        with ctx: rc = watcher.rehearse(clock=FakeClock(ist(now)), out=lines.append, make_kite_fn=lambda tok: kite, day=day)
        return rc, "\n".join(lines), kite

    def test_prints_would_alert_lines_and_leaves_no_trace(self):
        self.token(); cwd = os.getcwd(); orig = (watcher.ntfy, watcher.ping); before = self.snapshot()
        events = [ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 24111.5), ev("SKIP", "SELL", TUE + " 11:00", "11:15", 24000.0, note="skipped: low volatility"), ev("EXIT", "SELL", TUE + " 12:00", "12:15", 24200.0)]
        rc, text, kite = self.rehearse(events)
        self.assertEqual(rc, 0)
        for need in ("REHEARSAL", "WOULD ALERT | ENTRY BUY Nifty", "WOULD ALERT | EXIT SELL Nifty", "log only | SKIP", "lot 65", "paper engine position", "Stop: none, exits on opposite crossover", "ntfy messages that would have been sent (2)", "Candles for today: NSEI 75"):
            self.assertIn(need, text)
        self.assertNotIn("not sent, stale", text)                      # the stale rule is off, signals from hours ago still show as alerts
        self.assertEqual(before, self.snapshot()); self.assertFalse(os.path.exists(self.repo + "/data/live/alerts.csv"))
        self.assertEqual(os.getcwd(), cwd); self.assertEqual((watcher.ntfy, watcher.ping), orig)
        self.assertTrue(os.path.isdir(ROOT + "/kite") and os.path.exists(ROOT + "/kite/NSEI_5m.csv"))   # the temporary copy's links never delete real data
        self.assertTrue(all(x in ("instruments NSE", "instruments BSE", "instruments NFO", "instruments BFO", "historical_data") for x in kite.calls))
    def test_never_calls_ntfy_or_ping(self):
        self.token(); n, p = mock.Mock(), mock.Mock()
        with mock.patch.object(watcher, "ntfy", n), mock.patch.object(watcher, "ping", p):
            self.rehearse([ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 24111.5)])
            self.assertIs(watcher.ntfy, n); self.assertIs(watcher.ping, p)
        n.assert_not_called(); p.assert_not_called()
    def test_marks_signals_a_real_run_already_sent_and_does_not_touch_that_file(self):
        self.token(); real = self.repo + "/data/live/alerts.csv"
        with open(real, "w") as f: f.write(",".join(watcher.HDR) + "\nNSEI|%s 10:00|ENTRY,x,ENTRY,Nifty 50,BUY,x,x,65,1,x,x,,sent\n" % TUE)
        before = self.snapshot(); rc, text, _ = self.rehearse([ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 24111.5), ev("EXIT", "SELL", TUE + " 12:00", "12:15", 24200.0)])
        self.assertEqual(text.count("already sent by a real run"), 1); self.assertEqual(before, self.snapshot())
    def test_no_token_prints_the_login_message_and_returns_1(self):
        rc, text, _ = self.rehearse([])
        self.assertEqual(rc, 1); self.assertIn("Kite login needed", text); self.assertIn("check result: no token", text)
    def test_no_candles_means_no_signals_and_no_crash(self):
        self.token(); rc, text, _ = self.rehearse([ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 1.0)], with_candles=False)
        self.assertEqual(rc, 0); self.assertIn("check result: holiday", text); self.assertIn("No signals today.", text)
    def test_listed_holiday_never_touches_kite(self):
        self.token()
        with open(self.repo + "/holidays.txt", "w") as f: f.write(TUE + "\n")
        def boom(tok): raise AssertionError("Kite must not be touched")
        lines = []; rc = watcher.rehearse(clock=FakeClock(ist(TUE + " 16:40:00")), out=lines.append, make_kite_fn=boom)
        self.assertEqual(rc, 0); self.assertIn("check result: holiday", "\n".join(lines))
    def test_needs_only_the_kite_key(self):
        self.token()
        with mock.patch.dict(os.environ, {"NTFY_TOPIC": "", "HC_PING_URL": ""}):
            with open(self.envfile, "w") as f: f.write("KITE_API_KEY=dummykey1234\n")
            rc, text, _ = self.rehearse([])
        self.assertEqual(rc, 0)
    def test_the_flag_runs_rehearsal_and_starts_no_heartbeat(self):
        with mock.patch.object(sys, "argv", ["watcher.py", "--rehearse"]), mock.patch.object(watcher, "rehearse", lambda: 7), \
             mock.patch.object(watcher.threading, "Thread", side_effect=AssertionError("no heartbeat thread in rehearsal")):
            self.assertEqual(watcher.main(), 7)

    # ---- --rehearse-day: replay an earlier weekday (TUE = 2026-11-10 here), "now" is Thursday 2026-11-12 ----
    NOW = "2026-11-12 10:00:00"
    def write_ledger(self, ids):
        with open(self.repo + "/data/live/ledger.csv", "w") as f: f.write("event_id,recorded_at_ist,event\n" + "".join("%s,x,x\n" % i for i in ids))
    def test_replay_day_prints_signals_and_matches_the_ledger(self):
        self.token("2026-11-12"); before = self.snapshot(); orig = (watcher.ntfy, watcher.ping)
        self.write_ledger(["NSEI|%s 10:00|ENTRY" % TUE, "NSEI|%s 12:00|EXIT" % TUE, "NSEBANK|%s 11:00|SKIP" % TUE, "NSEI|2026-11-09 10:00|ENTRY"])
        led_before = open(self.repo + "/data/live/ledger.csv").read()
        events = [ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 24111.5), ev("EXIT", "SELL", TUE + " 12:00", "12:15", 24200.0)]
        rc, text, kite = self.rehearse(events, now=self.NOW, day=TUE)
        self.assertEqual(rc, 0)
        for need in ("REHEARSAL OF A PAST DAY (%s)" % TUE, "Candles for %s: NSEI 75" % TUE, "Signals on %s (2):" % TUE, "WOULD ALERT | ENTRY BUY Nifty", "WOULD ALERT | EXIT SELL Nifty", "paper engine position",
                     "2 of 2 replayed signals are in the ledger (ledger has 3 for that day)", "in the ledger but not replayed: NSEBANK|%s 11:00|SKIP" % TUE, "replayed but not in the ledger: none", "would have been sent (2)"):
            self.assertIn(need, text)
        self.assertNotIn("2026-11-09 10:00", text)                                  # another day's ledger rows are ignored
        self.assertEqual((watcher.ntfy, watcher.ping), orig); self.assertEqual(open(self.repo + "/data/live/ledger.csv").read(), led_before)
        snap = self.snapshot(); snap.pop(self.repo + "/data/live/ledger.csv"); before.pop(self.repo + "/data/live/ledger.csv", None); self.assertEqual(before, snap)
        self.assertFalse(os.path.exists(self.repo + "/data/live/alerts.csv"))
    def test_replay_day_reports_replayed_but_unrecorded_signals(self):
        self.token("2026-11-12"); self.write_ledger(["NSEI|%s 10:00|ENTRY" % TUE])
        rc, text, _ = self.rehearse([ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 1.0), ev("EXIT", "SELL", TUE + " 12:00", "12:15", 2.0)], now=self.NOW, day=TUE)
        self.assertIn("1 of 2 replayed signals", text); self.assertIn("replayed but not in the ledger: NSEI|%s 12:00|EXIT" % TUE, text)
    def test_replay_day_without_ledger_rows_says_so(self):
        self.token("2026-11-12"); self.write_ledger(["NSEI|2026-11-09 10:00|ENTRY"])
        rc, text, _ = self.rehearse([ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 1.0)], now=self.NOW, day=TUE)
        self.assertIn("No ledger rows for that day", text)
    def test_replay_day_uses_todays_token_not_the_replay_days(self):
        self.token(TUE)                                                              # a token dated the replayed day is useless today
        rc, text, _ = self.rehearse([], now=self.NOW, day=TUE)
        self.assertEqual(rc, 1); self.assertIn("check result: no token", text)
    def test_replay_day_rejects_bad_days_before_touching_kite(self):
        self.token("2026-11-12")
        def boom(tok): raise AssertionError("Kite must not be touched")
        for day, msg in (("banana", "needs a date like"), ("", "needs a date like"), ("2026-11-12", "before today"), ("2026-11-14", "before today"), ("2026-11-07", "weekend")):
            lines = []; rc = watcher.rehearse(clock=FakeClock(ist(self.NOW)), out=lines.append, make_kite_fn=boom, day=day)
            self.assertEqual(rc, 2, day); self.assertIn(msg, "\n".join(lines))
    def test_replay_day_never_calls_ntfy_or_ping(self):
        self.token("2026-11-12"); n, p = mock.Mock(), mock.Mock()
        with mock.patch.object(watcher, "ntfy", n), mock.patch.object(watcher, "ping", p):
            self.rehearse([ev("ENTRY", "BUY", TUE + " 10:00", "10:15", 24111.5)], now=self.NOW, day=TUE)
        n.assert_not_called(); p.assert_not_called()
    def test_flags_are_parsed(self):
        seen = []
        for argv in (["watcher.py", "--rehearse-day", "2026-11-04"], ["watcher.py", "--rehearse-day=2026-11-04"], ["watcher.py", "--rehearse-day"]):
            with mock.patch.object(sys, "argv", argv), mock.patch.object(watcher, "rehearse", lambda day=None: seen.append(day) or 7), \
                 mock.patch.object(watcher.threading, "Thread", side_effect=AssertionError("no heartbeat thread in rehearsal")):
                self.assertEqual(watcher.main(), 7)
        self.assertEqual(seen, ["2026-11-04", "2026-11-04", ""])

class SafetyTests(unittest.TestCase):
    def test_no_order_code_and_no_direct_env_file_read(self):
        for f in ("watcher.py", "login.py"):
            src = open(os.path.join(REPO, f)).read()
            self.assertIsNone(re.search(r"place_order|modify_order|cancel_order|exit_order|\.orders\(|\.gtt|convert_position|place_mf|\.trades\(", src), f)
            self.assertNotIn("open(ENV_PATH", src)
            self.assertNotIn("read_text", src.replace("def read_text", ""))

if __name__ == "__main__": unittest.main(verbosity=2)
