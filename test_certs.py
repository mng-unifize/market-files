"""Tests that watcher.py and login.py use certifi's certificate bundle by default (also under launchd's bare environment).
No network, no real secrets. Run:  ~/ibt-env/bin/python -m unittest -v test_certs"""
import os, sys, ssl, types, shutil, tempfile, subprocess, unittest
from unittest import mock

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
try: import kiteconnect                      # the real one on your Mac
except ImportError: sys.modules["kiteconnect"] = types.SimpleNamespace(KiteConnect=object)   # only so login.py can be imported on a box without it
try: import certifi
except ImportError: certifi = None
import watcher, login
VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")

def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in VARS}
    env.update(extra or {}); return env

class UseCertifi(unittest.TestCase):
    @unittest.skipUnless(certifi, "certifi not installed here")
    def test_sets_both_variables_when_unset(self):
        for mod in (watcher, login):
            with mock.patch.dict(os.environ, clean_env(), clear=True):
                self.assertTrue(mod.use_certifi())
                self.assertEqual((os.environ["SSL_CERT_FILE"], os.environ["REQUESTS_CA_BUNDLE"]), (certifi.where(), certifi.where()))
    @unittest.skipUnless(certifi, "certifi not installed here")
    def test_empty_values_are_replaced(self):
        for mod in (watcher, login):
            with mock.patch.dict(os.environ, clean_env({"SSL_CERT_FILE": "", "REQUESTS_CA_BUNDLE": ""}), clear=True):
                mod.use_certifi(); self.assertEqual(os.environ["SSL_CERT_FILE"], certifi.where())
    def test_a_value_you_set_yourself_is_respected(self):
        for mod in (watcher, login):
            with mock.patch.dict(os.environ, clean_env({"SSL_CERT_FILE": "/my/own.pem"}), clear=True):
                mod.use_certifi(); self.assertEqual(os.environ["SSL_CERT_FILE"], "/my/own.pem")
    def test_missing_certifi_falls_back_without_crashing(self):
        for mod in (watcher, login):
            with mock.patch.dict(sys.modules, {"certifi": None}), mock.patch.dict(os.environ, clean_env(), clear=True):
                self.assertFalse(mod.use_certifi()); self.assertNotIn("SSL_CERT_FILE", os.environ)

@unittest.skipUnless(certifi, "certifi not installed here")
class LaunchdLikeStart(unittest.TestCase):
    """A fresh process with a bare environment (what launchd gives) and nothing set by hand."""
    def run_py(self, code, extra_path=None):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")}
        if extra_path: env["PYTHONPATH"] = extra_path
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-500:]); return r.stdout.strip().splitlines()
    def test_watcher_sets_certifi_bundle_at_import(self):
        out = self.run_py("import os, watcher\nprint(os.environ.get('SSL_CERT_FILE'))\nprint(os.environ.get('REQUESTS_CA_BUNDLE'))")
        self.assertEqual(out, [certifi.where(), certifi.where()])
    def test_login_sets_certifi_bundle_at_import(self):
        stub = tempfile.mkdtemp()
        try:
            has_kite = subprocess.run([sys.executable, "-c", "import kiteconnect"], capture_output=True).returncode == 0
            if not has_kite:
                with open(os.path.join(stub, "kiteconnect.py"), "w") as f: f.write("class KiteConnect: pass\n")
            out = self.run_py("import os, login\nprint(os.environ.get('SSL_CERT_FILE'))\nprint(os.environ.get('REQUESTS_CA_BUNDLE'))", None if has_kite else stub)
            self.assertEqual(out, [certifi.where(), certifi.where()])
        finally: shutil.rmtree(stub)
    def test_python_default_https_context_trusts_the_bundle(self):
        # urllib builds its context with ssl.create_default_context(); after importing watcher it must hold certifi's certificates
        out = self.run_py("import watcher, ssl\nprint(ssl.create_default_context().cert_store_stats()['x509_ca'])")
        self.assertGreater(int(out[0]), 50)

if __name__ == "__main__": unittest.main(verbosity=2)
