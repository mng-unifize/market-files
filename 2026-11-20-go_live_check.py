#!/usr/bin/env python3
"""Go-live gate check. READ ONLY: it reads the logs and prints PASS, FAIL, PENDING (not enough data yet) or MANUAL (you confirm) for each gate.

  python 2026-11-20-go_live_check.py [--golive 2026-11-27] [--free-margin 700000] [--cash 400000] [--confirm-killswitch]

Gates: 1 money, 2 real round-trip test, 3 pre-flight on the last 3 trading days, 4 rehearsal health (last 5 trading days), 5 slippage (last 20 orders),
6 contracts (nothing expires on or before go-live), 7 safety (STOP absent, loss stops wired, kill switch tested), 8 Monday rule (23 Nov).
Verdict GO only when every gate is PASS. Nothing is written anywhere."""
import os, sys, csv, json, subprocess, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "paper"))
L = "data/live/"; MARGIN_PCT = 0.11; BUFFER = 100000; # Owner waiver, 26 Nov 2026: rehearsal failures on these days came from the automatic stop file blocking the rehearsal (fixed 26 Nov) and from two Bank Nifty signals the Mac missed
# on 25 Nov 13:10 (cause still being traced). Only BLOCKED, NOT_FILLED, REJECTED, PARTIAL and position-mismatch rows on these days are waived. Missing orders are never waived.
WAIVED_DAYS = {"2026-11-25": "stop file blocked the rehearsal", "2026-11-26": "rehearsal book drifted after the missed 25 Nov 13:10 signals"}
SLIP_MAX = 0.03; ORDER_N = 20; DAYS_OK = 5

def rows(p):
    try:
        with open(p, newline="") as f: return list(csv.DictReader(f))
    except OSError: return []
def args(a, k, d=None): return a[a.index(k) + 1] if k in a and len(a) > a.index(k) + 1 else d

def g_money(a, tickets):
    need = 0.0
    for idx in ("Nifty 50", "Bank Nifty", "Sensex"):
        r = [x for x in tickets if x["index"] == idx]
        if r: need += float(r[-1]["last"]) * int(r[-1]["qty"]) * MARGIN_PCT
    fm, cash = args(a, "--free-margin"), args(a, "--cash")
    if fm is None or cash is None: return "MANUAL", "need Rs %s margin (11%% of 3 lots at today's quotes) plus Rs %s buffer. Re-run with --free-margin and --cash from the Kite funds screen" % (format(round(need), ","), format(BUFFER, ","))
    fm, cash = float(fm), float(cash); bad = []
    if fm < need + BUFFER: bad.append("free margin Rs %s is below Rs %s" % (format(round(fm), ","), format(round(need + BUFFER), ",")))
    if cash < need * 0.5: bad.append("cash Rs %s is below half the margin, Rs %s" % (format(round(cash), ","), format(round(need * 0.5), ",")))
    return ("FAIL", "; ".join(bad)) if bad else ("PASS", "free margin Rs %s vs need Rs %s plus buffer; cash Rs %s" % (format(round(fm), ","), format(round(need), ","), format(round(cash), ",")))

def _live_pair():
    """A real live ENTRY then EXIT, both filled, position check ok, from the live order log."""
    try: o = [r for r in rows(L + "orders_bn_live.csv") if r.get("mode") == "live"]
    except Exception: return None
    ok = ("FILLED", "REPRICED_FILLED", "FALLBACK_FILLED")
    for i in range(len(o) - 1, 0, -1):
        a, b = o[i - 1], o[i]
        if a["kind"] == "ENTRY" and b["kind"] == "EXIT" and a["status"] in ok and b["status"] in ok and b.get("position_check") == "ok": return a, b
    return None

