#!/usr/bin/env python3
"""Daily Kite login for watcher.py. Run once each trading morning, before 08:30 India time (GitHub's first paper run starts then).

1. Prints the Kite login address. Open it, log in, and wait for the redirect to https://127.0.0.1/ (the browser shows an error page, that is fine).
2. Copy the whole address from the browser bar (or just the request_token) and paste it here.
3. The access token is saved to /Users/ng/ibt/kite_token.json (outside this folder, readable only by you).
4. The same token is then copied to the GitHub secret KITE_ACCESS_TOKEN with the gh command line tool, so this Mac and GitHub's paper
   recorder share one login. The token goes through standard input only. Use --no-github to skip this step.

Secrets come from /Users/ng/ibt/.env through python-dotenv. Nothing secret is printed."""
import os, sys, json, shutil, subprocess, datetime as dt
from dotenv import load_dotenv

def use_certifi():
    """Point Python's HTTPS (urllib) and requests (used by kiteconnect) at certifi's certificate bundle, unless one is already set.
    Some macOS Python builds have no usable system certificates (CERTIFICATE_VERIFY_FAILED), and launchd starts jobs with a bare environment."""
    try: import certifi
    except ImportError: return False
    path = certifi.where()
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        if not os.environ.get(k): os.environ[k] = path
    return True
use_certifi()
from kiteconnect import KiteConnect
from fetch_kite_history import token_from   # existing helper: accepts the bare token or the whole redirected address

ENV_PATH = "/Users/ng/ibt/.env"
TOKEN_PATH = "/Users/ng/ibt/kite_token.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
GITHUB_REPO = "mng-unifize/market-files"
SECRET_NAME = "KITE_ACCESS_TOKEN"      # the name GitHub's paper-live and preflight workflows read
MANUAL_COPY = ("python3 -c 'import json,sys;sys.stdout.write(json.load(open(\"%s\"))[\"access_token\"])' | gh secret set %s --repo %s"
               % ("/Users/ng/ibt/kite_token.json", SECRET_NAME, GITHUB_REPO))

def save_token(token, user_id):
    now = dt.datetime.now(IST)
    data = {"access_token": token, "date": now.strftime("%Y-%m-%d"), "created_at_ist": now.strftime("%Y-%m-%d %H:%M"), "user_id_tail": str(user_id)[-2:]}
    tmp = TOKEN_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(data, f)
    os.chmod(tmp, 0o600); os.replace(tmp, TOKEN_PATH)

def push_to_github(token, repo=GITHUB_REPO):
    """Copies the token into the GitHub secret with the gh command line tool. The token travels through standard input only, never in a
    command line and never printed. Returns (True, "") or (False, reason)."""
    gh = shutil.which("gh")
    if not gh: return False, "gh (the GitHub command line tool) was not found"
    try:
        r = subprocess.run([gh, "secret", "set", SECRET_NAME, "--repo", repo], input=token, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, type(e).__name__
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "gh failed").replace(token, "***").strip()[:200]
    return True, ""

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not os.path.isfile(ENV_PATH): print("The secrets file is missing at", ENV_PATH); return 2
    load_dotenv(ENV_PATH)
    missing = [k for k in ("KITE_API_KEY", "KITE_API_SECRET") if not os.environ.get(k, "").strip()]
    if missing: print("Missing in the secrets file:", ", ".join(missing)); return 2
    kite = KiteConnect(api_key=os.environ["KITE_API_KEY"].strip())
    print("Open this address, log in, then copy the full address from the browser bar after the redirect:\n")
    print(kite.login_url(), "\n")
    request_token = token_from(input("Paste the address or the request_token here: "))
    if not request_token: print("No request_token found in what you pasted."); return 1
    try:
        session = kite.generate_session(request_token, api_secret=os.environ["KITE_API_SECRET"].strip())
        kite.set_access_token(session["access_token"])
        profile = kite.profile()
    except Exception as e:
        print("Login failed:", type(e).__name__, str(e)[:150].replace(os.environ["KITE_API_SECRET"].strip(), "***"))
        print("The code works once and lasts a few minutes. Log in again and paste a fresh address.")
        return 1
    token = session["access_token"]
    save_token(token, profile.get("user_id", ""))
    print("Kite login ok for user id ending", str(profile.get("user_id", ""))[-2:], "| token saved to", TOKEN_PATH)
    if "--no-github" in argv: print("GitHub step skipped (--no-github)."); return 0
    ok, why = push_to_github(token)
    if ok: print("GitHub secret %s updated in %s. This Mac and GitHub now share today's login." % (SECRET_NAME, GITHUB_REPO)); return 0
    print("WARNING: the token is saved on this Mac, but GitHub was NOT updated (%s)." % why)
    print("GitHub's paper recorder keeps an old token until you run this:\n  " + MANUAL_COPY)
    return 3

if __name__ == "__main__": sys.exit(main())
