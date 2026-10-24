#!/usr/bin/env python3
"""Morning preflight: run after the Kite login, before 09:15 India time. Needs: pip install kiteconnect.
Checks the token, the three indexes, the three futures contracts (symbol, lot size, expiry), candle flow and a futures quote.
Writes data/live/preflight.json. Exit code 1 when anything fails, so GitHub emails you before the market opens."""
import sys, os, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live as L
import mock_detail as D

def main():
    now = L.now_ist(); today = now.strftime("%Y-%m-%d"); res = []
    def add(name, ok, detail): res.append(dict(check=name, result="PASS" if ok else "FAIL", detail=detail)); print(("PASS " if ok else "FAIL ") + name + ": " + detail)
    try:
        kite = L.get_client(); prof = kite.profile(); add("token and login", True, "Kite accepted the token")
    except Exception as e:
        add("token and login", False, str(e)[:150]); return finish(res, today)
    try: tokens, futs = L.find_tokens(kite, today)
    except Exception as e: add("instrument lists", False, str(e)[:150]); return finish(res, today)
    for nm in L.IDX:
        add(f"index found: {D.NAMES[nm]}", nm in tokens, f"token {tokens.get(nm)}" if nm in tokens else "not in Kite instrument list")
        sym, ftok = L.pick_future(nm, today, futs)
        want = D.contract(nm, today); info = futs.get(nm, {}).get(sym)
        add(f"future found: {D.NAMES[nm]}", ftok is not None and sym == want, f"{sym}" + ("" if sym == want else f" (engine expects {want}; the live job falls back to the nearest expiry, so check the contract before relying on it)"))
        if info:
            lot = info[2]; add(f"lot size: {D.NAMES[nm]}", lot == L.LOTS[nm], f"Kite says {lot}, our engine uses {L.LOTS[nm]}")
            days_left = (dt.date.fromisoformat(info[1]) - dt.date.fromisoformat(today)).days
            add(f"expiry: {D.NAMES[nm]}", days_left >= 0, f"{sym} expires {info[1]} ({days_left} days)")
        for label, tok in ((f"index candles: {D.NAMES[nm]}", tokens.get(nm)), (f"future candles: {D.NAMES[nm]}", ftok)):
            if tok is None: continue
            try:
                rows = L.candles(kite, tok, now, days=5); days = sorted({k[:10] for k in rows})
                add(label, bool(rows), f"{len(rows)} candles over {len(days)} days, newest {max(rows) if rows else 'none'}")
            except Exception as e: add(label, False, str(e)[:120])
        if ftok is not None:
            try:
                q = kite.quote([f"{L.FUT_EX[nm]}:{sym}"])[f"{L.FUT_EX[nm]}:{sym}"]; d = q.get("depth", {})
                add(f"quote: {D.NAMES[nm]}", bool(d.get("buy")) and bool(d.get("sell")), "best bid and ask available" if d.get("buy") else "no depth (normal before the open if the market is closed)")
            except Exception as e: add(f"quote: {D.NAMES[nm]}", False, str(e)[:120])
    return finish(res, today)

def finish(res, today):
    bad = [r for r in res if r["result"] == "FAIL"]
    os.makedirs(L.LIVE, exist_ok=True)
    json.dump(dict(run_at=L.now_ist().strftime("%Y-%m-%d %H:%M"), status="fail" if bad else "pass", checks=res), open(f"{L.LIVE}/preflight.json", "w"), indent=1)
    print("PREFLIGHT", "FAIL" if bad else "PASS", f"({len(res) - len(bad)} of {len(res)} checks passed)")
    return 1 if bad else 0

sys.exit(main()) if __name__ == "__main__" else None