def g_selftest():
    pr = _live_pair()
    if pr: return "PASS", "real live round trip %s: bought and sold 1 lot through the live pipeline, net Rs %s" % (pr[1]["run_at_ist"][:16], pr[1].get("trade_net") or "?")
    s = [r for r in rows(L + "selftest.csv") if r["leg"] == "SUMMARY" and r["mode"] == "live"]
    if not s: return "PENDING", "no live round trip recorded yet (data/live/selftest.csv)"
    last = s[-1]
    if last["result"] == "OK" and last["position_after"] == "0": return "PASS", "%s: bought and sold 1 lot, flat afterwards, round trip net Rs %s" % (last["run_at_ist"][:16], last["round_trip_net"] or "?")
    return "FAIL", "latest live test %s: %s %s" % (last["run_at_ist"][:16], last["result"], last["note"])

def g_preflight(golive):
    out = subprocess.run(["git", "log", "--format=%H %cs", "--", L + "preflight.json"], capture_output=True, text=True).stdout.split("\n")
    seen = {}
    for ln in out:
        if ln.strip():
            h, d = ln.split()
            if d < golive and d not in seen: seen[d] = h
    hol = set()
    try: hol = {ln.split("#")[0].strip() for ln in open("holidays.txt") if ln.strip() and not ln.startswith("#")}
    except Exception: pass
    seen = {d: h for d, h in seen.items() if d not in hol}
    days = sorted(seen)[-3:]
    if len(days) < 3: return "PENDING", "only %d day(s) of pre-flight history" % len(days)
    bad = []; info = []
    for d in days:
        j = json.loads(subprocess.run(["git", "show", "%s:%spreflight.json" % (seen[d], L)], capture_output=True, text=True).stdout or "{}")
        t = j.get("run_at", "")[11:]; info.append("%s %s %s" % (d[5:], j.get("status", "?"), t))
        if j.get("status") != "pass" or t > "09:14": bad.append(d)
    return ("FAIL", "not green by 09:13 on " + ", ".join(bad) + " (" + "; ".join(info) + ")") if bad else ("PASS", "; ".join(info))

def g_health(alerts, orders):
    days = sorted({r["run_at_ist"][:10] for r in orders})[-DAYS_OK:]
    if len(days) < DAYS_OK: return "PENDING", "%d of %d rehearsal days so far" % (len(days), DAYS_OK)
    have = {r["event_id"] for r in orders}; miss = []; bad = []; mm = []
    for r in alerts:
        d = r["event_id"].split("|")[1][:10]
        if d in days and r["event"] in ("ENTRY", "EXIT") and r["event_id"] not in have: miss.append(r["event_id"])
    waived = 0
    for r in orders:
        if r["run_at_ist"][:10] in WAIVED_DAYS and (r["status"] in ("BLOCKED", "NOT_FILLED", "REJECTED", "PARTIAL") or r.get("position_check") not in ("", "ok")):
            waived += 1; continue
        if r["run_at_ist"][:10] in days:
            if r["status"] in ("BLOCKED", "NOT_FILLED", "REJECTED", "PARTIAL"): bad.append("%s %s" % (r["event_id"], r["status"]))
            if r.get("position_check") not in ("", "ok"): mm.append(r["event_id"])
    if miss or bad or mm: return "FAIL", "missing orders %d, bad statuses %d (%s), position mismatches %d" % (len(miss), len(bad), "; ".join(bad[:3]), len(mm))
    return "PASS", "%d days, %d orders, none missing, blocked, unfilled, rejected or mismatched%s" % (len(days), sum(1 for r in orders if r["run_at_ist"][:10] in days), (" (waived by owner, 26 Nov: %d row(s) on %s)" % (waived, ", ".join(sorted(WAIVED_DAYS)))) if waived else "")

def g_slip(orders):
    f = [float(r["slip_pct"]) for r in orders if r["slip_pct"] not in ("", None) and r["status"] in ("FILLED", "REPRICED_FILLED", "FALLBACK_FILLED")][-ORDER_N:]
    if not f: return "PENDING", "no filled orders"
    avg = sum(f) / len(f); txt = "average %.4f%% per side over the last %d orders (limit %.2f%%, plan 0.01%%)" % (avg, len(f), SLIP_MAX)
    if len(f) < ORDER_N: return "PENDING", txt + ", need %d" % ORDER_N
    return ("PASS" if avg <= SLIP_MAX else "FAIL"), txt

