"""Turn tracker.json into the six dataset JSON files. Usage: python3 gen_ds.py TRACKER_JSON OUT_DIR"""
import json,sys,os
L=json.load(open(sys.argv[1]));O=sys.argv[2];os.makedirs(O,exist_ok=True)
w=lambda n,d:json.dump(d,open(f'{O}/{n}.json','w'))
w('summary',[dict(key=k,name=v['rule'],asof=v['asof'],start=v['start'],sessions_done=v['sessions_done'],sessions_target=20,equity=v['equity'],pnl_rs=v['pnl_rs'],pnl_pct=v['pnl_pct'],open_positions=v['open_positions'],free_slots=v['free_slots'],gate=('On' if v['gate_on'] else 'Off'),breadth_pct=v['breadth_pct'],signals_today=v['signals_today'],closed=len(v['closed'])) for k,v in L.items()])
w('pending',[dict(id=f"{k}-{p['symbol']}",key=k,order=i+1,**p) for k,v in L.items() for i,p in enumerate(v['pending'])])
w('positions',[dict(id=f"{k}-{p['symbol']}",key=k,**p) for k,v in L.items() for p in v['positions']])
w('curve',[dict(id=f"{k}-{c['date']}",key=k,**c) for k,v in L.items() for c in ([dict(date='2026-10-28',equity=1000000)]+v['equity_curve'])])
w('trades',[dict(id=f"{k}-{c['symbol']}-{c['entry_date']}",key=k,**c) for k,v in L.items() for c in v['closed']])
print({f:len(json.load(open(f'{O}/{f}'))) for f in sorted(os.listdir(O))})
