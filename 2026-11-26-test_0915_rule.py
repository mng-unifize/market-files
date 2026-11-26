"""Bank Nifty 9:15 candle rule (26 Nov 2026): signals on the first candle of the day are skipped. Other indexes and other slots unchanged."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper"))
import mock_detail as D, mock_trades as MT
N = "9:15 candle signal"
def R(): return MT.rules_(None, [dict(dist21=i, sprd=i, slope=i, volr=i) for i in range(1, 20)])
def test_config():
    assert N in D.CFG["NSEBANK"][3] and N in D.RULE_WORDS and N in R()
    assert N not in D.CFG["NSEI"][3] and N not in D.CFG["BSESN"][3]
def test_only_first_candle():
    f = R()[N]
    assert f(dict(minute=0)) and not f(dict(minute=5)) and not f(dict(minute=10)) and not f(dict(minute=300))
def test_words_cover_all_rules():
    for nm, (_, _, _, names) in D.CFG.items():
        for n in names: assert n in R() and n in D.RULE_WORDS
if __name__ == "__main__":
    for f in (test_config, test_only_first_candle, test_words_cover_all_rules): f()
    print("0915 rule tests ok")
