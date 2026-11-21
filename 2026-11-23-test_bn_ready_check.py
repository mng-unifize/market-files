import importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
R = importlib.import_module("2026-11-23-bn_ready_check")
class K:
    def __init__(s, ok=True): s.ok = ok
    def profile(s):
        if not s.ok: raise RuntimeError("x")
        return {}
go = lambda ok: (lambda t: (ok, "" if ok else "not dated today"))
def run(kite=K(), stop=False, live=True, free=300000, sym="BANKNIFTY26DECFUT"):
    return R.check("2026-11-24", kite, stop, go(live), lambda: free, lambda: sym, 221191)
ok, live, lines = run(); assert ok and live and not any(l.startswith("PROBLEM") for l in lines)
ok, live, lines = run(free=100000); assert not ok and any("below one lot" in l for l in lines)
ok, live, lines = run(free=230000); assert ok and any("WARNING" in l for l in lines)
ok, live, lines = run(stop=True); assert not ok
ok, live, lines = run(kite=K(False)); assert not ok and any("login" in l for l in lines)
ok, live, lines = run(sym=""); assert not ok
ok, live, lines = run(live=False); assert ok and not live
print("ready_check tests ok")
