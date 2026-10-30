"""IBT paper tracker for two backtested rules. Research only, no orders. Usage: python3 ibt_tracker.py REPO_DIR START_DATE OUT_DIR"""
import sys,os,io,zipfile,json
import numpy as np,pandas as pd
REPO,START,OUT=sys.argv[1],sys.argv[2],sys.argv[3]
os.makedirs(OUT,exist_ok=True)
dd=sorted(x for x in os.listdir(REPO+'/data') if x[:2]=='20' and any(f.startswith('BhavCopy_NSE') for f in os.listdir(f'{REPO}/data/{x}')))
dd=dd[-345:]
rows=[]
for d in dd:
    f=[x for x in os.listdir(f'{REPO}/data/{d}') if x.startswith('BhavCopy_NSE')][0]
    z=zipfile.ZipFile(f'{REPO}/data/{d}/{f}');df=pd.read_csv(io.StringIO(z.open(z.namelist()[0]).read().decode()),index_col=False)
    df=df.rename(columns={'TckrSymb':'sym','SctySrs':'ser','OpnPric':'o','ClsPric':'c','PrvsClsgPric':'pc','TtlTrfVal':'val','ISIN':'isin'})[['sym','ser','o','c','pc','val','isin']]
    df=df[(df.ser=='EQ')&df['isin'].astype(str).str.startswith('INE')&(df.c>0)&(df.pc>0)&(df.o>0)].drop_duplicates('sym');df['d']=d;rows.append(df)
A=pd.concat(rows);dates=pd.to_datetime(sorted(A.d.unique()))
A['d']=pd.to_datetime(A.d)
mat=lambda c:A.pivot(index='d',columns='sym',values=c).reindex(dates)
C,PC,O,V=mat('c'),mat('pc'),mat('o'),mat('val')
r=(C/PC-1).where(lambda x:x.abs()<=0.9)
P=np.exp(np.log1p(r.fillna(0)).where(C.notna()).fillna(0).cumsum())
Pf=P.where(C.notna()).ffill();Oadj=O*(P/C);raw=C.ffill()
r=Pf.pct_change();r=r.where(r.abs()<0.5,0)
liq=(V.rolling(20,min_periods=15).mean()>=2.5e6)&(raw>=2)
hi252=Pf.rolling(252,min_periods=200).max();s200=Pf.rolling(200,min_periods=200).mean()
volx=V/V.rolling(50,min_periods=30).mean()
rk=lambda x:x.where(liq).rank(axis=1,pct=True)
x60=Pf/Pf.shift(60)-1;x12=Pf.shift(21)/Pf.shift(252)-1
ew=(1+r.where(liq).mean(axis=1).fillna(0)).cumprod();gate=ew>ew.rolling(100).mean()
breadth=(Pf>Pf.rolling(100).mean()).where(liq).mean(axis=1)
S3=(Pf>=0.95*hi252)&(rk(x60)>=0.95)&(volx>=1.5)&liq&gate.values[:,None]
S2=(rk(x12)>=0.95)&liq&(Pf>s200)
strength={'R3':x60,'R2':x12}
RULES={'R3':dict(name='Rank 3: near 52-week high + top 60-day strength, market gate on, 15% trailing stop',sig=S3),
       'R2':dict(name='Rank 2: top 5% 12-month momentum, above 200-day average, 15% trailing stop',sig=S2)}
