"""Year-end view: rolling 12-month and calendar-year results per approach. Read only. 1 ATR = Rs 10,000 per market, cost 0.05%/side.
H = hold long with fixed size. AL/BL = long-only versions. A = breakout 55/20, B = crossover 20/100."""
import sys, os, collections, statistics as S
exec(open("2026-11-21-trend_recent.py").read().split("last = max")[0])
_pos = positions
def positions(rows, rule):
    n = len(rows)
    if rule == "H": return [0] * 100 + [1] * (n - 100)
    if rule.endswith("L"): return [max(0, x) for x in _pos(rows, rule[0])]
    return _pos(rows, rule)
def monthly(keys, rule):
    mo = collections.defaultdict(float); yr = collections.defaultdict(float)
    for nm in keys:
        p, _ = run2(nm, markets[nm], rule)
        for d, v in p.items(): mo[d[:7]] += v; yr[d[:4]] += v
    return mo, yr
def report(lab, keys, rule, since="2016-01"):
    mo, yr = monthly(keys, rule); ks = [k for k in sorted(mo) if k >= since]; m = [mo[k] for k in ks]
    r12 = sorted(sum(m[i:i + 12]) for i in range(len(m) - 11)); n = len(r12)
    print("%-26s 12m windows positive %3.0f%% | median %+6.1fL  10th pct %+6.1fL  worst %+6.1fL  best %+6.1fL | calendar years positive %d of %d: %s" % (lab, 100 * sum(1 for v in r12 if v > 0) / n, S.median(r12) / 1e5, r12[int(.1 * n)] / 1e5, r12[0] / 1e5, r12[-1] / 1e5, sum(1 for y, v in yr.items() if y >= since[:4] and v > 0), sum(1 for y in yr if y >= since[:4]), " ".join("%s %+.1f" % (y[2:], v / 1e5) for y, v in sorted(yr.items()) if y >= since[:4])))
ALL = list(markets); IND = [k for k in ALL if k in ("NIFTY", "BANKNIFTY")]; NOMET = [k for k in ALL if k not in ("GOLDM", "SILVERM", "COPPER")]
for since, tag in (("2016-01", "ALL HISTORY (2016 to Nov 2026)"), ("2021-11", "LAST 5 YEARS")):
    print("\n=====", tag, "=====")
    for lab, keys, rule in (("9 markets, breakout", ALL, "A"), ("9 markets, crossover", ALL, "B"), ("9 markets, breakout long only", ALL, "AL"), ("9 markets, crossover long only", ALL, "BL"), ("9 markets, hold long", ALL, "H"),
                            ("Nifty+BankNifty, breakout", IND, "A"), ("Nifty+BankNifty, crossover", IND, "B"), ("Nifty+BankNifty, hold", IND, "H"), ("no metals, breakout", NOMET, "A"), ("no metals, crossover", NOMET, "B")):
        report(lab, keys, rule, since)
