"""August rule (22 Nov 2026): new entries skipped in August from 1 Aug 2027, all three indexes. Earlier Augusts and other months unchanged."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper"))
import mock_detail as D, mock_trades as MT
def rules():
    return MT.rules_(None, [dict(dist21=i, sprd=i, slope=i, volr=i) for i in range(1, 20)])
N = "entries in August from 2027"
def test_listed_for_all_three_and_has_words():
    for nm in ("NSEI", "BSESN", "NSEBANK"): assert N in D.CFG[nm][3], nm
    assert N in D.RULE_WORDS
def test_dates():
    R = rules()[N]
    assert R(dict(d="2027-08-01")) and R(dict(d="2027-08-31")) and R(dict(d="2028-08-15"))     # August 2027 and later
    assert not R(dict(d="2026-08-10")) and not R(dict(d="2025-08-10")) and not R(dict(d="2021-08-10"))   # history unchanged
    assert not R(dict(d="2027-07-31")) and not R(dict(d="2027-09-01")) and not R(dict(d="2026-11-24"))
if __name__ == "__main__":
    test_listed_for_all_three_and_has_words(); test_dates(); print("august rule tests ok")
