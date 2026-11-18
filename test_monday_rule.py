"""Bank Nifty Monday rule (18 Nov 2026): Monday entries skipped until 23 Nov 2026, then only Monday SELL entries skipped. Nifty 50 and Sensex unchanged."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper"))
import mock_detail as D, mock_trades as MT
def rules():
    train = [dict(dist21=i, sprd=i, slope=i, volr=i) for i in range(1, 20)]
    return MT.rules_(None, train)
def t(wd, d, dr): return dict(wd=wd, d=d, dir=dr)
def test_config_and_words():
    R = rules()
    for nm, (_, _, _, names) in D.CFG.items():
        for n in names: assert n in R and n in D.RULE_WORDS, (nm, n)
    assert "entries on Monday" in D.CFG["NSEI"][3] and "entries on Monday" in D.CFG["BSESN"][3]
    assert "entries on Monday" not in D.CFG["NSEBANK"][3]
def skipped(day, wd, dr):
    R = rules(); names = [n for n in D.CFG["NSEBANK"][3] if "Monday" in D.RULE_WORDS[n] or "Monday" in n]
    return [n for n in names if R[n](t(wd, day, dr))]
def test_before_and_after_effective_date():
    assert skipped("2026-11-16", 0, 1) and skipped("2026-11-16", 0, -1)          # earlier Mondays stay skipped, history unchanged
    assert not skipped("2026-11-23", 0, 1)                                        # Monday BUY allowed from 23 Nov
    assert skipped("2026-11-23", 0, -1) == ["entries on Monday sell"]             # Monday SELL still skipped
    assert not skipped("2026-11-24", 1, 1) and not skipped("2026-11-24", 1, -1)  # other days untouched
def test_date_is_read_at_call_time():
    old = MT.MONDAY_BUY_FROM
    try:
        MT.MONDAY_BUY_FROM = "2000-01-01"; assert not skipped("2026-11-16", 0, 1) and skipped("2026-11-16", 0, -1)
    finally: MT.MONDAY_BUY_FROM = old
if __name__ == "__main__":
    for f in (test_config_and_words, test_before_and_after_effective_date, test_date_is_read_at_call_time): f()
    print("monday rule tests ok")
