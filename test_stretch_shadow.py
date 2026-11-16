import os, tempfile, stretch_shadow as S
def mk(d, closes):
    os.makedirs(d + "/candles"); 
    with open(d + "/candles/NSEI_5m.csv", "w") as f:
        for day, c in closes: f.write("%s 15:25,1,1,1,%s,0\n" % (day, c))
def test_flag():
    d = tempfile.mkdtemp(); days = ["2026-11-0%d" % i for i in range(1, 10)]
    mk(d, [(x, 100) for x in days[:5]] + [("2026-11-06", 100), ("2026-11-07", 110)] + [("2026-11-09", 105)])
    S.log_entry(d, "NSEI", {"day": "2026-11-09", "side": "SELL", "bar": "x"}, "e1")
    S.log_entry(d, "NSEI", {"day": "2026-11-09", "side": "SELL", "bar": "x"}, "e1")
    rows = open(d + "/stretch_shadow.csv").read().splitlines()
    assert len(rows) == 2 and rows[1].split(",")[-1] == "yes", rows
def test_short_history_and_missing_file_never_raise():
    d = tempfile.mkdtemp(); S.log_entry(d, "NSEI", {"day": "2026-11-09", "side": "BUY", "bar": "x"}, "e2")
    assert not os.path.exists(d + "/stretch_shadow.csv")
