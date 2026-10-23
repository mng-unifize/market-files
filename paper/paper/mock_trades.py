import csv,collections,datetime as dt,statistics as S,sys,json,os,shutil
CAPITAL=300000.0;COST=0.0002
def stt_rate(d):  # futures STT, sell side. 0.0125% to Sep 2024, 0.02% to Mar 2026, 0.05% after (Zerodha page shows 0.05% now; April 2026 start is my belief)
    return 0.000125 if d<"2024-10-01" else (0.0002 if d<"2026-04-01" else 0.0005)
def tcost(p0,p1,u,direction,d_in,d_out,bse=False):
    buy=(p0 if direction==1 else p1)*u;sell=(p1 if direction==1 else p0)*u
    d_sell=d_out if direction==1 else d_in
    stt=stt_rate(d_sell)*sell;brk=min(20.0,0.0003*buy)+min(20.0,0.0003*sell)
    exch=0.0 if bse else 0.0000183*(buy+sell);sebi=1e-7*(buy+sell);stamp=0.00002*buy
    gst=0.18*(brk+exch+sebi);return stt+brk+exch+sebi+stamp+gst
def cost_parts(p0,p1,u,direction,d_in,d_out,bse=False):
    buy=(p0 if direction==1 else p1)*u;sell=(p1 if direction==1 else p0)*u
    d_sell=d_out if direction==1 else d_in
    stt=stt_rate(d_sell)*sell;brk=min(20.0,0.0003*buy)+min(20.0,0.0003*sell)
    exch=0.0 if bse else 0.0000183*(buy+sell);sebi=1e-7*(buy+sell);stamp=0.00002*buy;gst=0.18*(brk+exch+sebi)
    return dict(brokerage=brk,stt=stt,exchange=exch,sebi=sebi,stamp=stamp,gst=gst,total=stt+brk+exch+sebi+stamp+gst)
LOTS={"NSEI":65,"BSESN":20,"NSEBANK":30}
def ema(x,n):
    a=2/(n+1);o=[x[0]]
    for v in x[1:]:o.append(a*v+(1-a)*o[-1])
    return o
