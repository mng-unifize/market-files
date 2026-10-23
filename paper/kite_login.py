"""Exchanges the Kite login code for the day's access token. Writes it to $RUNNER_TEMP/kite_token.txt (never prints it)."""
import os, sys
from kiteconnect import KiteConnect

def token_from(raw):
    raw = raw.strip().strip('"').strip("'")
    if "request_token=" in raw: raw = raw.split("request_token=", 1)[1]
    return raw.split("&", 1)[0].strip()

def main():
    kite = KiteConnect(api_key=os.environ["KITE_API_KEY"].strip())
    try:
        s = kite.generate_session(token_from(os.environ["KITE_REQUEST_TOKEN"]), api_secret=os.environ["KITE_API_SECRET"].strip())
    except Exception as e:
        print("Login failed:", str(e)[:200], "\nThe code is single use and lasts a few minutes. Log in again and paste a fresh address.")
        sys.exit(1)
    kite.set_access_token(s["access_token"])
    try: p = kite.profile(); print("Kite login ok for user id ending", str(p.get("user_id", ""))[-2:])
    except Exception as e: print("Token created but profile check failed:", str(e)[:150]); sys.exit(1)
    open(os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "kite_token.txt"), "w").write(s["access_token"])

main()
