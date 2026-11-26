"""Slippage tripwire (26 Nov 2026): pauses new live entries when the last 10 live fills average above 0.055% a side. Exits are never blocked."""
import sys, os, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safety as S
def fill(p, mode="live", status="FILLED", ts="2026-11-27 09:20:27"): return dict(mode=mode, status=status, slip_pct=str(p), run_at_ist=ts)
NOW = dt.datetime(2026, 11, 27, 9, 20, 30)
def chk(kind, trip): return S.pre_trade_checks(kind, "e", 1, 30, 30, 100.0, 99.9, 100.0, 100.0, NOW, set(), False, "", 0, 0, slip_trip=trip)
def test_needs_ten_fills():
    assert S.slip_tripwire([fill(0.2)] * 9)[0] is False
    assert S.slip_tripwire([fill(0.2)] * 10)[0] is True
def test_limit_is_strict():
    assert S.slip_tripwire([fill(0.055)] * 10)[0] is False and S.slip_tripwire([fill(0.0551)] * 10)[0] is True
def test_only_last_ten_and_only_live_filled():
    r = [fill(0.5)] * 5 + [fill(0.03)] * 10
    assert S.slip_tripwire(r)[0] is False                                    # old bad fills roll off
    assert S.slip_tripwire([fill(0.3, mode="shadow")] * 10)[0] is False       # rehearsal fills ignored
    assert S.slip_tripwire([fill(0.3, status="NOT_FILLED")] * 10)[0] is False
    assert S.slip_tripwire([fill("")] * 10)[0] is False
def test_resume_time():
    r = [fill(0.2, ts="2026-11-26 10:00:00")] * 10
    assert S.slip_tripwire(r)[0] is True and S.slip_tripwire(r, since="2026-11-27 08:00:00")[0] is False
def test_average_not_single_fill():
    r = [fill(0.02)] * 9 + [fill(0.3)]                                       # one bad fill: average 0.048, no trip
    assert S.slip_tripwire(r)[0] is False
def test_blocks_entry_not_exit():
    trip = S.slip_tripwire([fill(0.2)] * 10)
    ok, why = chk("ENTRY", trip); assert not ok and any(w.startswith("slippage tripwire") for w in why)
    ok, why = chk("ROLL-OPEN", trip); assert not ok
    ok, why = chk("EXIT", trip); assert ok and not why
    ok, why = chk("ENTRY", (False, 0.01, 10)); assert ok
    ok, why = chk("ENTRY", None); assert ok                                  # old callers unchanged
def test_read_since(tmp=None):
    import tempfile
    d = tempfile.mkdtemp(); p = os.path.join(d, "r.txt")
    assert S.read_since(p) == ""
    open(p, "w").write("2026-11-27 09:00:00\n"); assert S.read_since(p) == "2026-11-27 09:00:00"
if __name__ == "__main__":
    for f in (test_needs_ten_fills, test_limit_is_strict, test_only_last_ten_and_only_live_filled, test_resume_time, test_average_not_single_fill, test_blocks_entry_not_exit, test_read_since): f()
    print("tripwire tests ok")