STOP,HOLD,SLOTS,COST,CAP=0.15,120,10,0.005,1000000.0
t0=int(np.searchsorted(dates,pd.Timestamp(START)))
last=len(dates)-1
sym=Pf.columns
def sim(key):
    sig=RULES[key]['sig'].values;st=strength[key].values
    cash=CAP;pos=[];closed=[];eq=[]
    for t in range(t0,last+1):
        # exits flagged yesterday execute at today's open
        keep=[]
        for p in pos:
            if p['exit_flag']:
                s=p['s'];px=Oadj.values[t,s];px=Pf.values[t,s] if px!=px else px
                v=p['v']*px/Pf.values[t-1,s]*(1-COST);cash+=v
                closed.append(dict(symbol=sym[s],entry_date=str(dates[p['e']].date()),exit_date=str(dates[t].date()),entry=round(float(raw.values[p['e'],s]),2),exit=round(float(raw.values[t,s]),2),pnl_pct=round((v/p['in']-1)*100,2),pnl_rs=round(v-p['in'],0),reason=p['why']))
            else: keep.append(p)
        pos=keep
        for p in pos:
            s=p['s'];p['v']*=Pf.values[t,s]/Pf.values[t-1,s]
        # entries from yesterday's close signals, open today
        eqv=cash+sum(p['v'] for p in pos)
        held={p['s'] for p in pos}
        cand=[s for s in np.where(sig[t-1])[0] if s not in held and Oadj.values[t,s]==Oadj.values[t,s] and Oadj.values[t,s]/Pf.values[t-1,s]<1.10]
        cand.sort(key=lambda s:-st[t-1,s])
        for s in cand:
            if len(pos)>=SLOTS: break
            a=eqv/SLOTS
            if cash<a*0.999: break
            cash-=a;ent=Oadj.values[t,s]
            pos.append(dict(s=s,e=t,v=a*(1-COST)*Pf.values[t,s]/ent,**{'in':a},hi=Pf.values[t,s],exit_flag=False,why=''))
            held.add(s)
        # end of day: update trail, flag exits
        for p in pos:
            s=p['s'];c=Pf.values[t,s]
            if c==c:
                p['hi']=max(p['hi'],c)
                if c<p['hi']*(1-STOP): p['exit_flag']=True;p['why']='15% trailing stop'
                elif t-p['e']+1>=HOLD: p['exit_flag']=True;p['why']='120-session limit'
        eq.append((str(dates[t].date()),round(cash+sum(p['v'] for p in pos),0)))
    # state at last close
    posout=[]
    for p in pos:
        s=p['s'];k=raw.values[last,s]/Pf.values[last,s]
        posout.append(dict(symbol=sym[s],entry_date=str(dates[p['e']].date()),entry=round(float(raw.values[p['e'],s]),2),last=round(float(raw.values[last,s]),2),
          pnl_pct=round((p['v']/p['in']-1)*100,2),stop=round(float(p['hi']*(1-STOP)*k),2),sessions=int(last-p['e']+1),sell_next_open=bool(p['exit_flag']),why=p['why']))
    held={p['s'] for p in pos};free=SLOTS-len(pos)
    cand=[s for s in np.where(sig[last])[0] if s not in held]
    cand.sort(key=lambda s:-st[last,s])
    pend=[dict(symbol=sym[s],close=round(float(raw.values[last,s]),2),strength_pct=round(float(st[last,s])*100,1),action=('BUY next open (skip if gap above 10%)' if i<free else 'Waitlist, no free slot')) for i,s in enumerate(cand[:15])]
    eqn=cash+sum(p['v'] for p in pos)
    n_s=last-t0+1
    return dict(key=key,rule=RULES[key]['name'],asof=str(dates[last].date()),start=START,sessions_done=int(n_s),sessions_target=20,equity=round(eqn,0),pnl_rs=round(eqn-CAP,0),pnl_pct=round((eqn/CAP-1)*100,2),
      open_positions=len(pos),free_slots=free,cash=round(cash,0),positions=posout,pending=pend,closed=closed,equity_curve=[dict(date=a,equity=b) for a,b in eq],
      gate_on=bool(gate.iloc[last]),breadth_pct=round(float(breadth.iloc[last])*100,1),signals_today=int(sig[last].sum()))
res={k:sim(k) for k in RULES}
json.dump(res,open(OUT+'/tracker.json','w'),indent=1)
for k,v in res.items():
    print(k,v['asof'],'sessions',v['sessions_done'],'equity',v['equity'],'pnl%',v['pnl_pct'],'open',v['open_positions'],'closed',len(v['closed']),'signals',v['signals_today'],'gate',v['gate_on'])
    print([ (p['symbol'],p['pnl_pct']) for p in v['positions']]);print(v['pending'][:6])