class M:
    def __init__(s,nm,path):
        s.nm=nm;s.u=LOTS[nm];R=list(csv.DictReader(open(f"{path}/{nm}_5m.csv")))
        s.B=[dict(ts=r["ts_ist"],d=r["ts_ist"][:10],o=float(r["open"]),h=float(r["high"]),l=float(r["low"]),c=float(r["close"])) for r in R]
        B=s.B;n=len(B);c=[b["c"] for b in B];s.E9=ema(c,9);s.E21=ema(c,21);s.E108=ema(c,108);s.E252=ema(c,252)
        TR=[B[0]["h"]-B[0]["l"]]+[max(B[i]["h"]-B[i]["l"],abs(B[i]["h"]-B[i-1]["c"]),abs(B[i]["l"]-B[i-1]["c"])) for i in range(1,n)]
        s.A=ema(TR,14);s.A100=[];run=0
        for i in range(n):
            run+=TR[i]
            if i>=100:run-=TR[i-100]
            s.A100.append(run/min(i+1,100))
        pdm=[0]+[max(B[i]["h"]-B[i-1]["h"],0) if (B[i]["h"]-B[i-1]["h"])>(B[i-1]["l"]-B[i]["l"]) else 0 for i in range(1,n)]
        mdm=[0]+[max(B[i-1]["l"]-B[i]["l"],0) if (B[i-1]["l"]-B[i]["l"])>(B[i]["h"]-B[i-1]["h"]) else 0 for i in range(1,n)]
        sp,sm=ema(pdm,14),ema(mdm,14);dx=[]
        for i in range(n):
            a=s.A[i] or 1;pi=100*sp[i]/a;mi=100*sm[i]/a;dx.append(100*abs(pi-mi)/(pi+mi) if pi+mi else 0)
        s.ADX=ema(dx,14)
        s.days=[];s.dfirst={};s.dlast={}
        for i,b in enumerate(B):
            if b["d"] not in s.dfirst:s.dfirst[b["d"]]=i;s.days.append(b["d"])
            s.dlast[b["d"]]=i
        s.dopen={d:B[s.dfirst[d]]["o"] for d in s.days};s.dclose={d:B[s.dlast[d]]["c"] for d in s.days}
        s.dhigh={d:max(B[i]["h"] for i in range(s.dfirst[d],s.dlast[d]+1)) for d in s.days};s.dlow={d:min(B[i]["l"] for i in range(s.dfirst[d],s.dlast[d]+1)) for d in s.days}
        dc=[s.dclose[d] for d in s.days];s.D9=ema(dc,9);s.D21=ema(dc,21)
        s.sma20={};s.pc={};
        for k,d in enumerate(s.days):
            prev=dc[max(0,k-20):k];s.sma20[d]=sum(prev)/len(prev) if prev else dc[k];s.pc[d]=dc[k-1] if k else None
        s.mins=[int(b["ts"][11:13])*60+int(b["ts"][14:16])-555 for b in B]
        s.S=dt.date.fromisoformat(s.days[0]);s.start=s.S+dt.timedelta(days=20)
        s.nyears=min(5,max(1,round((dt.date.fromisoformat(s.days[-1])-s.S).days/365.25)))
    def year(s,d):return min(s.nyears-1,(dt.date.fromisoformat(d)-s.S).days//365)
    def feats(s,i,x,prev):
        B=s.B;b=B[i];d=b["d"];rng=max(b["h"]-b["l"],1e-9);A=s.A[i] or 1;pc=s.pc[d]
        body=lambda j:(B[j]["c"]-B[j]["o"])
        return dict(minute=s.mins[i],wd=dt.date.fromisoformat(d).weekday(),adx=s.ADX[i],volr=A/s.A100[i],
            dist21=x*(b["c"]-s.E21[i])/A,sprd=abs(s.E9[i]-s.E21[i])/A,slope=x*((s.E9[i]-s.E21[i])-(s.E9[i-3]-s.E21[i-3]))/A,
            gap=((s.dopen[d]-pc)/pc*100*x if pc else 0),dtrend=x*(b["c"]-s.sma20[d])/s.sma20[d]*100,
            htf=(1 if (x==1 and s.E108[i]>s.E252[i]) or (x==-1 and s.E108[i]<s.E252[i]) else 0),
            eng=bool(body(i)*body(i-1)<0 and abs(body(i))>abs(body(i-1))),inside=bool(b["h"]<=B[i-1]["h"] and b["l"]>=B[i-1]["l"]),
            doji=bool(abs(body(i))/rng<0.15),prevlen=prev[0],prevlost=prev[1])
    def gen(s,mode,stop=None):
        B=s.B;u=s.u;E9,E21=s.E9,s.E21;pos=0;ent=0;peak=0;out=[];prev=[99,False];F=None
        def close(i,p):
            nonlocal pos,prev
            gross=pos*(p-B[ent]["c"])*u;cst=tcost(B[ent]["c"],p,u,pos,F["d"],B[i]["d"],s.nm=="BSESN");pnl=gross-cst
            t=dict(F,ex=i,p1=p,pnl=pnl,gross=gross,cost=cst,d_ex=B[i]["d"],bars=i-ent);out.append(t);prev=[i-ent,pnl<=0];pos=0
        for i in range(1,len(B)):
            b=B[i];d=b["d"];last=(i==len(B)-1) or B[i+1]["d"]!=d
            if pos and stop:
                p0=B[ent]["c"]
                if pos==1:
                    S_=p0*(1-stop)
                    if b["l"]<=S_:close(i,min(b["o"],S_))
                else:
                    S_=p0*(1+stop)
                    if b["h"]>=S_:close(i,max(b["o"],S_))
            x=0
            if E9[i]>E21[i] and E9[i-1]<=E21[i-1]:x=1
            elif E9[i]<E21[i] and E9[i-1]>=E21[i-1]:x=-1
            if x and not(mode=="flat" and last) and not (mode.startswith("adapt") and False):
                if pos and x!=pos:close(i,b["c"])
                if pos==0 and dt.date.fromisoformat(d)>=s.start:
                    pos=x;ent=i;F=dict(ent=i,dir=x,m0=i+1,p0=b["c"],d=d,**s.feats(i,x,prev))
            if pos and last:
                if mode=="flat":close(i,b["c"])
                elif mode.startswith("adapt"):
                    pnl_open=pos*(b["c"]-B[ent]["c"]);rngd=max(s.dhigh[d]-s.dlow[d],1e-9)
                    strength=((b["c"]-s.dlow[d])/rngd if pos==1 else (s.dhigh[d]-b["c"])/rngd)
                    aligned=(pos==1 and b["c"]>s.sma20[d]) or (pos==-1 and b["c"]<s.sma20[d])
                    keep={"adapt1":pnl_open>0,"adapt2":pnl_open>0 and strength>=0.75,"adapt3":pnl_open>0 and aligned,"adapt4":aligned}[mode]
                    if not keep:close(i,b["c"])
        if pos:
            close(len(B)-1,B[-1]["c"]);out[-1]["open"]=True
        return out
    def gen_daily(s,mode):
        out=[];D=s.days;dc=[s.dclose[d] for d in D];u=s.u;pos=0;ent=None;F=None;prev=[99,False]
        for k in range(1,len(D)):
            d=D[k]
            if mode=="d_intra":
                if dt.date.fromisoformat(d)<s.start:continue
                x=1 if s.D9[k-1]>s.D21[k-1] else -1
                p0,p1=s.dopen[d],s.dclose[d];pnl=x*(p1-p0)*u-tcost(p0,p1,u,x,d,d,s.nm=="BSESN")
                out.append(dict(ent=s.dfirst[d],ex=s.dlast[d],dir=x,p0=p0,p1=p1,m0=s.dfirst[d],d=d,pnl=pnl,bars=0))
            else:
                x=1 if s.D9[k]>s.D21[k] and s.D9[k-1]<=s.D21[k-1] else (-1 if s.D9[k]<s.D21[k] and s.D9[k-1]>=s.D21[k-1] else 0)
                if x and x!=pos:
                    if pos:
                        p1=dc[k];pnl=pos*(p1-ent[1])*u-tcost(ent[1],p1,u,pos,ent[0],d,s.nm=="BSESN");out.append(dict(ent=s.dlast[ent[0]],ex=s.dlast[d],dir=pos,p0=ent[1],p1=p1,m0=s.dlast[ent[0]]+1,d=ent[0],pnl=pnl,bars=0))
                    pos=x;ent=(d,dc[k]) if dt.date.fromisoformat(d)>=s.start else None
                    if ent is None:pos=0
        if pos:d=D[-1];p1=dc[-1];out.append(dict(ent=s.dlast[ent[0]],ex=s.dlast[d],dir=pos,p0=ent[1],p1=p1,m0=s.dlast[ent[0]]+1,d=ent[0],pnl=pos*(p1-ent[1])*u-tcost(ent[1],p1,u,pos,ent[0],D[-1],s.nm=="BSESN"),bars=0))
        return out
    def drop(s,T):
        n=len(s.B);cl=[b["c"] for b in s.B];op=[0.0]*n;real=[0.0]*n;u=s.u
        for t in T:
            for j in range(t["m0"],t["ex"]):op[j]=t["dir"]*(cl[j]-t["p0"])*u
            real[t["ex"]]+=t["pnl"]
        run=0.0;pk=CAPITAL;dd=0;lo=CAPITAL
        for j in range(n):
            run+=real[j];e=CAPITAL+run+op[j];pk=max(pk,e);dd=max(dd,pk-e);lo=min(lo,e)
        return round(dd),round(lo)
def by_year(m,T):
    y=collections.defaultdict(lambda:[0.0,0.0,0])
    for t in T:
        r=y[m.year(t["d"])];r[2]+=1
        if t["pnl"]>0:r[0]+=t["pnl"]
        else:r[1]+=t["pnl"]
    return [ (round(y[k][0]),round(y[k][1]),round(y[k][0]+y[k][1]),y[k][2]) for k in range(m.nyears)]
def rules_(m,train):
    q=lambda f,p:sorted(t[f] for t in train)[int(len(train)*p)]
    R={"engulfing candle":lambda t:t["eng"],"inside bar":lambda t:t["inside"],"doji candle":lambda t:t["doji"],
       "lunch window 12:15-13:45":lambda t:180<=t["minute"]<270,"first 15 min":lambda t:t["minute"]<15,"first 45 min":lambda t:t["minute"]<45,
       "after 14:45":lambda t:t["minute"]>=330,"previous trade lost":lambda t:t["prevlost"],"choppy (last trade under 30 min)":lambda t:t["prevlen"]<6,
       "ADX under 15":lambda t:t["adx"]<15,"ADX under 20":lambda t:t["adx"]<20,"against daily trend":lambda t:t["dtrend"]<0,
       "against 2-day trend":lambda t:t["htf"]==0,"gap against trade over 0.5%":lambda t:t["gap"]<-0.5,"gap with trade over 0.5%":lambda t:t["gap"]>0.5}
    for w,nm in enumerate(("Monday","Tuesday","Wednesday","Thursday","Friday")):R["entries on "+nm]=lambda t,w=w:t["wd"]==w
    for f,lab in (("dist21","price close to 21-line"),("sprd","narrow average spread"),("slope","flat average slope")):
        th=q(f,0.25);R["weak momentum: "+lab]=lambda t,f=f,th=th:t[f]<th
    th=q("volr",0.25);R["low volatility"]=lambda t,th=th:t["volr"]<th
    th=q("volr",0.75);R["high volatility"]=lambda t,th=th:t["volr"]>th
    return R
def select(m,T,ntrain,maxr=3):
    tr=[t for t in T if m.year(t["d"])<ntrain];R=rules_(m,tr);chosen=[];cur=T;tested=len(R)
    base=lambda X,yr:sum(t["pnl"] for t in X if m.year(t["d"])==yr)
    for _ in range(maxr):
        best=None
        for nm,f in R.items():
            if nm in chosen:continue
            kept=[t for t in cur if not f(t)];sk=len(cur)-len(kept)
            if sk<0.05*len(cur) or sk>0.6*len(cur):continue
            gains=[base(kept,y)-base(cur,y) for y in range(ntrain)];tot=sum(gains)
            if tot<=0 or sum(1 for g in gains if g>0)<max(1,ntrain-1):continue
            if best is None or tot>best[0]:best=(tot,nm,kept)
        if not best:break
        chosen.append(best[1]);cur=best[2]
    return chosen,cur,tested
STRATS=[("5-min, square off daily","flat"),("5-min, hold overnight","hold"),("5-min, hold only if it looks good (A: in profit)","adapt1"),("5-min, hold if in profit and closing strong","adapt2"),("5-min, hold if in profit and with daily trend","adapt3"),("5-min, hold if with daily trend","adapt4")]
def study(m,ntrain,verbose=True):
    res={}
    cands=[(lab,m.gen(mode)) for lab,mode in STRATS]
    cands+= [("daily candle, hold until crossover",m.gen_daily("d_hold")),("daily candle, trade open to close",m.gen_daily("d_intra"))]
    for lab,T in cands:
        T=[t for t in T if dt.date.fromisoformat(t["d"])>=m.start]
        base=by_year(m,T)
        if lab.startswith("daily"):res[lab]=dict(base=base,chosen=[],filt=base,dd0=m.drop(T),dd1=m.drop(T),tested=0,train_net=sum(r[2] for r in base[:ntrain]),test_net=sum(r[2] for r in base[ntrain:]),base_test=sum(r[2] for r in base[ntrain:]));continue
        ch,kept,tested=select(m,T,ntrain);filt=by_year(m,kept)
        res[lab]=dict(base=base,chosen=ch,filt=filt,dd0=m.drop(T),dd1=m.drop(kept),tested=tested,train_net=sum(r[2] for r in filt[:ntrain]),test_net=sum(r[2] for r in filt[ntrain:]),base_test=sum(r[2] for r in base[ntrain:]))
    return res
def f(n):
    s=str(abs(round(n)))
    if len(s)>3:
        h=s[-3:];r=s[:-3];p=[]
        while len(r)>2:p.insert(0,r[-2:]);r=r[:-2]
        if r:p.insert(0,r)
        s=",".join(p)+","+h
    return ("-" if n<0 else "")+s

# ---------------- mock trades ----------------
KITE="data/kite";INTRA="data/intraday";OUTD="data/mock";START_LOG="2026-10-23"
CH={"NSEI":("hold",["high volatility","entries on Monday","lunch window 12:15-13:45"]),
    "NSEBANK":("hold",["entries on Monday","weak momentum: price close to 21-line","weak momentum: flat average slope"]),
    "BSESN":("adapt4",["weak momentum: narrow average spread","high volatility","weak momentum: price close to 21-line"])}
NAMES={"NSEI":"Nifty 50","NSEBANK":"Bank Nifty","BSESN":"Sensex"}
VAR=[("square off daily","flat",None),("hold overnight","hold",None),("recipe (with skips)",None,None)]
def combine(nm,work):
    rows={}
    for r in csv.DictReader(open(f"{KITE}/{nm}_5m.csv")):rows[r["ts_ist"]]=r
    last=max(rows)
    p=f"{INTRA}/{nm}_5m.csv"
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            if r["ts_ist"]>last and r["ts_ist"][11:]<"15:30":rows[r["ts_ist"]]=r
    os.makedirs(work,exist_ok=True)
    with open(f"{work}/{nm}_5m.csv","w",newline="") as f:
        w=csv.writer(f);w.writerow(["ts_ist","open","high","low","close","volume"])
        for k in sorted(rows):r=rows[k];w.writerow([k,r["open"],r["high"],r["low"],r["close"],r.get("volume",0)])
    return max(rows)
def leg(price,u,side,d,bse):
    v=price*u;brk=min(20.0,0.0003*v);exch=0.0 if bse else 0.0000183*v;sebi=1e-7*v
    stt=stt_rate(d)*v if side=="sell" else 0.0;stamp=0.00002*v if side=="buy" else 0.0
    gst=0.18*(brk+exch+sebi);return dict(brokerage=brk,stt=stt,exchange=exch,sebi=sebi,stamp=stamp,gst=gst,total=brk+exch+sebi+stt+stamp+gst)
def main():
    work="/tmp/mock_work";os.makedirs(OUTD,exist_ok=True);log=[];lines=[]
    D=None;summary={}
    for nm in ("NSEI","NSEBANK","BSESN"):
        lastts=combine(nm,work);m=M(nm,work);B=m.B;u=m.u;bse=nm=="BSESN";D=m.days[-1];prevclose=m.dclose[m.days[-2]]
        for lab,mode,_ in VAR:
            mode_=mode or CH[nm][0];T=[t for t in m.gen(mode_) if dt.date.fromisoformat(t["d"])>=m.start]
            rules=None
            if mode is None:
                tr=[t for t in T if m.year(t["d"])<3];R=rules_(m,tr);rules={k:R[k] for k in CH[nm][1]}
            for t in T:
                ent_d=B[t["ent"]]["d"];ex_d=B[t["ex"]]["d"]
                if ent_d<START_LOG:continue
                why=[k for k,f in rules.items() if f(t)] if rules else []
                if why:
                    log.append(dict(index=NAMES[nm],variant=lab,status="SKIPPED",direction="BUY" if t["dir"]==1 else "SELL",entry_time=B[t["ent"]]["ts"],entry_price=B[t["ent"]]["c"],exit_time="",exit_price="",gross="",costs="",net="",reason="; ".join(why)));continue
                isopen=bool(t.get("open"));gross=t["dir"]*(t["p1"]-B[t["ent"]]["c"])*u
                cst=tcost(B[t["ent"]]["c"],t["p1"],u,t["dir"],ent_d,ex_d,bse) if not isopen else leg(B[t["ent"]]["c"],u,"buy" if t["dir"]==1 else "sell",ent_d,bse)["total"]
                log.append(dict(index=NAMES[nm],variant=lab,status="OPEN (carried overnight)" if isopen else "closed",direction="BUY" if t["dir"]==1 else "SELL",entry_time=B[t["ent"]]["ts"],entry_price=B[t["ent"]]["c"],exit_time="" if isopen else B[t["ex"]]["ts"],exit_price=t["p1"],gross=round(gross),costs=round(cst),net=round(gross-cst),reason=""))
    flds=["index","variant","status","direction","entry_time","entry_price","exit_time","exit_price","gross","costs","net","reason"]
    with open(f"{OUTD}/trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=flds);w.writeheader()
        for r in log:w.writerow(r)
    # daily + cumulative summary
    out=[f"# Mock trades report for {D} (1 lot each, Rs 3 lakh per index, Zerodha charges)",""]
    for nm in ("Nifty 50","Bank Nifty","Sensex"):
        out.append(f"## {nm}");out.append("| Variant | Trades closed today | Skipped today | Net of trades closed today | Open position | Total since "+START_LOG+" (closed, after charges) |");out.append("|---|---|---|---|---|---|")
        for lab,_,_ in VAR:
            L=[r for r in log if r["index"]==nm and r["variant"]==lab]
            ct=[r for r in L if r["status"]=="closed" and r["exit_time"][:10]==D];sk=[r for r in L if r["status"]=="SKIPPED" and r["entry_time"][:10]==D]
            op=[r for r in L if r["status"].startswith("OPEN")];tot=sum(r["net"] for r in L if r["status"]=="closed")
            out.append(f"| {lab} | {len(ct)} (net Rs {sum(r['net'] for r in ct):,}) | {len(sk)} | Rs {sum(r['net'] for r in ct):,} | "+(f"{op[0]['direction']} from {op[0]['entry_time'][5:]} (open gross Rs {op[0]['gross']:,})" if op else "none")+f" | Rs {tot:,} |")
        out.append("")
    out.append("Trade-by-trade detail is in data/mock/trades.csv. Skipped signals show the reason.")
    open(f"{OUTD}/report-{D}.md","w").write("\n".join(out));open(f"{OUTD}/report-latest.md","w").write("\n".join(out));print("\n".join(out))
if __name__=="__main__":main()
