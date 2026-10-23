#!/usr/bin/env python3
# One-time backfill of 5-minute and 60-minute index candles from FYERS into data/fyers/*.csv.
# Needs: pip install fyers-apiv3. Environment: FYERS_APP_ID, FYERS_SECRET, FYERS_REDIRECT, FYERS_AUTH_CODE (all from the workflow).
import csv, os, sys, time, datetime as dt
from fyers_apiv3 import fyersModel
IST = dt.timezone(dt.timedelta(hours=5, minutes=30)); OUT = "data/fyers"
SYMS = {"NSEI": "NSE:NIFTY50-INDEX", "BSESN": "BSE:SENSEX-INDEX", "NSEBANK": "NSE:NIFTYBANK-INDEX"}
RES = {"5": "5m", "60": "60m"}
START = dt.date(int(os.environ.get("START_YEAR", "2025")), int(os.environ.get("START_MONTH", "8")), 1)   # about 14 months of history
def token():
    s = fyersModel.SessionModel(client_id=os.environ["FYERS_APP_ID"], secret_key=os.environ["FYERS_SECRET"],
                                redirect_uri=os.environ["FYERS_REDIRECT"], response_type="code", grant_type="authorization_code")
    s.set_token(os.environ["FYERS_AUTH_CODE"].strip()); r = s.generate_token()
    if "access_token" not in r: raise SystemExit("login failed: " + str(r))
    return r["access_token"]
def main():
    fy = fyersModel.FyersModel(client_id=os.environ["FYERS_APP_ID"], is_async=False, token=token(), log_path="")
    os.makedirs(OUT, exist_ok=True); today = dt.date.today()
    for name, sym in SYMS.items():
        for res, tag in RES.items():
            rows = {}; a = START
            while a <= today:
                b = min(a + dt.timedelta(days=95), today)                     # limit is 100 days per request
                r = fy.history(data={"symbol": sym, "resolution": res, "date_format": "1", "range_from": a.isoformat(), "range_to": b.isoformat(), "cont_flag": "1"})
                if r.get("s") != "ok": print("WARN", name, tag, a, b, r, file=sys.stderr)
                for c in r.get("candles", []):
                    ts = dt.datetime.fromtimestamp(c[0], IST).strftime("%Y-%m-%d %H:%M")
                    rows[ts] = [ts, c[1], c[2], c[3], c[4], int(c[5] or 0)]
                a = b + dt.timedelta(days=1); time.sleep(0.5)
            with open(f"{OUT}/{name}_{tag}.csv", "w", newline="") as f:
                w = csv.writer(f); w.writerow(["ts_ist", "open", "high", "low", "close", "volume"])
                for k in sorted(rows): w.writerow(rows[k])
            print(name, tag, len(rows), "rows", min(rows) if rows else "-", max(rows) if rows else "-")
main()