def g_contracts(tickets, golive):
    bad = []; info = []
    for idx in ("Nifty 50", "Bank Nifty", "Sensex"):
        r = [x for x in tickets if x["index"] == idx]
        if not r: bad.append(idx + " no ticket"); continue
        r = r[-1]; info.append("%s %s expires %s" % (idx, r["contract"], r["expiry"]))
        if r["expiry"] <= golive: bad.append("%s %s expires %s, on or before go-live" % (idx, r["contract"], r["expiry"]))
    return ("FAIL", "; ".join(bad)) if bad else ("PASS", "; ".join(info))

def g_safety(a):
    import safety as S
    bits = []; bad = []
    if os.path.exists(S.STOP_FILE): bad.append("STOP file is present: " + open(S.STOP_FILE).read().strip()[:80])
    if S.DAILY_LOSS_STOP != 40000: bad.append("daily loss stop is Rs %s, expected 40,000" % S.DAILY_LOSS_STOP)
    else: bits.append("daily stop Rs 40,000")
    try:
        txt = open("paper/mock_detail.py").read()
        if "monthly loss stop" in txt and "50,000" in txt: bits.append("monthly stop Rs 50,000 wired")
        else: bad.append("monthly loss stop text not found in paper/mock_detail.py")
    except OSError: bad.append("paper/mock_detail.py not found")
    if bad: return "FAIL", "; ".join(bad)
    if "--confirm-killswitch" not in a: return "MANUAL", "; ".join(bits) + ". Send the phone STOP message once, see an order blocked, delete the STOP file, then re-run with --confirm-killswitch"
    return "PASS", "; ".join(bits) + "; kill switch test confirmed by you"

def g_monday(alerts):
    day = "2026-11-23"; today = dt.date.today().isoformat()
    try: import mock_trades as MT; frm = MT.MONDAY_BUY_FROM
    except Exception as e: return "FAIL", "cannot read MONDAY_BUY_FROM (%s)" % type(e).__name__
    if frm != day: return "FAIL", "MONDAY_BUY_FROM is %s, expected %s" % (frm, day)
    if today < day: return "PENDING", "rule is set for %s; first live Monday not reached" % day
    bn = [r for r in alerts if r["event_id"].startswith("NSEBANK|" + day) and r["event"] == "ENTRY"]
    sells = [r for r in bn if r["action"] == "SELL"]; buys = [r for r in bn if r["action"] == "BUY"]
    if sells: return "FAIL", "Bank Nifty SELL entry alerted on 23 Nov: %s" % sells[0]["event_id"]
    return "PASS", "23 Nov: no Bank Nifty sell entries; %d buy entr%s" % (len(buys), "y" if len(buys) == 1 else "ies")

def main(a):
    golive = args(a, "--golive", "2026-11-27"); alerts = rows(L + "alerts.csv"); orders = rows(L + "orders.csv"); tickets = rows(L + "tickets.csv")
    R = [("1 Money", *g_money(a, tickets)), ("2 Real round-trip test", *g_selftest()), ("3 Pre-flight, last 3 days", *g_preflight(golive)), ("4 Rehearsal health", *g_health(alerts, orders)),
         ("5 Slippage", *g_slip(orders)), ("6 Contracts and rolls", *g_contracts(tickets, golive)), ("7 Safety", *g_safety(a)), ("8 Monday rule", *g_monday(alerts))]
    print("GO-LIVE CHECK for %s (read only)\n" % golive)
    for n, s, t in R: print("%-8s %-26s %s" % (s, n, t))
    c = {k: sum(1 for r in R if r[1] == k) for k in ("PASS", "FAIL", "PENDING", "MANUAL")}
    print("\n%s" % ("VERDICT: GO. All 8 gates pass." if c["PASS"] == 8 else "VERDICT: NOT YET. %d pass, %d fail, %d pending, %d need your confirmation." % (c["PASS"], c["FAIL"], c["PENDING"], c["MANUAL"])))
    return 0 if c["PASS"] == 8 else 1

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
