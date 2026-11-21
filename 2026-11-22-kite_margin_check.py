"""Asks Kite what ONE lot of Bank Nifty (and Nifty, Sensex for reference) needs as margin, then prints a plan for cash and pledged margin.
Read only: logs in and asks Kite for a margin estimate (order_margins). Places NO orders. Writes only data/live/margin_per_lot.json (margins per lot, no balances).
Your balances are printed to the workflow log only.
Env: KITE_API_KEY, KITE_API_SECRET, KITE_REQUEST_TOKEN. Optional: DAILY_STOP (default 40000), BUFFER_PCT (default 25), CASH_SHARE (default 50)."""
import os, sys, json, datetime as dt
CODES = {"NSEBANK": ("NFO", "BANKNIFTY", 30), "NSEI": ("NFO", "NIFTY", 65), "BSESN": ("BFO", "SENSEX", 20)}
DAILY_STOP = float(os.environ.get("DAILY_STOP", "40000")); BUFFER = float(os.environ.get("BUFFER_PCT", "25")) / 100; CASH_SHARE = float(os.environ.get("CASH_SHARE", "50")) / 100
def token_from(raw):
    raw = raw.strip().strip('"').strip("'")
    if "request_token=" in raw: raw = raw.split("request_token=", 1)[1]
    return raw.split("&", 1)[0].strip()
def pick(instruments, name, today, min_days=7):
    """Nearest futures contract that still has at least min_days to run (the one the system trades after a roll)."""
    c = [i for i in instruments if i.get("instrument_type") == "FUT" and i.get("name") == name and i["expiry"] and (i["expiry"] - today).days >= min_days]
    return min(c, key=lambda i: i["expiry"]) if c else None
def plan(margin, daily_stop=DAILY_STOP, buffer=BUFFER, cash_share=CASH_SHARE):
    """margin = Kite's total for one lot. Returns the numbers to keep in the account."""
    need = margin * (1 + buffer)
    cash_for_margin = need * cash_share
    return {"kite_margin_one_lot": round(margin), "with_buffer": round(need), "cash_at_least": round(cash_for_margin + daily_stop + 5000),
            "pledge_at_most_counted": round(need - cash_for_margin), "free_margin_to_show": round(need + daily_stop)}
def main():
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=os.environ["KITE_API_KEY"].strip())
    s = kite.generate_session(token_from(os.environ["KITE_REQUEST_TOKEN"]), api_secret=os.environ["KITE_API_SECRET"].strip()); kite.set_access_token(s["access_token"]); print("login ok")
    today = dt.date.today(); cache = {}; out = {"as_of": today.isoformat(), "lots": {}}
    for code, (ex, name, qty) in CODES.items():
        if ex not in cache:
            try: cache[ex] = kite.instruments(ex)
            except Exception as e: print("could not load", ex, str(e)[:100]); cache[ex] = []
        ins = pick(cache[ex], name, today)
        if not ins: print("NOT FOUND", name); continue
        res = {}
        for side in ("BUY", "SELL"):
            r = kite.order_margins([{"exchange": ex, "tradingsymbol": ins["tradingsymbol"], "transaction_type": side, "variety": "regular", "product": "NRML", "order_type": "MARKET", "quantity": ins["lot_size"], "price": 0}])[0]
            res[side] = r
        tot = max(res["BUY"]["total"], res["SELL"]["total"])
        out["lots"][code] = {"contract": ins["tradingsymbol"], "expiry": str(ins["expiry"]), "lot_size": ins["lot_size"], "buy_total": res["BUY"]["total"], "sell_total": res["SELL"]["total"], "span": res["BUY"].get("span"), "exposure": res["BUY"].get("exposure"), "plan": plan(tot)}
        print("%s %s lot %s: BUY needs %s, SELL needs %s (span %s, exposure %s)" % (code, ins["tradingsymbol"], ins["lot_size"], res["BUY"]["total"], res["SELL"]["total"], res["BUY"].get("span"), res["BUY"].get("exposure")))
        print("   plan:", out["lots"][code]["plan"])
    try:
        m = kite.margins("equity"); av = m.get("available", {})
        print("YOUR ACCOUNT (this log only): cash %s | collateral (pledged, after haircut) %s | opening balance %s | used %s" % (av.get("cash"), av.get("collateral"), av.get("opening_balance"), m.get("utilised", {}).get("debits")))
    except Exception as e: print("could not read account margins", str(e)[:100])
    os.makedirs("data/live", exist_ok=True); json.dump(out, open("data/live/margin_per_lot.json", "w"), indent=1); print("written data/live/margin_per_lot.json")
if __name__ == "__main__": main()
