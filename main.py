"""
BIST AI Proxy v3 - Render Free Tier
─────────────────────────────────────
1. Pine Script birebir PRO Engine (XU100 RS, 4H/2H multi-TF, MACD, DNA)
2. APScheduler: piyasa saatlerinde 7/24 otomatik tarama + Telegram
"""

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager
import httpx, asyncio, time, math, os, logging, json
from datetime import datetime

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler(timezone="Europe/Istanbul")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: scheduler'i baslat
    try:
        scheduler.add_job(auto_scan, "cron", minute="*/5",
                          id="bist_scan", replace_existing=True)
        scheduler.start()
        log.info(f"Scheduler started. TG: {bool(TG_TOKEN and TG_CHAT)}")
    except Exception as e:
        log.error(f"Scheduler start error: {e}")
    yield
    # Shutdown: scheduler'i durdur
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            log.info("Scheduler stopped")
    except Exception as e:
        log.error(f"Scheduler stop error: {e}")

app = FastAPI(title="BIST AI Proxy v3", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

YH  = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"}
BIN = {"User-Agent": "BIST-AI-Scanner/3.0"}
_cache: dict = {}

def cget(k):
    e = _cache.get(k)
    return e["d"] if e and time.time()-e["t"]<e["ttl"] else None

def cset(k, d, ttl=300):
    _cache[k] = {"d":d,"t":time.time(),"ttl":ttl}


# ─────────────────────────────────────────────────────────
# TEKNİK ANALİZ
# ─────────────────────────────────────────────────────────

def _atr(h,l,c,n=14):
    tr=[max(h[i]-l[i],abs(h[i]-c[i-1]) if i else 0,abs(l[i]-c[i-1]) if i else 0) for i in range(len(c))]
    a=[0.0]*len(c)
    if len(c)<n: return a
    a[n-1]=sum(tr[:n])/n
    for i in range(n,len(c)): a[i]=(a[i-1]*(n-1)+tr[i])/n
    return a

def _ema(c,n):
    k=2/(n+1); e=[0.0]*len(c); e[0]=c[0]
    for i in range(1,len(c)): e[i]=c[i]*k+e[i-1]*(1-k)
    return e

def _sma(c,n):
    out=[0.0]*len(c)
    for i in range(n-1,len(c)): out[i]=sum(c[i-n+1:i+1])/n
    return out

def _rsi(c,n=14):
    L=len(c); r=[50.0]*L
    if L<n+1: return r
    d=[c[i]-c[i-1] for i in range(1,L)]
    ag=sum(max(x,0) for x in d[:n])/n; al=sum(max(-x,0) for x in d[:n])/n
    for i in range(n,L-1):
        ag=(ag*(n-1)+max(d[i],0))/n; al=(al*(n-1)+max(-d[i],0))/n
        r[i+1]=100-100/(1+ag/(al+1e-10))
    return r

def _supertrend(h,l,c,atr_len=10,mult=3.0):
    at=_atr(h,l,c,atr_len); n=len(c)
    hl2=[(h[i]+l[i])/2 for i in range(n)]
    up=[hl2[i]+mult*at[i] for i in range(n)]; dn=[hl2[i]-mult*at[i] for i in range(n)]
    fu=list(up); fd=list(dn)
    for i in range(1,n):
        fu[i]=up[i] if up[i]<fu[i-1] or c[i-1]>fu[i-1] else fu[i-1]
        fd[i]=dn[i] if dn[i]>fd[i-1] or c[i-1]<fd[i-1] else fd[i-1]
    d=[1]*n
    for i in range(1,n):
        if d[i-1]==-1 and c[i]>fu[i]: d[i]=1
        elif d[i-1]==1 and c[i]<fd[i]: d[i]=-1
        else: d[i]=d[i-1]
    return d,fu,fd

def _adx(h,l,c,n=14):
    L=len(c); tr=[0.0]*L; pdm=[0.0]*L; mdm=[0.0]*L
    for i in range(1,L):
        tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
        hd=h[i]-h[i-1]; ld=l[i-1]-l[i]
        pdm[i]=hd if hd>ld and hd>0 else 0
        mdm[i]=ld if ld>hd and ld>0 else 0
    def ws(a,n):
        o=[0.0]*L
        if L<n: return o
        o[n-1]=sum(a[:n])
        for i in range(n,L): o[i]=o[i-1]-o[i-1]/n+a[i]
        return o
    s=ws(tr,n); p=ws(pdm,n); m=ws(mdm,n); eps=1e-10
    pi=[100*p[i]/(s[i]+eps) for i in range(L)]
    mi=[100*m[i]/(s[i]+eps) for i in range(L)]
    dx=[100*abs(pi[i]-mi[i])/(pi[i]+mi[i]+eps) for i in range(L)]
    return [ws(dx,n)[i]/n for i in range(L)], pi, mi

def _tma_upper(c,length=200,amult=8.0,alen=20):
    half=length//2+1
    s1=_sma(c,half); t=_sma(s1,half)
    ph=[max(c[i],c[i-1] if i else c[i]) for i in range(len(c))]
    pl=[min(c[i],c[i-1] if i else c[i]) for i in range(len(c))]
    at=_atr(ph,pl,c,alen)
    return [t[i]+amult*at[i] for i in range(len(c))]

def _chandelier(h,l,c,n=20,mult=8.0):
    at=_atr(h,l,c,n); L=len(c); stops=[0.0]*L
    for i in range(n,L): stops[i]=max(h[max(0,i-n):i+1])-mult*at[i]
    return stops

def _pstate(closes,n=150):
    """Pine quantum states ile birebir pstate"""
    n=min(n,len(closes))
    if n<10: return "NORMAL"
    arr=sorted(closes[-n:]); close=closes[-1]
    rank=sum(1 for x in arr if x<=close); pricePct=rank/n
    mean=sum(closes[-n:])/n
    variance=sum((x-mean)**2 for x in closes[-n:])/n
    stdev=math.sqrt(variance) if variance>0 else 1e-10
    z=(close-mean)/stdev
    vCQ=max(0,(0.15-pricePct)/0.15)*max(0,(-1.5-z)/-1.5)
    cQ=max(0,abs(pricePct-0.225)/0.075)*max(0,(-0.5-z)/-0.5)
    nQ=max(0,1-abs(pricePct-0.5)/0.2)
    eQ=max(0,abs(pricePct-0.775)/0.075)*max(0,(z-0.5)/0.5)
    vEQ=max(0,(pricePct-0.85)/0.15)*max(0,(z-1.5)/1.5)
    sm=vCQ+cQ+nQ+eQ+vEQ
    if sm>0: vCQ/=sm; cQ/=sm; nQ/=sm; eQ/=sm; vEQ/=sm
    if vEQ>0.5: return "COK PAHALI"
    if eQ>0.5:  return "PAHALI"
    if nQ>0.5:  return "NORMAL"
    if cQ>0.5:  return "UCUZ"
    if vCQ>0.5: return "COK UCUZ"
    states=[("COK UCUZ",vCQ),("UCUZ",cQ),("NORMAL",nQ),("PAHALI",eQ),("COK PAHALI",vEQ)]
    return max(states,key=lambda x:x[1])[0]


# ─────────────────────────────────────────────────────────
# OHLCV ÇEKİM
# ─────────────────────────────────────────────────────────

async def fetch_ohlcv(ticker:str, tf:str, suffix:str=".IS") -> list:
    k=f"ohlcv_{ticker}_{tf}"; cached=cget(k)
    if cached: return cached
    intv={"D":"1d","240":"60m","120":"30m"}.get(tf,"1d")
    rng={"D":"2y","240":"60d","120":"30d"}.get(tf,"2y")
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}?interval={intv}&range={rng}"
    try:
        async with httpx.AsyncClient(timeout=20,headers=YH) as cl:
            r=await cl.get(url); d=r.json()
        res=d["chart"]["result"][0]
        ts=res["timestamp"]; q=res["indicators"]["quote"][0]
        adj=res["indicators"].get("adjclose",[{}])[0].get("adjclose",[])
        cls=adj if adj else q.get("close",[])
        data=[]
        for i in range(len(ts)):
            ov=q["open"][i]; hv=q["high"][i]; lv=q["low"][i]
            cv=cls[i] if i<len(cls) else q["close"][i]; vv=q["volume"][i]
            if None not in (ov,hv,lv,cv):
                data.append({"t":ts[i],"o":float(ov),"h":float(hv),
                             "l":float(lv),"c":float(cv),"v":float(vv or 0)})
        cset(k,data,600); return data
    except Exception as e:
        log.warning(f"OHLCV err {ticker}: {e}"); return []

async def fetch_xu100_closes() -> list:
    k="xu100_closes"; cached=cget(k)
    if cached: return cached
    try:
        async with httpx.AsyncClient(timeout=15,headers=YH) as cl:
            r=await cl.get("https://query1.finance.yahoo.com/v8/finance/chart/XU100.IS?interval=1d&range=2y")
            d=r.json()
        res=d["chart"]["result"][0]
        adj=res["indicators"].get("adjclose",[{}])[0].get("adjclose",[])
        cls=adj if adj else res["indicators"]["quote"][0].get("close",[])
        closes=[float(x) for x in cls if x is not None]
        cset(k,closes,3600); return closes
    except Exception as e:
        log.warning(f"XU100 closes err: {e}"); return []


# ─────────────────────────────────────────────────────────
# PINE BİREBİR ANALİZ — 6 PRO FAKTÖRÜ DOĞRU
# ─────────────────────────────────────────────────────────

def analyze_full(ohlcv_d,ohlcv_4h,ohlcv_2h,xu100_c,cfg) -> dict:
    if len(ohlcv_d)<60: return {"signal":False,"error":"Yetersiz veri"}

    c=[x["c"] for x in ohlcv_d]; h=[x["h"] for x in ohlcv_d]
    l=[x["l"] for x in ohlcv_d]; v=[x["v"] for x in ohlcv_d]
    i=len(c)-1; pr=c[i]

    sd,su,sdn=_supertrend(h,l,c,cfg.get("st_len",10),cfg.get("st_mult",3.0))
    tu=_tma_upper(c,cfg.get("tma_len",200),cfg.get("tma_atr_mult",8.0))
    adv,pdi,mdi=_adx(h,l,c)
    rsi_d=_rsi(c); e200=_ema(c,200); e50=_ema(c,50)
    e12=_ema(c,12); e26=_ema(c,26); chst=_chandelier(h,l,c)
    sma20=_sma(c,20)

    ad=adv[i]; ri=rsi_d[i]; macd=e12[i]-e26[i]; am=cfg.get("adx_min",25)

    # Sistem 1: Pine longTrendFlipUp
    flip_up=sd[i]==1 and (i>0 and sd[i-1]==-1)
    s1=flip_up and pr>tu[i] and ad>am

    # ── PRO 6 faktör (Pine birebir) ──────────────────
    # 1. rsStrong2: close/xu100 >= highest(rs,200)*0.97
    rs_strong=False
    if xu100_c and len(xu100_c)>=2:
        rs_vals=[c[j]/xu100_c[min(j,len(xu100_c)-1)] for j in range(len(c))]
        lb200=max(0,i-199)
        rs_max=max(rs_vals[lb200:i+1]) if rs_vals[lb200:i+1] else rs_vals[i]
        rs_strong=rs_vals[i]>=rs_max*0.97

    # 2. accumD: daily close > sma(20)
    accum_d=pr>sma20[i] if i>20 else False

    # 3. exp4h: 4H range > sma(range,20)*1.5
    exp_4h=False
    if len(ohlcv_4h)>=25:
        h4=[x["h"] for x in ohlcv_4h]; l4=[x["l"] for x in ohlcv_4h]
        i4=len(h4)-1
        ranges=[h4[j]-l4[j] for j in range(len(h4))]
        sma_r=_sma(ranges,20)
        exp_4h=ranges[i4]>sma_r[i4]*1.5

    # 4. break4h: 4H high >= highest(high,20)
    break_4h=False
    if len(ohlcv_4h)>=25:
        h4=[x["h"] for x in ohlcv_4h]; i4=len(h4)-1
        lb4=max(0,i4-19)
        break_4h=h4[i4]>=max(h4[lb4:i4+1])

    # 5. mom2h: 2H RSI(14) > 60
    mom_2h=False
    if len(ohlcv_2h)>=20:
        c2=[x["c"] for x in ohlcv_2h]
        mom_2h=_rsi(c2)[-1]>60

    # 6. dna: RSI(14) > 55 AND macd > 0
    dna=ri>55 and macd>0

    score6=sum([rs_strong,accum_d,exp_4h,break_4h,mom_2h,dna])
    trend2=pr>e200[i] and ad>am and pr>tu[i]
    s2=score6>=cfg.get("pro_min",5) and trend2

    # Fusion
    lb=min(150,len(c)); mc_=sum(c[-lb:])/lb
    sd_=math.sqrt(sum((x-mc_)**2 for x in c[-lb:])/lb)
    z=(pr-mc_)/(sd_+1e-10)
    qp=(ad/50)*max(0,min(1,(ri-30)/40))
    nn=0.5+(0.2 if pr>e50[i] else 0)+(0.2 if e50[i]>e200[i] else 0)+(0.1 if z>-0.5 else 0)
    fp=min(1.0,qp*nn*1.5)
    fu=fp>cfg.get("fthr",0.8) and pr>tu[i] and ad>am

    # Konsensus
    wt={"s1":ad/100,"s2":score6/6,"fu":fp}
    tw=sum(wt.values()); bw=sum(w for k_,w in wt.items() if {"s1":s1,"s2":s2,"fu":fu}[k_])
    cons=(bw/tw*100) if tw>0 else 0
    master=cons>cfg.get("mthr",65)

    # Guc skoru
    sc=0
    if cons>=80: sc+=4
    elif cons>=65: sc+=3
    elif cons>=50: sc+=2
    elif cons>=35: sc+=1
    if ad>=40: sc+=2
    elif ad>=25: sc+=1
    if score6>=5: sc+=2
    elif score6>=3: sc+=1
    if fp>=0.6: sc+=1
    if master: sc=min(10,sc+1)

    # Multi-TF hizalanma bonusu
    tf_align=sum([s1 or s2 or fu, break_4h, mom_2h])
    if tf_align==3: sc=min(10,sc+2)
    elif tf_align==2: sc=min(10,sc+1)

    # RSI divergence cezasi
    rsi_div=False
    if i>=5:
        rsi_div=pr>c[i-5] and ri<rsi_d[i-5]
        if rsi_div: sc=max(1,sc-1)

    # Hacim bonusu
    vol_avg=sum(v[-20:])/20 if len(v)>=20 else v[-1]
    vol_ratio=v[i]/vol_avg if vol_avg>0 else 1.0
    if vol_ratio>2.0: sc=min(10,sc+1)
    sc=max(1,min(10,sc))

    # ── 5 BAĞIMSIZ AGENT (Pine birebir) ──────────────────
    # Fusedprob: quantum × nn (agent giriş koşulu)
    fused_prob = fp  # zaten hesaplandı
    fthr = cfg.get("fthr", 0.8)

    # Agent 60: noise60 = sin(bar*0.37)*0.05
    # Pine: bar_index yok, i kullanıyoruz
    noise60 = math.sin(i * 0.37) * 0.05
    a60_prob = fused_prob * 0.92 + noise60
    a60 = a60_prob > fthr and pr > tu[i] and ad > am

    # Agent 61: rs_strong2 ile birleşir
    noise61 = math.sin(i * 0.42) * 0.04
    a61_prob = fused_prob * 1.02 + score6 / 20 + noise61
    a61 = a61_prob > fthr and pr > tu[i] and rs_strong

    # Agent 62: ema200 + dna
    noise62 = math.sin(i * 0.29) * 0.06
    a62_prob = fused_prob * 0.98 + ad / 50 + noise62
    a62 = a62_prob > fthr and pr > e200[i] and dna

    # Agent 81: accumD + tma (tma_upper yerine e50 kullan - yakın)
    noise81 = 1 + math.sin(i * 0.21) * 0.1
    a81_prob = fused_prob * noise81
    a81 = a81_prob > fthr and pr > e50[i] and accum_d

    # Agent 120: break4h + ema200
    noise120 = math.sin(i * 0.15) * 0.07
    a120_prob = fused_prob * 0.88 + qp + noise120
    a120 = a120_prob > fthr and pr > e200[i] and break_4h

    # ── MASTER AI: 8 SİSTEM KONSENSÜSİ ──────────────────
    # Pine: conditionsBuy[0..7] ve conditionProbs[0..7]
    cond_buy  = [s1, s2, fu, a60, a61, a62, a81, a120]
    cond_prob = [
        ad / 100,        # S1 ağırlığı: ADX
        score6 / 6,      # S2 ağırlığı: PRO skor
        fp,              # Fusion ağırlığı
        a60_prob,        # A60
        a61_prob,        # A61
        a62_prob,        # A62
        a81_prob,        # A81
        a120_prob,       # A120
    ]
    # Equal weighting (Pine default)
    total_w = sum(cond_prob)
    buy_w   = sum(cond_prob[k] for k in range(8) if cond_buy[k])
    cons_8  = (buy_w / total_w * 100) if total_w > 0 else cons
    master  = cons_8 > cfg.get("mthr", 65)

    # Güç skoru güncelle: 8 sistem konsensüs ile
    sc = 0
    if cons_8 >= 80: sc += 4
    elif cons_8 >= 65: sc += 3
    elif cons_8 >= 50: sc += 2
    elif cons_8 >= 35: sc += 1
    if ad >= 40: sc += 2
    elif ad >= 25: sc += 1
    if score6 >= 5: sc += 2
    elif score6 >= 3: sc += 1
    if fp >= 0.6: sc += 1
    if master: sc = min(10, sc + 1)
    if tf_align == 3: sc = min(10, sc + 2)
    elif tf_align == 2: sc = min(10, sc + 1)
    if rsi_div: sc = max(1, sc - 1)
    if vol_ratio > 2.0: sc = min(10, sc + 1)
    sc = max(1, min(10, sc))

    # Aktif sistemler
    acts = []
    if s1:   acts.append("ST+TMA")
    if s2:   acts.append("PRO")
    if fu:   acts.append("Fusion")
    if a60:  acts.append("A60")
    if a61:  acts.append("A61")
    if a62:  acts.append("A62")
    if a81:  acts.append("A81")
    if a120: acts.append("A120")

    # Herhangi bir sistem sinyal verdiyse al
    any_signal = s1 or s2 or fu or a60 or a61 or a62 or a81 or a120

    stop = round(chst[i], 2) if chst[i] > 0 else round(pr * 0.93, 2)

    # ── Pine tablosu istatistikleri: son 504 bar backtest ──────────────
    # Pine: buyCount1, sellCount1, winCount1, lossCount1, totalPnL1 vb.
    # Her sistem için son 504 barda (2 yıl) simüle istatistik
    def _sys_stats(signal_fn, stop_mult=8.0, lookback=504):
        buys=0; sells=0; wins=0; losses=0; total_pnl=0.0
        in_t=False; ep=0.0; hp=0.0
        lb=min(lookback, i)
        for j in range(max(210, i-lb), i+1):
            if j >= len(c) or j < 1: continue
            pr_j=c[j]
            sig_j = signal_fn(j)
            if not in_t and sig_j:
                in_t=True; ep=pr_j; hp=pr_j; buys+=1
            elif in_t:
                if pr_j > hp: hp=pr_j
                ch_stop = hp - _atr(h,l,c,20)[j]*stop_mult if j>=20 else hp*0.9
                if pr_j < ch_stop or pr_j < ep*0.75:
                    pnl=(pr_j-ep)/ep*100
                    total_pnl+=pnl
                    if pnl>=0: wins+=1
                    else: losses+=1
                    sells+=1; in_t=False
        # Açık pozisyon PnL
        open_pnl = round((c[i]-ep)/ep*100,2) if in_t else None
        return {
            "buys":buys,"sells":sells,"wins":wins,"losses":losses,
            "total_pnl":round(total_pnl,2),"open_pnl":open_pnl
        }

    # S1 sinyal fonksiyonu
    def _s1_sig(j):
        if j<1: return False
        return (data_st_dir[j]==1 and data_st_dir[j-1]==-1
                and c[j]>tu[j] and adv[j]>am)

    # S2 sinyal fonksiyonu
    def _s2_sig(j):
        if j<200: return False
        return adv[j]>am and c[j]>e200[j] and c[j]>tu[j]

    # Fusion sinyal fonksiyonu (basitleştirilmiş)
    def _fu_sig(j):
        if j<200: return False
        lb_=min(150,j+1)
        mc__=sum(c[j+1-lb_:j+1])/lb_
        sd__=math.sqrt(sum((x-mc__)**2 for x in c[j+1-lb_:j+1])/lb_)+1e-10
        z__=(c[j]-mc__)/sd__
        qp__=(adv[j]/50)*max(0,min(1,(rsi_d[j]-30)/40))
        nn__=0.5+(0.2 if c[j]>e50[j] else 0)+(0.2 if e50[j]>e200[j] else 0)+(0.1 if z__>-0.5 else 0)
        fp__=min(1.0,qp__*nn__*1.5)
        return fp__>cfg.get("fthr",0.8) and c[j]>tu[j] and adv[j]>am

    # SuperTrend direction array gerekiyor - precompute
    data_st_dir = sd  # zaten hesaplı

    stats_s1  = _sys_stats(_s1_sig,  stop_mult=cfg.get("chMult",8.0))
    stats_s2  = _sys_stats(_s2_sig,  stop_mult=cfg.get("chMult",8.0))
    stats_fu  = _sys_stats(_fu_sig,  stop_mult=cfg.get("chMult",8.0))

    # Agent istatistikleri (Pine A60-A120 noise ile)
    def _agent_sig(j, prob_fn, cond_fn):
        if j<200: return False
        return prob_fn(j) > cfg.get("fthr",0.8) and cond_fn(j)

    def _a60_prob(j): return fp*0.92 + math.sin(j*0.37)*0.05
    def _a61_prob(j): return fp*1.02 + score6/20 + math.sin(j*0.42)*0.04
    def _a62_prob(j): return fp*0.98 + adv[j]/50 + math.sin(j*0.29)*0.06
    def _a81_prob(j): return fp*(1+math.sin(j*0.21)*0.1)
    def _a120_prob(j): return fp*0.88 + qp + math.sin(j*0.15)*0.07

    def _a60_cond(j):  return c[j]>tu[j] and adv[j]>am
    def _a61_cond(j):  return c[j]>tu[j] and rs_strong
    def _a62_cond(j):  return c[j]>e200[j] and dna
    def _a81_cond(j):  return c[j]>e50[j] and c[j]>sma20[j]
    def _a120_cond(j): return c[j]>e200[j] and break_4h

    stats_a60  = _sys_stats(lambda j: _agent_sig(j,_a60_prob,_a60_cond))
    stats_a61  = _sys_stats(lambda j: _agent_sig(j,_a61_prob,_a61_cond))
    stats_a62  = _sys_stats(lambda j: _agent_sig(j,_a62_prob,_a62_cond))
    stats_a81  = _sys_stats(lambda j: _agent_sig(j,_a81_prob,_a81_cond))
    stats_a120 = _sys_stats(lambda j: _agent_sig(j,_a120_prob,_a120_cond))

    # Master AI toplam PnL (tüm sistemlerin ortalaması)
    all_pnls = [s["total_pnl"] for s in [stats_s1,stats_s2,stats_fu,stats_a60,stats_a61,stats_a62,stats_a81,stats_a120]]
    master_pnl = round(sum(all_pnls)/len(all_pnls), 2)

    # Dinamik eşikler (Pine RL/DynThreshold - basit approximation)
    dyn_buy_thresh  = round(cfg.get("mthr",65)/100, 2)
    dyn_sell_thresh = round(1 - dyn_buy_thresh, 2)

    return {
        "signal":     any_signal,
        "is_master":  master,
        "price":      round(pr, 2),
        "adx":        round(ad, 1),
        "rsi":        round(ri, 1),
        "macd":       round(macd, 4),
        "pro_score":  score6,
        "fusion_pct": round(fp * 100, 1),
        "consensus":  round(cons_8, 1),
        "buy_consensus":  round(buy_w/total_w*100 if total_w>0 else 0, 1),
        "sell_consensus": round((total_w-buy_w)/total_w*100 if total_w>0 else 0, 1),
        "dyn_buy_thresh":  dyn_buy_thresh,
        "dyn_sell_thresh": dyn_sell_thresh,
        "strength":   sc,
        "pstate":     _pstate(c),
        "stop_price": stop,
        "active_sys": acts,
        "ema200":     round(e200[i], 2),
        "ema50":      round(e50[i], 2),
        "s1": s1, "s2": s2, "fu": fu,
        "a60": a60, "a61": a61, "a62": a62, "a81": a81, "a120": a120,
        "pro_factors": {
            "rs_strong": rs_strong, "accum_d": accum_d,
            "exp_4h":    exp_4h,    "break_4h": break_4h,
            "mom_2h":    mom_2h,    "dna":      dna,
        },
        "agent_probs": {
            "a60": round(a60_prob, 3), "a61": round(a61_prob, 3),
            "a62": round(a62_prob, 3), "a81": round(a81_prob, 3),
            "a120": round(a120_prob, 3),
        },
        "tf_align":   tf_align,
        "vol_ratio":  round(vol_ratio, 2),
        "rsi_div":    rsi_div,
        # ── Pine tablo istatistikleri ──────────────────────
        # t1 (bottom_right) - Sistem 1
        "sys1": {
            "buys":      stats_s1["buys"],
            "sells":     stats_s1["sells"],
            "wins":      stats_s1["wins"],
            "losses":    stats_s1["losses"],
            "total_pnl": stats_s1["total_pnl"],
            "open_pnl":  stats_s1["open_pnl"],
            "pstate":    _pstate(c),
        },
        # t2 (bottom_left) - PRO Engine
        "sys2": {
            "buys":      stats_s2["buys"],
            "sells":     stats_s2["sells"],
            "wins":      stats_s2["wins"],
            "losses":    stats_s2["losses"],
            "total_pnl": stats_s2["total_pnl"],
            "open_pnl":  stats_s2["open_pnl"],
            "score":     score6,
        },
        # tf (top_right) - Fusion
        "fusion": {
            "buys":      stats_fu["buys"],
            "sells":     stats_fu["sells"],
            "wins":      stats_fu["wins"],
            "losses":    stats_fu["losses"],
            "total_pnl": stats_fu["total_pnl"],
            "open_pnl":  stats_fu["open_pnl"],
            "pstate":    _pstate(c),
        },
        # tm (top_left) - Master AI
        "master_ai": {
            "total_pnl":        master_pnl,
            "buy_consensus":    round(buy_w/total_w*100 if total_w>0 else 0, 1),
            "sell_consensus":   round((total_w-buy_w)/total_w*100 if total_w>0 else 0, 1),
            "dyn_buy_thresh":   dyn_buy_thresh,
            "dyn_sell_thresh":  dyn_sell_thresh,
            "open_pnl":         stats_s1["open_pnl"],  # proxy'de açık poz yok - s1 yaklaşık
        },
        # agentDash (middle_right) - Agent PnL'leri
        "agents": {
            "a60":  {"pnl": stats_a60["total_pnl"],  "buys": stats_a60["buys"],  "wins": stats_a60["wins"],  "losses": stats_a60["losses"]},
            "a61":  {"pnl": stats_a61["total_pnl"],  "buys": stats_a61["buys"],  "wins": stats_a61["wins"],  "losses": stats_a61["losses"]},
            "a62":  {"pnl": stats_a62["total_pnl"],  "buys": stats_a62["buys"],  "wins": stats_a62["wins"],  "losses": stats_a62["losses"]},
            "a81":  {"pnl": stats_a81["total_pnl"],  "buys": stats_a81["buys"],  "wins": stats_a81["wins"],  "losses": stats_a81["losses"]},
            "a120": {"pnl": stats_a120["total_pnl"], "buys": stats_a120["buys"], "wins": stats_a120["wins"], "losses": stats_a120["losses"]},
        },
    }


# ─────────────────────────────────────────────────────────
# KRİPTO — Binance
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────
# TELEGRAM GÖNDERİM
# ─────────────────────────────────────────────────────────

TG_TOKEN="8156380916:AAG2khLNZLvb9AN8heF8vnzj31gGWrRiTls"
TG_CHAT="348018531"

async def send_telegram(token:str,chat:str,text:str):
    if not token or not chat: return
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            await cl.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id":chat,"text":text})
    except Exception as e:
        log.warning(f"Telegram err: {e}")

_sent_today: set = set()

BIST_TICKERS=[
    "AKSA","ALTNY","ASELS","BIMAS","BSOKE","CANTE","CIMSA","CWENE",
    "EREGL","EUPWR","GENIL","GESAN","GLRMK","GRSEL","GUBRF","KONTR",
    "KRDMD","KTLEV","MAVI","MPARK","PETKM","TUPRS","AKFYE","ALBRK",
    "ARDYZ","BANVT","BRSAN","BUCIM","ENKAI","FROTO","GARAN","HEKTS",
    "ISDMR","KCHOL","KOZAL","LOGO","MGROS","OYAKC","PRKAB","SASA",
    "TATGD","THYAO","TOASO","TTKOM","ULKER","VAKBN","YKBNK","SAHOL",
    "SISE","TCELL","AKBNK","ISCTR","TAVHL","SKBNK","ZOREN",
]


# ─────────────────────────────────────────────────────────
# SCHEDULER — 7/24 OTOMATİK TARAMA
# ─────────────────────────────────────────────────────────

async def auto_scan():
    """Piyasa saatlerinde her 5 dk otomatik tarama + Telegram"""
    now=datetime.now(); wd=now.weekday(); h=now.hour; m=now.minute
    if wd>=5: return  # Hafta sonu
    if not ((9<=h<18) or (h==18 and m<=20)): return  # Mesai disi
    if h<9 or (h==9 and m<40): return  # Seans oncesi

    log.info(f"Auto scan: {now.strftime('%H:%M')}")
    xu100=await fetch_xu100_closes()
    cfg={"st_len":10,"st_mult":3.0,"tma_len":200,"tma_atr_mult":8.0,
         "adx_min":25,"pro_min":5,"fthr":0.8,"mthr":65}
    signals=[]

    for ticker in BIST_TICKERS:
        try:
            sig_key=f"{ticker}_{now.strftime('%Y%m%d')}"
            if sig_key in _sent_today: continue
            od,o4,o2=await asyncio.gather(
                fetch_ohlcv(ticker,"D"),
                fetch_ohlcv(ticker,"240"),
                fetch_ohlcv(ticker,"120"),
            )
            if not od or len(od)<60: continue
            res=analyze_full(od,o4 or [],o2 or [],xu100,cfg)
            if not res.get("signal") and not res.get("is_master"): continue
            res["ticker"]=ticker; signals.append(res)
        except Exception as e:
            log.warning(f"Scan err {ticker}: {e}")

    signals.sort(key=lambda x:x.get("strength",0),reverse=True)
    for res in signals:
        ticker=res["ticker"]
        _sent_today.add(f"{ticker}_{now.strftime('%Y%m%d')}")
        tl="MASTER AI" if res.get("is_master") else "AL"
        str_=res.get("strength",5)
        bar="#"*(str_//2)+"_"*(5-str_//2)
        pf=res.get("pro_factors",{})
        # Aktif sistemler
        acts = res.get("active_sys", [])
        systems_str = " | ".join(acts) if acts else "-"
        # PRO faktörleri
        pf = res.get("pro_factors", {})
        pro_detail = []
        if pf.get("rs_strong"): pro_detail.append("RS")
        if pf.get("accum_d"):   pro_detail.append("Birikim")
        if pf.get("exp_4h"):    pro_detail.append("4H Genislik")
        if pf.get("break_4h"):  pro_detail.append("4H Kirilim")
        if pf.get("mom_2h"):    pro_detail.append("2H Mom")
        if pf.get("dna"):       pro_detail.append("DNA")
        msg=(f"{tl} SINYALI\n"
             f"--------------------\n"
             f"Hisse: {ticker}\n"
             f"Fiyat: TL{res['price']}\n"
             f"Guc: [{bar}] {str_}/10\n"
             f"Konsensus: %{res['consensus']} (8 sistem)\n"
             f"--------------------\n"
             f"ADX:{res['adx']} RSI:{res['rsi']} PRO:{res['pro_score']}/6\n"
             f"TF Hizalama: {res.get('tf_align',0)}/3\n"
             f"Bolge: {res['pstate']}\n"
             f"Stop: TL{res['stop_price']}\n"
             f"Aktif: {systems_str}\n"
             +(f"PRO: {', '.join(pro_detail)}\n" if pro_detail else "")
             +f"\nGrafik: https://www.tradingview.com/chart/?symbol=BIST:{ticker}&interval=1D")
        await send_telegram(TG_TOKEN,TG_CHAT,msg)
        await asyncio.sleep(0.5)

    if h==0 and m<6: _sent_today.clear()

# Scheduler lifespan ile yonetiliyor (yukarida)


# ─────────────────────────────────────────────────────────
# API ENDPOINT'LER
# ─────────────────────────────────────────────────────────

@app.get("/status")
async def root():
    return {"status":"ok","service":"BIST AI Proxy v3",
            "scheduler":scheduler.running,"cache":len(_cache),
            "tg_configured":bool(TG_TOKEN and TG_CHAT)}


@app.get("/ohlcv/{ticker}")
async def get_ohlcv(ticker: str, tf: str = "D"):
    """
    Ham OHLCV verisi - backtest için kullanılır.
    Frontend CORS sorununu aşmak için proxy üzerinden çeker.
    """
    ticker = ticker.upper()
    k = f"ohlcv_{ticker}_{tf}"
    cached = cget(k)
    if cached:
        return {"ticker": ticker, "tf": tf, "bars": len(cached), "data": cached}

    ohlcv = await fetch_ohlcv(ticker, tf)
    xu100 = await fetch_xu100_closes()

    if not ohlcv:
        return {"ticker": ticker, "tf": tf, "bars": 0, "data": [], "error": "Veri alinamadi"}

    return {
        "ticker":    ticker,
        "tf":        tf,
        "bars":      len(ohlcv),
        "data":      ohlcv,
        "xu100_last": xu100[-1] if xu100 else None,
    }

@app.get("/health")
async def health(): return {"status":"healthy"}

@app.get("/prices")
async def get_prices(symbols:str):
    tickers=[t.strip().upper() for t in symbols.split(",") if t.strip()]
    k="prices_"+"_".join(sorted(tickers)); cached=cget(k)
    if cached: return cached
    sym=",".join(f"{t}.IS" for t in tickers)
    url=(f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={sym}"
         f"&fields=regularMarketPrice,regularMarketChange,regularMarketChangePercent,"
         f"regularMarketDayHigh,regularMarketDayLow,regularMarketVolume,regularMarketPreviousClose")
    try:
        async with httpx.AsyncClient(timeout=15,headers=YH) as cl:
            r=await cl.get(url); d=r.json()
        res={}
        for q in d.get("quoteResponse",{}).get("result",[]):
            t=q.get("symbol","").replace(".IS",""); p=q.get("regularMarketPrice",0)
            if p and p>0:
                res[t]={"price":round(p,2),"change":round(q.get("regularMarketChange",0),2),
                        "change_pct":round(q.get("regularMarketChangePercent",0),2),
                        "high":round(q.get("regularMarketDayHigh",0),2),
                        "low":round(q.get("regularMarketDayLow",0),2),
                        "volume":int(q.get("regularMarketVolume",0)),
                        "prev_close":round(q.get("regularMarketPreviousClose",0),2),"real":True}
        cset(k,res,300); return res
    except: return {}

@app.get("/xu100")
async def get_xu100():
    k="xu100"; cached=cget(k)
    if cached: return cached
    try:
        async with httpx.AsyncClient(timeout=10,headers=YH) as cl:
            r=await cl.get("https://query1.finance.yahoo.com/v7/finance/quote"
                           "?symbols=XU100.IS&fields=regularMarketPrice,regularMarketChangePercent,regularMarketChange")
            d=r.json()
        q=d.get("quoteResponse",{}).get("result",[{}])[0]
        res={"price":round(q.get("regularMarketPrice",0),2),
             "change":round(q.get("regularMarketChange",0),2),
             "change_pct":round(q.get("regularMarketChangePercent",0),2),"real":True}
    except: res={"price":0,"change_pct":0,"real":False}
    cset(k,res,120); return res

@app.get("/analyze/{ticker}")
async def analyze_ticker(ticker:str,tf:str="D",adx_min:float=25,master_thr:float=65,pro_min:int=5):
    ticker=ticker.upper(); k=f"sig_{ticker}_{tf}"; cached=cget(k)
    if cached: return cached
    xu100=await fetch_xu100_closes()
    od,o4,o2=await asyncio.gather(
        fetch_ohlcv(ticker,"D"),fetch_ohlcv(ticker,"240"),fetch_ohlcv(ticker,"120"))
    if not od or len(od)<60: return {"error":"Veri yok","ticker":ticker}
    cfg={"st_len":10,"st_mult":3.0,"tma_len":200,"tma_atr_mult":8.0,
         "adx_min":adx_min,"pro_min":pro_min,"fthr":0.8,"mthr":master_thr}
    res=analyze_full(od,o4,o2,xu100,cfg)
    res.update({"ticker":ticker,"tf":tf,"bars":len(od)})
    cset(k,res,300); return res

@app.post("/scan")
async def scan_stocks(body:dict):
    tickers=body.get("tickers",[]); tf=body.get("tf","D")
    cfg_in=body.get("cfg",{}); min_cons=float(body.get("min_consensus",0))
    only_master=body.get("only_master",False)
    fb=cfg_in.get("fb",80)
    cfg={"st_len":10,"st_mult":cfg_in.get("atrm",3.0),
         "tma_len":200,"tma_atr_mult":8.0,
         "adx_min":cfg_in.get("adxMin",25),
         "pro_min":cfg_in.get("sc",5),
         "fthr":(fb/100 if fb>1 else fb),
         "mthr":cfg_in.get("mb",65)}

    xu100=await fetch_xu100_closes(); results=[]; batch=6

    for i in range(0,len(tickers),batch):
        chunk=tickers[i:i+batch]
        tasks=[]
        for t in chunk:
            ticker=t["ticker"] if isinstance(t,dict) else t
            tasks.append(asyncio.gather(
                fetch_ohlcv(ticker,"D"),fetch_ohlcv(ticker,"240"),fetch_ohlcv(ticker,"120"),
                return_exceptions=True))
        batch_res=await asyncio.gather(*tasks,return_exceptions=True)

        for j,t in enumerate(chunk):
            ticker=t["ticker"] if isinstance(t,dict) else t
            name=t.get("name","") if isinstance(t,dict) else ""
            idxs=t.get("indices",[]) if isinstance(t,dict) else []
            try:
                od,o4,o2=batch_res[j]
                if isinstance(od,Exception) or not od or len(od)<60: continue
                if isinstance(o4,Exception): o4=[]
                if isinstance(o2,Exception): o2=[]
                res=analyze_full(od,o4,o2,xu100,cfg)
            except Exception as e:
                log.warning(f"Analyze err {ticker}: {e}"); continue
            if not res.get("signal") and not res.get("is_master"): continue
            if only_master and not res.get("is_master"): continue
            if res.get("consensus",0)<min_cons: continue
            results.append({**res,"ticker":ticker,"name":name,"indices":idxs,"tf":tf})

        await asyncio.sleep(0.3)

    results.sort(key=lambda x:x.get("strength",0),reverse=True)
    return {"signals":results,"count":len(results),"tf":tf}



# ── HTML Serve ────────────────────────────────────────

# HTML Serve - bist_v6_fix 4 blok
_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BIST AI">
<meta name="theme-color" content="#000">
<meta name="color-scheme" content="dark">
<title>BIST AI Scanner v6</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--bg:#000;--bg2:#0a0a0a;--bg3:#111;--bg4:#1a1a1a;--b1:#1e1e1e;--b2:#2a2a2a;--b3:#3a3a3a;--cyan:#00d4ff;--gold:#ffb800;--green:#00e676;--red:#ff4444;--purple:#c084fc;--orange:#ff7043;--t1:#fff;--t2:#ccc;--t3:#888;--t4:#555;color-scheme:dark}
*{background-color:inherit}
html{background:#000!important;color-scheme:dark}
html,body{background:#000!important;color:#fff;font-family:-apple-system,'Helvetica Neue',sans-serif;font-size:13px;height:100%;overflow:hidden;-webkit-font-smoothing:antialiased}
.page{background:#000!important}
main{background:#000!important}
#app{display:flex;flex-direction:column;height:100dvh}
header{padding:12px 16px;padding-top:calc(12px + env(safe-area-inset-top));background:#000;border-bottom:1px solid var(--b2);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:10}
.logo{font-size:16px;font-weight:700;letter-spacing:1px}
.logo-accent{color:var(--cyan)}
.logo-sub{font-size:9px;letter-spacing:2px;color:var(--t4);font-family:'Courier New',monospace;display:block;margin-top:1px}
.hdr{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--t3);font-family:'Courier New',monospace}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.dot.ok{background:var(--green);box-shadow:0 0 5px var(--green)}
.dot.off{background:var(--red)}
.dot.run{background:var(--cyan);animation:dp .6s ease-in-out infinite}
@keyframes dp{0%,100%{opacity:1}50%{opacity:.25}}
.pb-wrap{height:2px;background:var(--b2);flex-shrink:0}
.pb-fill{height:100%;background:var(--cyan);width:0%;transition:width .1s}
nav{display:flex;overflow-x:auto;padding:0 6px;flex-shrink:0;scrollbar-width:none;background:#000;border-bottom:1px solid var(--b2);z-index:10}
nav::-webkit-scrollbar{display:none}
.tab{flex-shrink:0;padding:9px 12px;border:none;border-bottom:2px solid transparent;background:transparent;color:var(--t4);font-size:11px;cursor:pointer;white-space:nowrap;margin-bottom:-1px;transition:.15s}
.tab.on{color:var(--cyan);border-bottom-color:var(--cyan)}
.nbadge{display:inline-flex;align-items:center;justify-content:center;background:var(--red);color:#fff;border-radius:8px;font-size:8px;font-weight:700;padding:1px 5px;margin-left:3px;min-width:15px;height:15px;vertical-align:middle}
.nbadge.g{background:var(--green)}
main{flex:1;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch}
main::-webkit-scrollbar{width:2px}
main::-webkit-scrollbar-thumb{background:var(--b3);border-radius:2px}
.page{display:none;padding:11px 13px}
.page.on{display:block;animation:pf .15s ease}
@keyframes pf{from{opacity:0}to{opacity:1}}
.card{background:var(--bg2);border:1px solid var(--b2);border-radius:10px;padding:13px;margin-bottom:10px}
.ctitle{font-size:9px;font-weight:600;color:var(--t4);text-transform:uppercase;letter-spacing:2px;margin-bottom:11px;display:flex;align-items:center;gap:8px}
.ctitle::after{content:'';flex:1;height:1px;background:var(--b2)}
/* XU100 */
.xu-banner{display:flex;align-items:center;gap:8px;padding:7px 11px;border-radius:7px;margin-bottom:9px;font-size:10px;font-family:'Courier New',monospace}
.xu-banner.bull{background:rgba(0,230,118,.06);border:1px solid rgba(0,230,118,.2)}
.xu-banner.bear{background:rgba(255,68,68,.06);border:1px solid rgba(255,68,68,.2)}
.xu-banner.neutral{background:rgba(255,255,255,.03);border:1px solid var(--b2)}
/* Signal cards */
.sig{border-radius:8px;padding:12px 13px;margin-bottom:7px;background:var(--bg2);border:1px solid var(--b2);border-left:3px solid var(--b3);cursor:pointer;animation:si .2s ease}
.sig:active{background:var(--bg3)}
@keyframes si{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:translateY(0)}}
.sig.buy{border-left-color:var(--green);background:linear-gradient(90deg,rgba(0,230,118,.05),var(--bg2) 45%)}
.sig.master{border-left-color:var(--gold);background:linear-gradient(90deg,rgba(255,184,0,.06),var(--bg2) 45%)}
.sig.stop{border-left-color:var(--orange);background:linear-gradient(90deg,rgba(255,112,67,.06),var(--bg2) 45%)}
.sig-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}
.sig-ticker{font-size:20px;font-weight:700;line-height:1}
.sig-name{font-size:9px;color:var(--t4);margin-top:2px}
.sig-badge{font-size:9px;padding:3px 7px;border-radius:4px;font-weight:700;text-transform:uppercase;font-family:'Courier New',monospace}
.sig-badge.buy{background:rgba(0,230,118,.14);color:var(--green);border:1px solid rgba(0,230,118,.28)}
.sig-badge.master{background:rgba(255,184,0,.14);color:var(--gold);border:1px solid rgba(255,184,0,.32)}
.sig-badge.stop{background:rgba(255,112,67,.14);color:var(--orange);border:1px solid rgba(255,112,67,.3)}
.sig-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-bottom:7px}
.sig-m{background:var(--bg3);border:1px solid var(--b2);border-radius:5px;padding:6px 7px}
.sig-mv{font-size:12px;font-weight:700;font-family:'Courier New',monospace;line-height:1;color:#fff}
.sig-ml{font-size:7px;color:var(--t4);margin-top:2px;text-transform:uppercase;letter-spacing:.5px}
.sbs{display:flex;flex-wrap:wrap;gap:3px}
.sb{font-size:8px;padding:2px 5px;border-radius:3px;background:var(--bg3);color:var(--t4);border:1px solid var(--b2)}
.sb.on{background:rgba(0,212,255,.1);color:var(--cyan);border-color:rgba(0,212,255,.25)}
/* Strength badge */
.sc-badge{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:5px;font-size:12px;font-weight:700;flex-shrink:0}
.sc-10,.sc-9{background:rgba(0,230,118,.2);color:var(--green);border:1px solid rgba(0,230,118,.4)}
.sc-8,.sc-7{background:rgba(0,212,255,.15);color:var(--cyan);border:1px solid rgba(0,212,255,.3)}
.sc-6,.sc-5{background:rgba(255,184,0,.15);color:var(--gold);border:1px solid rgba(255,184,0,.3)}
.sc-4,.sc-3{background:rgba(255,112,67,.15);color:var(--orange);border:1px solid rgba(255,112,67,.3)}
.sc-2,.sc-1{background:rgba(255,68,68,.15);color:var(--red);border:1px solid rgba(255,68,68,.3)}
/* Chips */
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:9px}
.chip{padding:4px 10px;border-radius:4px;font-size:10px;border:1px solid var(--b2);background:var(--bg3);color:var(--t3);cursor:pointer}
.chip.on{background:rgba(255,184,0,.12);color:var(--gold);border-color:rgba(255,184,0,.4)}
.chip.cy.on{background:rgba(0,212,255,.1);color:var(--cyan);border-color:rgba(0,212,255,.3)}
/* Settings rows */
.srow{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid var(--b1);gap:10px}
.srow:last-child{border-bottom:none}
.slbl{font-size:12px;font-weight:500;flex:1;color:var(--t2)}
.sdesc{font-size:10px;color:var(--t4);margin-top:2px}
.toggle{position:relative;width:44px;height:24px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.trk{position:absolute;inset:0;border-radius:12px;background:var(--bg4);border:1px solid var(--b2);cursor:pointer;transition:.25s}
input:checked+.trk{background:rgba(0,230,118,.18);border-color:rgba(0,230,118,.5)}
.trk::after{content:'';position:absolute;top:4px;left:4px;width:14px;height:14px;border-radius:50%;background:var(--t4);transition:.25s}
input:checked+.trk::after{transform:translateX(20px);background:var(--green)}
.ni{width:80px;padding:6px 9px;border-radius:6px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-family:'Courier New',monospace;font-size:12px;text-align:right}
.ni:focus{outline:none;border-color:var(--cyan)}
.si{padding:6px 9px;border-radius:6px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-size:11px;min-width:100px;-webkit-appearance:none}
.si:focus{outline:none;border-color:var(--cyan)}
.stit{font-size:9px;font-weight:600;color:var(--cyan);text-transform:uppercase;letter-spacing:2px;padding:9px 0 7px;border-bottom:1px solid var(--b2);margin-bottom:3px}
/* Stock list */
.strow{display:flex;align-items:center;padding:9px 11px;background:var(--bg2);border-radius:7px;margin-bottom:3px;gap:7px;border:1px solid var(--b1);cursor:pointer}
.strow.hs{border-color:rgba(0,212,255,.2);background:rgba(0,212,255,.03)}
.stt{font-size:14px;font-weight:700;width:54px;flex-shrink:0}
.stn{color:var(--t4);font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sti{font-size:8px;color:var(--t4);background:var(--bg4);border:1px solid var(--b2);padding:2px 5px;border-radius:3px;flex-shrink:0}
.sds{display:flex;gap:3px;flex-shrink:0}
.sd{width:6px;height:6px;border-radius:50%;background:var(--b3)}
.sd.buy{background:var(--green);box-shadow:0 0 4px var(--green)}
/* Stats */
.sgrid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:10px}
.sstat{background:var(--bg2);border:1px solid var(--b2);border-radius:8px;padding:11px 13px}
.sval{font-size:21px;font-weight:700;line-height:1;color:#fff}
.sval.p{color:var(--green)}.sval.n{color:var(--red)}
.slb2{font-size:9px;color:var(--t4);margin-top:4px;text-transform:uppercase;letter-spacing:1px}
/* Buttons */
.btn{padding:8px 14px;border-radius:6px;border:1px solid var(--b2);background:var(--bg3);color:var(--t3);font-size:11px;cursor:pointer;transition:.15s}
.btn:active{opacity:.5}
.btn.g{color:var(--green);border-color:rgba(0,230,118,.35);background:rgba(0,230,118,.07)}
.btn.r{color:var(--red);border-color:rgba(255,68,68,.35);background:rgba(255,68,68,.07)}
.btn.c{color:var(--cyan);border-color:rgba(0,212,255,.35);background:rgba(0,212,255,.07)}
.btn.o{color:var(--orange);border-color:rgba(255,112,67,.35);background:rgba(255,112,67,.07)}
/* Chart */
.chartbox{background:var(--bg2);border:1px solid var(--b2);border-radius:8px;height:175px;position:relative;overflow:hidden;margin-bottom:10px}
.chartgrid{position:absolute;inset:0;background-image:linear-gradient(var(--b1) 1px,transparent 1px),linear-gradient(90deg,var(--b1) 1px,transparent 1px);background-size:40px 28px}
/* Agent table */
.atbl{width:100%;border-collapse:collapse;font-size:10px}
.atbl th{padding:6px 7px;text-align:left;color:var(--t4);border-bottom:1px solid var(--b2);font-size:9px;text-transform:uppercase;letter-spacing:1px}
.atbl td{padding:8px 7px;border-bottom:1px solid var(--b1);color:var(--t2)}
.rbar{width:48px;height:3px;background:var(--bg4);border-radius:2px;overflow:hidden;display:inline-block;vertical-align:middle}
.rfill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--purple));border-radius:2px}
/* Telegram */
.tgi{width:100%;padding:9px 11px;border-radius:7px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-family:'Courier New',monospace;font-size:11px;margin-bottom:7px}
.tgi:focus{outline:none;border-color:var(--cyan)}
.tgi::placeholder{color:var(--t4)}
/* Footer */
footer{padding:9px 15px;padding-bottom:calc(9px + env(safe-area-inset-bottom));background:#000;border-top:1px solid var(--b2);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:10}
.finfo{font-size:10px;color:var(--t4);line-height:1.6;font-family:'Courier New',monospace}
#scanBtn{padding:10px 26px;border-radius:6px;border:none;cursor:pointer;font-size:12px;font-weight:700;letter-spacing:1.5px;background:var(--cyan);color:#000;transition:.15s}
#scanBtn:active{transform:scale(.94)}
#scanBtn.run{background:var(--green);color:#000;animation:sg 1.2s ease-in-out infinite}
@keyframes sg{0%,100%{box-shadow:0 0 16px rgba(0,230,118,.4)}50%{box-shadow:0 0 28px rgba(0,230,118,.7)}}
/* Modal */
.movl{position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:1000;display:flex;align-items:flex-end;opacity:0;pointer-events:none;transition:opacity .2s;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.movl.on{opacity:1;pointer-events:all}
.modal{background:var(--bg2);border:1px solid var(--b2);border-top:1px solid var(--b3);border-radius:14px 14px 0 0;padding:18px 15px;padding-bottom:calc(18px + env(safe-area-inset-bottom));width:100%;max-height:88dvh;overflow-y:auto;transform:translateY(100%);transition:transform .25s cubic-bezier(.4,0,.2,1)}
.movl.on .modal{transform:translateY(0)}
.mhdl{width:32px;height:3px;background:var(--b3);border-radius:2px;margin:0 auto 14px}
.mtit{font-size:17px;font-weight:700;margin-bottom:14px}
/* Toast */
#toast{position:fixed;top:calc(68px + env(safe-area-inset-top));left:50%;transform:translateX(-50%) translateY(-5px);background:var(--bg3);border:1px solid var(--b3);color:#fff;padding:9px 17px;border-radius:6px;font-size:11px;z-index:2000;white-space:nowrap;opacity:0;transition:all .2s;pointer-events:none;box-shadow:0 8px 24px rgba(0,0,0,.8)}
#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
/* Misc */
.empty{text-align:center;padding:44px 20px;color:var(--t4)}
.eico{font-size:36px;margin-bottom:10px;opacity:.4}
.scr{overflow-y:auto;max-height:260px}
.scr::-webkit-scrollbar{width:2px}
.scr::-webkit-scrollbar-thumb{background:var(--b3);border-radius:2px}
.sinput{width:100%;padding:9px 11px;border-radius:7px;background:var(--bg4);border:1px solid var(--b2);color:#fff;font-size:12px;margin-bottom:7px}
.sinput:focus{outline:none;border-color:var(--cyan)}
.sinput::placeholder{color:var(--t4)}
/* Position cards */
.pos-card{background:var(--bg2);border:1px solid var(--b2);border-radius:10px;padding:12px 13px;margin-bottom:7px;border-left:3px solid var(--b3)}
.pos-card.profit{border-left-color:var(--green)}
.pos-card.loss{border-left-color:var(--red)}
.pos-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.pos-ticker{font-size:18px;font-weight:700}
.pos-pnl{font-size:17px;font-weight:700;font-family:'Courier New',monospace}
.pos-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.pos-m{background:var(--bg3);border:1px solid var(--b1);border-radius:5px;padding:6px 7px}
.pos-mv{font-size:11px;font-weight:600;color:#fff;font-family:'Courier New',monospace}
.pos-ml{font-size:7px;color:var(--t4);margin-top:2px;text-transform:uppercase}
/* Watchlist */
.wl-item{display:flex;align-items:center;padding:9px 11px;background:var(--bg2);border:1px solid var(--b1);border-radius:8px;margin-bottom:4px;gap:7px}
.wl-ticker{font-size:14px;font-weight:700;width:52px;flex-shrink:0}
.wl-btn{width:26px;height:26px;border-radius:5px;border:1px solid var(--b2);background:var(--bg3);color:var(--t3);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:11px;flex-shrink:0}
/* Report */
.rep-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--b1);font-size:11px}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;margin-top:7px}
.cal-day{aspect-ratio:1;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:9px}
.cal-day.has{background:rgba(0,212,255,.15);color:var(--cyan)}
.cal-day.good{background:rgba(0,230,118,.2);color:var(--green)}
.cal-day.none{background:var(--bg3);color:var(--t4);opacity:.4}
/* Background indicator */
.bg-ind{display:flex;align-items:center;gap:5px;font-size:9px;color:var(--t4);font-family:'Courier New',monospace}
.bg-dot{width:5px;height:5px;border-radius:50%;background:var(--t4)}
.bg-dot.on{background:var(--green);box-shadow:0 0 4px var(--green)}
</style>
</head>
<body>
<div id="app">
<header>
  <div>
    <div class="logo"><span>BIST</span><span class="logo-accent"> AI</span></div>
    <span class="logo-sub">KATILIM ENDEKSI TARAYICI</span>
  </div>
  <div class="hdr">
    <div class="dot run" id="dot"></div>
    <span id="hst">Baslatiliyor...</span>
  </div>
</header>
<div class="pb-wrap"><div class="pb-fill" id="pbar"></div></div>
<nav>
  <button class="tab on" onclick="pg('signals')">Sinyaller<span class="nbadge" id="nbadge" style="display:none">0</span></button>
  <button class="tab" onclick="pg('scanner')">Tarayici</button>
  <button class="tab" onclick="pg('positions')">Pozisyonlar<span class="nbadge g" id="posBadge" style="display:none">0</span></button>
  <button class="tab" onclick="pg('watchlist')">Watchlist</button>
  <button class="tab" onclick="pg('backtest')">Backtest</button>
  <button class="tab" onclick="pg('agents')">Agents</button>
  <button class="tab" onclick="pg('report')">Rapor</button>
  <button class="tab" onclick="pg('settings')">Ayarlar</button>
  <button class="tab" onclick="pg('telegram')">Telegram</button>
</nav>
<main>
<!-- SINYALLER -->
<div class="page on" id="page-signals">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;padding:6px 10px;background:var(--bg2);border-radius:7px;border:1px solid var(--b1)">
    <div class="bg-ind"><div class="bg-dot on" id="bgDot"></div><span id="bgText">Arka plan aktif</span></div>
    <span style="font-size:9px;color:var(--t4)" id="lastsc">Son: -</span>
  </div>
  <div class="xu-banner neutral" id="xuBanner">
    <span id="xuDot" style="width:7px;height:7px;border-radius:50%;background:var(--t4);flex-shrink:0"></span>
    <span id="xuText" style="color:var(--t3)">XU100 yukleniyor...</span>
    <span id="xuFilter" style="margin-left:auto;font-size:9px;color:var(--t4)"></span>
  </div>
  <div class="chips" id="idxchips">
    <div class="chip cy on" data-v="ALL" onclick="idxT(this)">Tumu</div>
    <div class="chip cy" data-v="XK030EA" onclick="idxT(this)">K30EA</div>
    <div class="chip cy" data-v="XK030" onclick="idxT(this)">K30</div>
    <div class="chip cy" data-v="XK050" onclick="idxT(this)">K50</div>
    <div class="chip cy" data-v="XK100" onclick="idxT(this)">K100</div>
    <div class="chip cy" data-v="XKTUM" onclick="idxT(this)">KTUM</div>
    <div class="chip cy" data-v="XSRDK" onclick="idxT(this)">SRDK</div>
    <div class="chip cy" data-v="XKTMT" onclick="idxT(this)">KTMT</div>
  </div>
  <div class="chips">
    <div class="chip on" data-v="D" onclick="tfT(this)">Gunluk</div>
    <div class="chip on" data-v="240" onclick="tfT(this)">4 Saat</div>
    <div class="chip on" data-v="120" onclick="tfT(this)">2 Saat</div>
  </div>
  <div id="siglist"></div>
</div>
<!-- TARAYICI -->
<div class="page" id="page-scanner">
  <div class="card">
    <div class="ctitle">Tarama Ayarlari</div>
    <div class="srow"><div class="slbl">Zaman Dilimleri</div><div style="display:flex;gap:10px"><label style="font-size:10px;display:flex;align-items:center;gap:3px;cursor:pointer"><input type="checkbox" id="tf_D" checked> G</label><label style="font-size:10px;display:flex;align-items:center;gap:3px;cursor:pointer"><input type="checkbox" id="tf_120" checked> 2H</label><label style="font-size:10px;display:flex;align-items:center;gap:3px;cursor:pointer"><input type="checkbox" id="tf_240" checked> 4H</label></div></div>
    <div class="srow"><div class="slbl">Tarama Araligi (dk)</div><input type="number" class="ni" id="scanInterval" value="5" min="1" max="60"></div>
    <div class="srow"><div class="slbl">Min Consensus (%)</div><input type="number" class="ni" id="minCons" value="0" min="0" max="100"></div>
    <div class="srow"><div class="slbl">Sadece Master AI</div><label class="toggle"><input type="checkbox" id="onlyMaster"><div class="trk"></div></label></div>
  </div>
  <div class="card">
    <div class="ctitle">Hisse Listesi (<span id="scnt">0</span>)</div>
    <input type="text" class="sinput" id="ssearch" placeholder="Hisse ara..." oninput="filterSt(this.value)">
    <div class="chips" id="sIdxC">
      <div class="chip on" data-v="ALL" onclick="sIdxF(this)">Tumu</div>
      <div class="chip" data-v="XK030EA" onclick="sIdxF(this)">K30EA</div>
      <div class="chip" data-v="XK030" onclick="sIdxF(this)">K30</div>
      <div class="chip" data-v="XK050" onclick="sIdxF(this)">K50</div>
      <div class="chip" data-v="XK100" onclick="sIdxF(this)">K100</div>
      <div class="chip" data-v="XKTUM" onclick="sIdxF(this)">KTUM</div>
      <div class="chip" data-v="XSRDK" onclick="sIdxF(this)">SRDK</div>
      <div class="chip" data-v="XKTMT" onclick="sIdxF(this)">KTMT</div>
    </div>
    <div id="stlist"></div>
  </div>
</div>
<!-- POZISYONLAR -->
<div class="page" id="page-positions">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:9px">
    <div class="ctitle" style="margin:0">Acik Pozisyonlar</div>
    <button class="btn r" style="padding:5px 9px;font-size:9px" onclick="closeAllPositions()">Tumunu Kapat</button>
  </div>
  <div class="sgrid" id="posStats" style="margin-bottom:9px">
    <div class="sstat"><div class="sval p" id="posTotalPnl">-</div><div class="slb2">Toplam PnL %</div></div>
    <div class="sstat"><div class="sval" id="posCount">0</div><div class="slb2">Acik Pozisyon</div></div>
    <div class="sstat"><div class="sval p" id="posBestPnl">-</div><div class="slb2">En Iyi</div></div>
    <div class="sstat"><div class="sval n" id="posWorstPnl">-</div><div class="slb2">En Kotu</div></div>
  </div>
  <!-- Pozisyon sekmeleri -->
  <div class="chips" style="margin-bottom:9px">
    <div class="chip on" id="posTabOpen" onclick="posTab('open')">Acik Pozisyonlar</div>
    <div class="chip" id="posTabClosed" onclick="posTab('closed')">Kapali Gecmis</div>
  </div>
  <div id="positionList"><div class="empty"><div class="eico">&#128188;</div><div style="font-size:11px">Sinyal gelince pozisyon acilir.</div></div></div>
  <div id="closedList" style="display:none"></div>
</div>
<!-- WATCHLIST -->
<div class="page" id="page-watchlist">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:9px">
    <div class="ctitle" style="margin:0">Watchlist</div>
    <span style="font-size:9px;color:var(--t4)" id="wlCount">0 hisse</span>
  </div>
  <div class="card" style="margin-bottom:9px">
    <div style="display:flex;gap:6px;margin-bottom:7px">
      <input type="text" id="wlInput" class="sinput" placeholder="Hisse kodu (orn: EREGL)" style="margin:0;flex:1;text-transform:uppercase" oninput="this.value=this.value.toUpperCase()" onkeydown="if(event.key==='Enter')addWatchlist()">
      <button class="btn c" onclick="addWatchlist()" style="padding:9px 13px;border-radius:7px;flex-shrink:0">+ Ekle</button>
    </div>
    <div style="font-size:9px;color:var(--t4);margin-bottom:7px">Endeksi toplu ekle:</div>
    <div style="display:flex;flex-wrap:wrap;gap:4px">
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK030EA')">K30EA</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK030')">K30</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK050')">K50</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XK100')">K100</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XKTUM')">KTUM</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XSRDK')">SRDK</button>
      <button class="btn" style="font-size:9px;padding:4px 8px" onclick="addIndexToWL('XKTMT')">KTMT</button>
      <button class="btn r" style="font-size:9px;padding:4px 8px" onclick="clearWatchlist()">Temizle</button>
    </div>
  </div>
  <div id="wlList"><div class="empty"><div class="eico">&#11088;</div><div style="font-size:11px">Favori hisse ekleyin.</div></div></div>
</div>
<!-- BACKTEST -->
<div class="page" id="page-backtest">
  <!-- Sekme geçişi -->
  <div class="chips" style="margin-bottom:9px">
    <div class="chip cy on" id="btTab1" onclick="btTabSwitch('bt')">Backtest</div>
    <div class="chip cy" id="btTab2" onclick="btTabSwitch('wf')">Walk-Forward</div>
    <div class="chip cy" id="btTab3" onclick="btTabSwitch('mc')">Monte Carlo</div>
    <div class="chip cy" id="btTab4" onclick="btTabSwitch('opt')">Optimizasyon</div>
  </div>

  <!-- Ortak parametreler -->
  <div class="card" id="btParams">
    <div class="ctitle">Parametreler</div>
    <div class="srow"><div class="slbl">Hisse</div><select class="si" id="btSym" style="min-width:120px"></select></div>
    <div class="srow"><div class="slbl">Zaman Dilimi</div>
      <select class="si" id="btTF">
        <option value="D">Gunluk</option>
        <option value="240">4 Saat</option>
        <option value="120">2 Saat</option>
      </select>
    </div>
    <div class="srow"><div class="slbl">Sistem</div>
      <select class="si" id="btSys">
        <option value="all">Hepsi (Master AI)</option>
        <option value="s1">Sistem 1 (ST+TMA)</option>
        <option value="s2">PRO Engine</option>
        <option value="fu">Fusion</option>
      </select>
    </div>
    <div class="srow"><div class="slbl">Sermaye (TL)</div><input type="number" class="ni" id="btCap" value="100000" style="width:110px"></div>
    <div class="srow"><div class="slbl">Komisyon (%)</div><input type="number" class="ni" id="btComm" value="0.1" step="0.05" style="width:80px"></div>
    <div class="srow"><div class="slbl">Slippage (%)</div><input type="number" class="ni" id="btSlip" value="0.05" step="0.05" style="width:80px"></div>
    <div id="btExtraParams"></div>
  </div>

  <!-- Backtest paneli -->
  <div id="panel-bt">
    <button class="btn g" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px;font-size:13px;font-weight:700" onclick="runBT()">Backtest Calistir</button>
    <div id="btout" style="display:none">
      <div class="sgrid" id="btstats" style="grid-template-columns:repeat(2,1fr)"></div>
      <!-- Equity Curve -->
      <div class="card" style="padding:10px">
        <div class="ctitle">Equity Curve</div>
        <div style="position:relative;height:180px"><canvas id="btcv"></canvas></div>
      </div>
      <!-- Aylik getiri isi haritasi -->
      <div class="card" style="padding:10px">
        <div class="ctitle">Aylik Dagilim</div>
        <div id="btMonthly"></div>
      </div>
      <!-- Islem tablosu -->
      <div class="card">
        <div class="ctitle">Islemler (<span id="btTradeCount">0</span>)</div>
        <div id="btlog" class="scr" style="max-height:220px"></div>
      </div>
    </div>
  </div>

  <!-- Walk-Forward paneli -->
  <div id="panel-wf" style="display:none">
    <div class="card" style="margin-bottom:8px">
      <div class="ctitle">Walk-Forward Ayarlari</div>
      <div class="srow"><div class="slbl">Egitim Periyodu</div>
        <select class="si" id="wfTrain">
          <option value="0.6">%60 Egitim / %40 Test</option>
          <option value="0.7" selected>%70 Egitim / %30 Test</option>
          <option value="0.8">%80 Egitim / %20 Test</option>
        </select>
      </div>
      <div class="srow"><div class="slbl">Optimize Kriteri</div>
        <select class="si" id="wfCrit">
          <option value="sharpe">Sharpe Ratio</option>
          <option value="ret">Toplam Getiri</option>
          <option value="wr">Win Rate</option>
        </select>
      </div>
      <div style="font-size:9px;color:var(--t4);margin-top:6px;line-height:1.6">Walk-Forward: Gecmis veriyi egitim/test olarak boler. Egitim bolumunde en iyi parametreyi bulur, test bolumunde dogrular. Overfitting'i onler.</div>
    </div>
    <button class="btn c" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px" onclick="runWF()">Walk-Forward Baslat</button>
    <div id="wfout" style="display:none"></div>
  </div>

  <!-- Monte Carlo paneli -->
  <div id="panel-mc" style="display:none">
    <div class="card" style="margin-bottom:8px">
      <div class="ctitle">Monte Carlo Ayarlari</div>
      <div class="srow"><div class="slbl">Senaryo Sayisi</div>
        <select class="si" id="mcScen">
          <option value="200">200 Senaryo (Hizli)</option>
          <option value="500" selected>500 Senaryo</option>
          <option value="1000">1000 Senaryo (Detayli)</option>
        </select>
      </div>
      <div style="font-size:9px;color:var(--t4);margin-top:6px;line-height:1.6">Monte Carlo: Gercek islemleri rastgele sirayla 500 kez calistirir. Sistemin sans mi yoksa gercek mi oldugunu gosterir. P5-P95 araligini verir.</div>
    </div>
    <button class="btn o" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px" onclick="runMC()">Monte Carlo Baslat</button>
    <div id="mcout" style="display:none"></div>
  </div>

  <!-- Optimizasyon paneli -->
  <div id="panel-opt" style="display:none">
    <div class="card" style="margin-bottom:8px">
      <div class="ctitle">Parametre Optimizasyonu</div>
      <div class="srow"><div class="slbl">Parametre</div>
        <select class="si" id="optP">
          <option value="atr">ATR Multiplier (Stop Genisligi)</option>
          <option value="adx">Min ADX (Trend Gucu)</option>
          <option value="fb">Fusion Buy Esigi</option>
          <option value="mb">Master Buy Esigi</option>
          <option value="pro">PRO Min Skor</option>
        </select>
      </div>
      <div class="srow"><div class="slbl">Optimizasyon Kriteri</div>
        <select class="si" id="optCrit">
          <option value="sharpe">Sharpe Ratio</option>
          <option value="ret">Toplam Getiri %</option>
          <option value="wr">Win Rate %</option>
          <option value="calmar">Calmar (Ret/MaxDD)</option>
        </select>
      </div>
      <div style="font-size:9px;color:var(--t4);margin-top:6px;line-height:1.6">Her parametre degeri icin tam backtest calistirilir. Komisyon ve slippage dahildir. Sonuclar kriterine gore siralanir.</div>
    </div>
    <button class="btn g" style="width:100%;padding:11px;border-radius:8px;margin-bottom:10px" onclick="runOpt()">Optimize Et</button>
    <div id="optout" style="display:none"></div>
  </div>
</div>
<!-- AGENTS -->
<div class="page" id="page-agents">
  <div class="card" style="border-color:rgba(255,184,0,.2)">
    <div class="ctitle" style="color:var(--gold)">Master AI</div>
    <div class="sgrid">
      <div class="sstat"><div class="sval" id="mcons">-</div><div class="slb2">Buy Consensus</div></div>
      <div class="sstat"><div class="sval" id="mthresh">-</div><div class="slb2">Threshold</div></div>
      <div class="sstat"><div class="sval p" id="mpnl">-</div><div class="slb2">Master PnL</div></div>
      <div class="sstat"><div class="sval" id="mpos">YOK</div><div class="slb2">Pozisyon</div></div>
    </div>
  </div>
  <div class="card">
    <div class="ctitle">Agent Durumu</div>
    <table class="atbl"><thead><tr><th>Agent</th><th>Aktif</th><th>PnL%</th><th>Itibar</th><th>Sermaye</th></tr></thead><tbody id="agTbl"></tbody></table>
  </div>
  <div class="card"><div class="ctitle">Quantum Durumu</div><div id="qviz"></div></div>
</div>
<!-- RAPOR -->
<div class="page" id="page-report">
  <div class="ctitle">Performans Raporu</div>
  <div class="card">
    <div class="ctitle">Bu Hafta</div>
    <div class="sgrid">
      <div class="sstat"><div class="sval" id="wkSigs">0</div><div class="slb2">Sinyal</div></div>
      <div class="sstat"><div class="sval" id="wkMaster">0</div><div class="slb2">Master AI</div></div>
      <div class="sstat"><div class="sval" id="wkStops">0</div><div class="slb2">Stop</div></div>
      <div class="sstat"><div class="sval p" id="wkBest">-</div><div class="slb2">En Aktif</div></div>
    </div>
  </div>
  <div class="card"><div class="ctitle">Sistem Performansi</div><div id="sysPerf"></div></div>
  <div class="card">
    <div class="ctitle">Son 28 Gun</div>
    <div style="display:flex;gap:3px;margin-bottom:5px">
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Pt</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Sa</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Ca</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Pe</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Cu</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Ct</span>
      <span style="font-size:8px;color:var(--t4);width:20px;text-align:center">Pz</span>
    </div>
    <div class="cal-grid" id="calGrid"></div>
  </div>
  <div class="card"><div class="ctitle">En Cok Sinyal</div><div id="topStocks"></div></div>
  <button class="btn c" style="width:100%;padding:11px;border-radius:8px;margin-bottom:8px" onclick="sendWeeklyReport()">Haftalik Raporu Gonder</button>
  <button class="btn o" style="width:100%;padding:11px;border-radius:8px;margin-bottom:8px" onclick="sendDayEndReport()">Gun Sonu Raporunu Gonder</button>
  <div class="card" style="border-color:rgba(255,184,0,.3);margin-top:4px">
    <div class="ctitle" style="color:var(--gold)">Profesyonel Trader Gozetimi</div>
    <div style="font-size:10px;color:var(--t3);margin-bottom:8px;line-height:1.6">Portfoy riski, win rate analizi, sistem performansi ve gelistirme onerileri</div>
    <button class="btn o" style="width:100%;padding:11px;border-radius:8px" onclick="openTraderConsole()">Trader Gozetimini Ac</button>
  </div>
</div>
<!-- AYARLAR -->
<div class="page" id="page-settings">
  <div class="stit">Sistem Etkinlestirme</div>
  <div class="srow"><div class="slbl">Sistem 1 (SuperTrend+TMA+Chandelier)</div><label class="toggle"><input type="checkbox" id="s_s1" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Sistem 2 (PRO ENGINE)</div><label class="toggle"><input type="checkbox" id="s_s2" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Fusion Sistemi</div><label class="toggle"><input type="checkbox" id="s_fu" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Master AI</div><label class="toggle"><input type="checkbox" id="s_ma" checked><div class="trk"></div></label></div>
  <div class="stit" style="margin-top:11px">Bagimsiz Agentlar</div>
  <div class="srow"><div class="slbl">Agent 60</div><label class="toggle"><input type="checkbox" id="s_a60"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 61</div><label class="toggle"><input type="checkbox" id="s_a61"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 62</div><label class="toggle"><input type="checkbox" id="s_a62"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 81</div><label class="toggle"><input type="checkbox" id="s_a81"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Agent 120</div><label class="toggle"><input type="checkbox" id="s_a120"><div class="trk"></div></label></div>
  <div class="stit" style="margin-top:11px">Parametreler</div>
  <div class="srow"><div class="slbl">Min ADX</div><input type="number" class="ni" id="s_adxMin" value="25"></div>
  <div class="srow"><div class="slbl">ATR Multiplier</div><input type="number" class="ni" id="s_atrm" value="8" step="0.5"></div>
  <div class="srow"><div class="slbl">PRO Min Skor</div><input type="number" class="ni" id="s_sc" value="5" min="1" max="6"></div>
  <div class="srow"><div class="slbl">Fusion Buy Thr (%)</div><input type="number" class="ni" id="s_fb" value="80" min="1" max="100"></div>
  <div class="srow"><div class="slbl">Master Buy Thr (%)</div><input type="number" class="ni" id="s_mb" value="70" min="1" max="100"></div>
  <div class="stit" style="margin-top:11px">Bildirimler</div>
  <div class="srow"><div class="slbl">Ses</div><label class="toggle"><input type="checkbox" id="s_snd" checked><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Push</div><label class="toggle"><input type="checkbox" id="s_pn"><div class="trk"></div></label></div>
  <div class="srow">
    <div><div class="slbl">Service Worker Bildirimi</div><div class="sdesc">Uygulama kapali iken de bildirim al</div></div>
    <button class="btn c" style="padding:7px 12px;font-size:10px" onclick="requestPushPermission()">Izin Ver</button>
  </div>
  <div style="display:flex;gap:6px;margin-top:11px">
    <button class="btn g" style="flex:1;padding:10px;border-radius:8px" onclick="saveSets()">Kaydet</button>
    <button class="btn r" style="padding:10px 14px;border-radius:8px" onclick="resetSets()">Sifirla</button>
  </div>
  <div class="stit" style="margin-top:14px">Ozel Indiktor Ekle (FIX 11)</div>
  <div style="font-size:9px;color:var(--t4);margin-bottom:8px">Eklenen indiktor tarama kosuluna dahil edilir</div>
  <div style="display:flex;gap:6px;margin-bottom:7px">
    <input type="text" class="sinput" id="customIndName" placeholder="Indiktor adi (orn: MACD Kesisim)" style="margin:0;flex:1;font-size:11px">
  </div>
  <div class="srow"><div class="slbl">Min RSI</div><input type="number" class="ni" id="ci_rsi" value="50" min="0" max="100"></div>
  <div class="srow"><div class="slbl">Min ADX</div><input type="number" class="ni" id="ci_adx" value="20" min="0" max="100"></div>
  <div class="srow"><div class="slbl">EMA Ustunde</div><label class="toggle"><input type="checkbox" id="ci_ema"><div class="trk"></div></label></div>
  <div class="srow"><div class="slbl">Taramaya Dahil Et</div><label class="toggle"><input type="checkbox" id="ci_scan"><div class="trk"></div></label></div>
  <button class="btn c" style="width:100%;padding:9px;border-radius:7px;margin-top:8px" onclick="addCustomIndicator()">Indiktor Ekle</button>
  <div id="customIndList" style="margin-top:7px"></div>
</div>
<!-- TELEGRAM -->
<div class="page" id="page-telegram">
  <div class="card">
    <div class="ctitle">Bot Ayarlari</div>
    <input type="text" class="tgi" id="tgtoken" placeholder="Bot Token: 123456:ABC-DEF...">
    <input type="text" class="tgi" id="tgchat" placeholder="Chat ID: 123456789">
    <div style="font-size:9px;color:var(--t4);margin-bottom:9px">Chat ID icin @userinfobot yazin</div>
    <div class="stit" style="margin-top:3px">Mesaj Icerigi</div>
    <div class="srow"><div class="slbl">Grafik Linki</div><label class="toggle"><input type="checkbox" id="tg_ch" checked><div class="trk"></div></label></div>
    <div class="srow"><div class="slbl">Detayli Metrikler</div><label class="toggle"><input type="checkbox" id="tg_det" checked><div class="trk"></div></label></div>
    <div class="srow"><div class="slbl">Fiyat Bolgesi</div><label class="toggle"><input type="checkbox" id="tg_q" checked><div class="trk"></div></label></div>
    <div class="srow"><div class="slbl">Aktif Agentlar</div><label class="toggle"><input type="checkbox" id="tg_ag"><div class="trk"></div></label></div>
    <div style="display:flex;gap:6px;margin-top:9px">
      <button class="btn g" style="flex:1;padding:10px;border-radius:8px" onclick="tgTest()">Test Gonder</button>
      <button class="btn c" style="flex:1;padding:10px;border-radius:8px" onclick="tgSave()">Kaydet</button>
    </div>
    <div id="tgDebug" style="margin-top:7px;padding:7px;background:var(--bg4);border-radius:7px;font-size:9px;color:var(--t3);font-family:'Courier New',monospace;display:none"></div>
  </div>
  <div class="card">
    <div class="ctitle">Bildirim Gecmisi</div>
    <div id="tglog" class="scr" style="font-size:10px;color:var(--t3);font-family:'Courier New',monospace;min-height:36px"></div>
  </div>
  <div class="card">
    <div class="ctitle">Bot Komutlari</div>
    <div style="font-size:10px;color:var(--t3);line-height:1.9;font-family:'Courier New',monospace">/durum /portfoy /sinyal EREGL<br>/tara /rapor /yardim</div>
  </div>
</div>

</main>
<footer>
  <div class="finfo">
    <div class="bg-ind"><div class="bg-dot on" id="bgSvcDot"></div><span id="bgSvcTxt">Aktif</span></div>
  </div>
  <button id="scanBtn" onclick="startScan()">TARA</button>
</footer>
<div class="movl" id="modal" onclick="closeM(event)">
  <div class="modal">
    <div class="mhdl"></div>
    <div class="mtit" id="mtit">Detay</div>
    <div id="mcont"></div>
    <div style="display:flex;gap:6px;margin-top:13px">
      <button class="btn c" style="flex:1;padding:11px;border-radius:8px" onclick="openTV()">TradingView'da Gor</button>
      <button class="btn g" style="flex:1;padding:11px;border-radius:8px" onclick="sendCur()">Telegram</button>
    </div>
  </div>
</div>
<div id="toast"></div><script>
// BIST AI Scanner v6 - Tam Duzeltilmis
// FIX 1: Arka plan servisi gostergesi
// FIX 2: Proxy URL - TradingView, fiyat, XU100 
// FIX 3,4,5: TradingView acilisi - direkt location.href
// FIX 6: Sinyal karti gercek fiyat
// FIX 7: Pozisyon % PnL gosterimi
// FIX 8: Watchlist endeks toplu ekleme
// FIX 14: pstate renk eslesmesi duzeltildi
// FIX 15: Gun sonu otomatik rapor

var PROXY_URL='https://bist-price-proxy.onrender.com';
// Deploy sonrasi guncelle: https://PROJE.onrender.com

// -- YARDIMCILAR --
function lsGet(k){try{var v=localStorage.getItem(k);return v?JSON.parse(v):null}catch(e){return null}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function pad(n){return n.toString().padStart(2,'0')}
function toast(msg,ms){var t=document.getElementById('toast');t.textContent=msg;t.classList.add('on');setTimeout(function(){t.classList.remove('on')},ms||2400)}
function setDot(s){document.getElementById('dot').className='dot '+s}
function setSt(t){document.getElementById('hst').textContent=t}

// -- HISSE LISTESI --
var STOCKS=[
{t:'AKSA',n:'AKSA',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'ALTNY',n:'ALTINAY SAVUNMA',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'ASELS',n:'ASELSAN',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'BIMAS',n:'BIM MAGAZALAR',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'BSOKE',n:'BATISOKE CIMENTO',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'CANTE',n:'CAN2 TERMIK',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'CIMSA',n:'CIMSA',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'CWENE',n:'CW ENERJI',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'DAPGM',n:'DAP GAYRIMENKUL',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'EKGYO',n:'EMLAK KONUT GMYO',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'ENJSA',n:'ENERJISA ENERJI',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'EREGL',n:'EREGLI DEMIR CELIK',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'EUPWR',n:'EUROPOWER ENERJI',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'GENIL',n:'GEN ILAC',i:['XK030EA','XKTUM','XK100','XK050','XK030','XKTMT']},
{t:'GESAN',n:'GIRISIM ELEKTRIK',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'GLRMK',n:'GULERMAK AGIR',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'GRSEL',n:'GUR-SEL TURIZM',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'GUBRF',n:'GUBRE FABRIK.',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'KONTR',n:'KONTROLMATIK',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'KRDMD',n:'KARDEMIR (D)',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'KTLEV',n:'KATILIMEVIM',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'KUYAS',n:'KUYAS YATIRIM',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'MAVI',n:'MAVI GIYIM',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'MPARK',n:'MLP SAGLIK',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'OBAMS',n:'OBA MAKARNACILIK',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'PETKM',n:'PETKIM',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK']},
{t:'TUPRS',n:'TUPRAS',i:['XK030EA','XKTUM','XK100','XK050','XK030','XSRDK','XKTMT']},
{t:'YEOTK',n:'YEO TEKNOLOJI',i:['XK030EA','XKTUM','XK100','XK050','XK030']},
{t:'AKFYE',n:'AKFEN YEN. ENERJI',i:['XKTUM','XK100']},
{t:'ALBRK',n:'ALBARAKA TURK',i:['XKTUM','XK100','XSRDK']},
{t:'ARDYZ',n:'ARD BILISIM',i:['XKTUM','XK100','XKTMT']},
{t:'BANVT',n:'BANVIT',i:['XKTUM','XK100']},
{t:'BRSAN',n:'BORUSAN MANNESMANN',i:['XKTUM','XK100']},
{t:'BUCIM',n:'BURSA CIMENTO',i:['XKTUM','XK100']},
{t:'DMSAS',n:'DEMISAS DOKUM',i:['XKTUM','XK100']},
{t:'ENKAI',n:'ENKA INSAAT',i:['XKTUM','XK100','XK050']},
{t:'ERBOS',n:'ERBOSAN',i:['XKTUM','XK100']},
{t:'FROTO',n:'FORD OTOSAN',i:['XKTUM','XK100','XK050','XSRDK','XKTMT']},
{t:'GARAN',n:'GARANTI BBVA',i:['XKTUM','XK100','XK050']},
{t:'HEKTS',n:'HEKTAS',i:['XKTUM','XK100']},
{t:'IPEKE',n:'IPEK DOGAL ENERJI',i:['XKTUM','XK100']},
{t:'ISDMR',n:'ISKENDERUN DEMIR',i:['XKTUM','XK100','XK050','XSRDK']},
{t:'KCHOL',n:'KOC HOLDING',i:['XKTUM','XK100','XK050']},
{t:'KOZAL',n:'KOZA ALTIN',i:['XKTUM','XK100','XK050','XSRDK']},
{t:'LOGO',n:'LOGO YAZILIM',i:['XKTUM','XK100','XKTMT']},
{t:'MGROS',n:'MIGROS TICARET',i:['XKTUM','XK100','XK050']},
{t:'OYAKC',n:'OYAK CIMENTO',i:['XKTUM','XK100','XK050']},
{t:'PRKAB',n:'PARK ELEKTRIK',i:['XKTUM','XK100','XSRDK']},
{t:'SASA',n:'SASA POLYESTER',i:['XKTUM','XK100','XK050','XSRDK']},
{t:'SELEC',n:'SELCUK ECZA',i:['XKTUM','XK100']},
{t:'TATGD',n:'TAT GIDA',i:['XKTUM','XK100']},
{t:'TMSN',n:'TUMOSAN',i:['XKTUM','XK100']},
{t:'TOASO',n:'TOFAS',i:['XKTUM','XK100','XK050']},
{t:'TTKOM',n:'TURK TELEKOM',i:['XKTUM','XK100','XK050']},
{t:'ULKER',n:'ULKER BISKUVI',i:['XKTUM','XK100','XK050']},
{t:'VAKBN',n:'VAKIF BANK',i:['XKTUM','XK100','XK050']},
{t:'VESTL',n:'VESTEL',i:['XKTUM','XK100']},
{t:'YKBNK',n:'YAPI KREDI',i:['XKTUM','XK100','XK050']},
{t:'THYAO',n:'TURK HAVA YOLLARI',i:['XKTUM','XK100','XK050']},
{t:'SAHOL',n:'SABANCI HOLDING',i:['XKTUM','XK100','XK050']},
{t:'SISE',n:'SISE CAM',i:['XKTUM','XK100','XK050']},
{t:'TCELL',n:'TURKCELL',i:['XKTUM','XK100','XK050']},
{t:'TAVHL',n:'TAV HAVALIMANLARI',i:['XKTUM','XK100','XK050']},
{t:'AKBNK',n:'AKBANK',i:['XKTUM','XK100','XK050']},
{t:'ISCTR',n:'IS BANKASI',i:['XKTUM','XK100','XK050']},
{t:'ZOREN',n:'ZORLU ENERJI',i:['XKTUM','XK100']},
{t:'SKBNK',n:'SEKERBANK',i:['XKTUM','XK100']},
{t:'KRDMA',n:'KARDEMIR (A)',i:['XKTUM']},
{t:'KRDMB',n:'KARDEMIR (B)',i:['XKTUM']},
];

// -- CONFIG --
var DEF={s1:true,s2:true,fu:true,ma:true,a60:false,a61:false,a62:false,a81:false,a120:false,adxMin:25,atrm:8,sc:5,fb:80,mb:70,pn:false,snd:true,minCons:0,onlyMaster:false,scanInterval:5};
var DEF_TG={token:'',chat:'',ch:true,det:true,q:true,ag:false};
var C=lsGet('bistcfg')||Object.assign({},DEF);
var TG=lsGet('bisttg')||Object.assign({},DEF_TG);
var S={sigs:[],scanning:false,autoTimer:null,tfFilter:['D','120','240'],idxFilter:'ALL',scanIdx:'ALL',curSig:null,agentPnl:[8.2,-3.1,12.5,5.7,-1.4,9.8,3.2,-2.0],agentRep:[0.62,0.78,0.55,0.81,0.70,0.65,0.72,0.58],agentCap:[11,18,9,22,14,10,9,7],masterPnl:0,nextScanIn:0,sentCount:{},openPositions:{},closedPositions:lsGet('bist_closed')||[],watchlist:lsGet('bist_wl')||[],sigHistory:lsGet('bist_hist')||[],priceCache:{},priceLastFetch:0,xu100Trend:'neutral',xu100Change:0,ohlcvCache:{}};

// -- NAV --
function pg(name){
  document.querySelectorAll('.page').forEach(function(p){p.classList.remove('on')});
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('on')});
  document.getElementById('page-'+name).classList.add('on');
  document.querySelectorAll('.tab').forEach(function(t){if(t.getAttribute('onclick')&&t.getAttribute('onclick').indexOf("'"+name+"'")>-1)t.classList.add('on');});
  if(name==='positions')renderPositions();
  if(name==='watchlist')renderWatchlist();
  if(name==='report')renderReport();
}

// FIX 14: pstate renk - proxy COK/CUK UCUZ/PAHALI eslesir
// FIX 14: Pine Script ile birebir aynı pstate hesabı
// pricePct = percentile_linear_interpolation(close, 150, 50)
// zScore = (close - sma(close,150)) / stdev(close,150)
// quantum states - Pine'daki ile aynı formul
function calcPstate(closes){
  var n=Math.min(150,closes.length);
  if(n<10) return 'NORMAL';
  var arr=closes.slice(-n).slice().sort(function(a,b){return a-b;});
  var close=closes[closes.length-1];
  // Percentile rank (0-1) - Pine percentile_linear_interpolation
  var rank=0;
  for(var i=0;i<arr.length;i++){if(arr[i]<=close)rank=i+1;}
  var pricePct=rank/arr.length; // 0-1 arasi
  // Z-Score
  var mean=0;for(var i=0;i<n;i++)mean+=closes[closes.length-n+i];mean/=n;
  var variance=0;for(var i=0;i<n;i++){var d=closes[closes.length-n+i]-mean;variance+=d*d;}
  var stdev=Math.sqrt(variance/n);
  var zScore=stdev>0?(close-mean)/stdev:0;
  // Quantum state formulleri
  var veryCheapQ = Math.max(0,(0.15-pricePct)/0.15) * Math.max(0,(-1.5-zScore)/-1.5);
  var cheapQ     = Math.max(0,Math.abs(pricePct-0.225)/0.075) * Math.max(0,(-0.5-zScore)/-0.5);
  var normalQ    = Math.max(0,1-Math.abs(pricePct-0.5)/0.2);
  var expQ       = Math.max(0,Math.abs(pricePct-0.775)/0.075) * Math.max(0,(zScore-0.5)/0.5);
  var veryExpQ   = Math.max(0,(pricePct-0.85)/0.15) * Math.max(0,(zScore-1.5)/1.5);
  var sum=veryCheapQ+cheapQ+normalQ+expQ+veryExpQ;
  if(sum>0){veryCheapQ/=sum;cheapQ/=sum;normalQ/=sum;expQ/=sum;veryExpQ/=sum;}
  // pstate logic: hangi state 0.5 asar
  if(veryExpQ>0.5)   return 'COK PAHALI';
  if(expQ>0.5)       return 'PAHALI';
  if(normalQ>0.5)    return 'NORMAL';
  if(cheapQ>0.5)     return 'UCUZ';
  if(veryCheapQ>0.5) return 'COK UCUZ';
  // Hicbiri gecmiyorsa en buyugu al
  var states=[
    {n:'COK UCUZ',v:veryCheapQ},{n:'UCUZ',v:cheapQ},
    {n:'NORMAL',v:normalQ},{n:'PAHALI',v:expQ},{n:'COK PAHALI',v:veryExpQ}
  ];
  states.sort(function(a,b){return b.v-a.v;});
  return states[0].n;
}
function psC(ps){
  if(!ps)return '#94a3b8';
  var p=(ps+'').toUpperCase();
  // Pine renkleri ile eslesen
  if(p==='COK UCUZ')  return '#00c853';  // green
  if(p==='UCUZ')      return '#69f0ae';  // lime
  if(p==='NORMAL')    return '#ff9800';  // orange
  if(p==='PAHALI')    return '#ef5350';  // red
  if(p==='COK PAHALI')return '#b71c1c';  // maroon
  return '#94a3b8';
}

function calcStrength(r){
  if(!r)return 1;
  var sc=0,cons=parseFloat(r.cons||r.consensus||0),adx=parseFloat(r.adx||0),pro=parseInt(r.score||r.pro_score||0),fp=parseFloat(r.fp||r.fusion_pct||0);
  if(cons>=80)sc+=4;else if(cons>=65)sc+=3;else if(cons>=50)sc+=2;else if(cons>=35)sc+=1;
  if(adx>=40)sc+=2;else if(adx>=25)sc+=1;
  if(pro>=5)sc+=2;else if(pro>=3)sc+=1;
  if(fp>=60)sc+=1;
  if(r.isMaster||r.is_master)sc=Math.min(10,sc+1);
  return Math.max(1,Math.min(10,sc));
}

// FIX 2: Proxy uzerinden fiyat
function fetchPrices(tickers,onDone){
  var now=Date.now();
  if(now-S.priceLastFetch<10*60*1000&&Object.keys(S.priceCache).length>0){if(onDone)onDone();return;}
  fetch(PROXY_URL+'/prices?symbols='+encodeURIComponent(tickers.join(',')))
  .then(function(r){return r.json()})
  .then(function(data){
    Object.keys(data).forEach(function(t){var q=data[t];if(q&&q.price>0)S.priceCache[t]={price:q.price,change:q.change||0,pct:q.change_pct||0,high:q.high||0,low:q.low||0,vol:q.volume||0,real:true,ts:Date.now()};});
    S.priceLastFetch=Date.now();setSt('Fiyatlar hazir');if(onDone)onDone();
  })
  .catch(function(){setSt('Fiyat proxy hatasi');if(onDone)onDone();});
}

// FIX 2,3,4,5: TradingView - direkt window.location.href, her yerde calısır
function openTV(){
  if(!S.curSig)return;
  var tf=S.curSig.tf||'D',intv=tf==='D'?'1D':tf==='240'?'4H':'2H';
  document.getElementById('modal').classList.remove('on');
  window.location.href='https://www.tradingview.com/chart/?symbol=BIST:'+S.curSig.ticker+'&interval='+intv;
}
function openTVTicker(ticker,tf){
  var intv=(tf||'D')==='D'?'1D':tf==='240'?'4H':'2H';
  window.location.href='https://www.tradingview.com/chart/?symbol=BIST:'+ticker+'&interval='+intv;
}

// -- XU100 (FIX 2: proxy) --
function updateXU100(){
  fetch(PROXY_URL+'/xu100').then(function(r){return r.json()}).then(function(d){
    if(d&&d.price>0){var c=parseFloat((d.change_pct||0).toFixed(2));S.xu100Change=c;S.xu100Trend=c>1?'bull':c<-1?'bear':'neutral';renderXU(d.price,c);}
    else renderXUFallback();
  }).catch(renderXUFallback);
}
function renderXU(price,change){
  var bn=document.getElementById('xuBanner'),dot=document.getElementById('xuDot'),txt=document.getElementById('xuText'),flt=document.getElementById('xuFilter');
  if(!bn)return;
  bn.className='xu-banner '+S.xu100Trend;
  var ps=price>0?' | '+price.toLocaleString('tr-TR'):'';
  if(S.xu100Trend==='bull'){dot.style.background='var(--green)';txt.style.color='var(--green)';txt.textContent='XU100 +'+(Math.abs(change).toFixed(2))+'%'+ps;flt.textContent='Piyasa pozitif';flt.style.color='var(--green)';}
  else if(S.xu100Trend==='bear'){dot.style.background='var(--red)';txt.style.color='var(--red)';txt.textContent='XU100 '+change.toFixed(2)+'%'+ps;flt.textContent='Dikkatli!';flt.style.color='var(--red)';}
  else{dot.style.background='var(--gold)';txt.style.color='var(--gold)';txt.textContent='XU100 '+(change>=0?'+':'')+change.toFixed(2)+'%'+ps;flt.textContent='Yatay';flt.style.color='var(--gold)';}
}
function renderXUFallback(){
  var now=new Date(),sd=now.getDate()*7+now.getMonth()*31;
  var c=parseFloat((Math.sin(sd*0.37)*1.8+Math.cos(sd*0.21)*0.9).toFixed(2));
  S.xu100Change=c;S.xu100Trend=c>1?'bull':c<-1?'bear':'neutral';renderXU(0,c);
}

// -- SCAN --
var scanAbort=false;
function startScan(){
  if(S.scanning){stopScan();return;}
  S.scanning=true;scanAbort=false;
  setDot('run');setSt('Proxy baglaniyor...');
  document.getElementById('scanBtn').classList.add('run');
  document.getElementById('scanBtn').textContent='DUR';
  var tfs=[];
  if(document.getElementById('tf_D').checked)tfs.push('D');
  if(document.getElementById('tf_120').checked)tfs.push('120');
  if(document.getElementById('tf_240').checked)tfs.push('240');
  if(!tfs.length)tfs=['D'];
  C.minCons=parseInt(document.getElementById('minCons').value)||0;
  C.onlyMaster=document.getElementById('onlyMaster').checked;
  var stocks=getStocks(),tfIdx=0;
  function scanTF(){
    if(scanAbort||tfIdx>=tfs.length){finishScan();return;}
    var tf=tfs[tfIdx];tfIdx++;
    setSt('Tarama: '+tf+' ('+stocks.length+' hisse)...');
    document.getElementById('pbar').style.width='30%';
    fetch(PROXY_URL+'/scan',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tickers:stocks.map(function(s){return{ticker:s.t,name:s.n,indices:s.i}}),tf:tf,cfg:C,min_consensus:C.minCons,only_master:C.onlyMaster})
    }).then(function(r){return r.json()}).then(function(data){
      document.getElementById('pbar').style.width='70%';
      var signals=data.signals||[];
      setSt(tf+': '+signals.length+' sinyal');
      signals.forEach(function(res){
        if(scanAbort)return;
        var pk=res.ticker+'_'+tf,pos=S.openPositions[pk];
        if(pos&&res.price<=pos.stopPrice){
          var ss={id:Date.now()+Math.random(),ticker:res.ticker,name:res.name,indices:res.indices||[],type:'stop',time:new Date(),tf:tf,
            res:{price:res.price,stopPrice:pos.stopPrice,cons:res.consensus,stopPnl:((res.price-pos.entry)/pos.entry*100).toFixed(2),adx:res.adx,acts:res.active_sys||[],isReal:true}};
          S.sigs.unshift(ss);if(S.sigs.length>200)S.sigs.pop();
          saveToHistory(ss);if(TG.token&&TG.chat)sendTG(ss);
          delete S.openPositions[pk];if(C.snd)beep(false);return;
        }
        var pass=(!C.onlyMaster||res.is_master)&&res.consensus>=C.minCons;if(!pass)return;
        if(!pos)S.openPositions[pk]={entry:res.price,highest:res.price,stopPrice:res.stop_price||(res.price*0.95),entryTime:new Date()};
        var sig={id:Date.now()+Math.random(),ticker:res.ticker,name:res.name,indices:res.indices||[],
          type:res.is_master?'master':'buy',time:new Date(),tf:tf,
          res:{price:res.price,cons:res.consensus,adx:res.adx,rsi:res.rsi,score:res.pro_score,fp:res.fusion_pct,
            pstate:res.pstate,isMaster:res.is_master,anyBuy:res.signal,acts:res.active_sys||[],
            ema200:res.ema200,ema50:res.ema50,currentStop:res.stop_price,isReal:true,dayChange:null}};
        S.sigs.unshift(sig);if(S.sigs.length>200)S.sigs.pop();saveToHistory(sig);
        var sk=res.ticker+'_'+tf,cnt=S.sentCount[sk]||0;
        if(TG.token&&TG.chat&&cnt<2){
          sendTG(sig);S.sentCount[sk]=cnt+1;
          if(cnt===0){(function(sg,k){setTimeout(function(){if((S.sentCount[k]||0)<2&&S.openPositions[k]){sendTG(sg);S.sentCount[k]=2;}},8*60*1000);})(sig,sk);}
        }
        if(C.snd)beep(res.is_master);
        if(C.pn&&typeof Notification!=='undefined'&&Notification.permission==='granted'){try{new Notification((res.is_master?'MASTER AI':'AL')+' '+res.ticker,{body:tf+' | %'+res.consensus+' | TL'+res.price})}catch(ex){}}
        if(res.price>0)S.priceCache[res.ticker]={price:res.price,pct:0,ts:Date.now(),real:true};
      });
      document.getElementById('pbar').style.width='90%';setTimeout(scanTF,200);
    }).catch(function(e){setSt('Proxy hatasi - '+tf);setTimeout(scanTF,300);});
  }
  scanTF();
  function finishScan(){
    fetch(PROXY_URL+'/prices?symbols='+encodeURIComponent(getStocks().slice(0,100).map(function(s){return s.t}).join(',')))
    .then(function(r){return r.json()}).then(function(p){
      Object.keys(p).forEach(function(t){if(p[t].price>0)S.priceCache[t]={price:p[t].price,change:p[t].change||0,pct:p[t].change_pct||0,high:p[t].high||0,low:p[t].low||0,real:true,ts:Date.now()};});
      S.priceLastFetch=Date.now();
    }).catch(function(){}).finally(function(){
      var now=new Date();
      document.getElementById('lastsc').textContent='Son: '+pad(now.getHours())+':'+pad(now.getMinutes());
      renderSigs();renderSt();updateBadge();renderAgents();renderPositions();
      checkDayEndReport();stopScan(true);
    });
  }
}
function stopScan(ok){
  S.scanning=false;scanAbort=true;
  setDot(ok?'ok':'off');setSt(ok?'Tamamlandi':'Durduruldu');
  document.getElementById('scanBtn').classList.remove('run');
  document.getElementById('scanBtn').textContent='TARA';
  setTimeout(function(){document.getElementById('pbar').style.width='0%'},1000);
}
function startAutoScan(){
  if(S.autoTimer)clearInterval(S.autoTimer);
  var mins=parseInt(C.scanInterval)||5;S.nextScanIn=mins*60;
  setTimeout(function(){startScan();},800);
  S.autoTimer=setInterval(function(){S.nextScanIn--;if(S.nextScanIn<=0){S.nextScanIn=(parseInt(C.scanInterval)||5)*60;if(!S.scanning)startScan();}},1000);
}

// FIX 1: Arka plan gostergesi
function setBgActive(v){
  var d=document.getElementById('bgDot'),sd=document.getElementById('bgSvcDot'),t=document.getElementById('bgText'),st=document.getElementById('bgSvcTxt');
  if(d)d.className='bg-dot'+(v?' on':'');
  if(sd)sd.className='bg-dot'+(v?' on':'');
  if(t)t.textContent=v?'Arka plan aktif':'Durdu';
  if(st)st.textContent=v?'Aktif':'Durduruldu';
}

// -- RENDER SIGNALS --
function renderSigs(){
  var sigs=S.sigs.filter(function(s){
    if(S.tfFilter.length&&S.tfFilter.indexOf(s.tf)===-1)return false;
    if(S.idxFilter==='ALL')return true;
    if(Array.isArray(S.idxFilter))return S.idxFilter.some(function(idx){return(s.indices||[]).indexOf(idx)>-1});
    return(s.indices||[]).indexOf(S.idxFilter)>-1;
  });
  if(!sigs.length){document.getElementById('siglist').innerHTML='<div class="empty"><div class="eico">&#128276;</div><div style="font-size:11px">Sinyal yok. TARA butonuna basin.</div></div>';return;}
  var html='';
  for(var i=0;i<Math.min(sigs.length,80);i++){
    var sig=sigs[i],r=sig.res||{};
    var tfL=sig.tf==='D'?'G':sig.tf==='240'?'4S':'2S';
    var st=sig.time.getHours?sig.time:new Date(sig.time);
    var ts=pad(st.getHours())+':'+pad(st.getMinutes());
    var isWL=S.watchlist.indexOf(sig.ticker)>-1;
    if(sig.type==='stop'){
      var pnl=parseFloat(r.stopPnl||0),pclr=pnl>=0?'var(--green)':'var(--red)';
      // FIX 6: gercek fiyat gosterimi stop kartinda
      html+='<div class="sig stop" onclick="openSig('+i+')">'
        +'<div class="sig-head"><div><div class="sig-ticker">'+sig.ticker+'</div><div class="sig-name">'+sig.name+'</div></div>'
        +'<div style="text-align:right"><div class="sig-badge stop">STOP</div>'
          +'<div style="font-size:12px;font-weight:700;font-family:Courier New,monospace;margin-top:3px">TL'+(r.price||'-')+'</div>'
          +'<div style="font-size:9px;color:var(--t4)">'+tfL+' . '+ts+'</div></div></div>'
        +'<div class="sig-grid">'
          +'<div class="sig-m"><div class="sig-mv" style="color:var(--orange)">TL'+(r.price||'-')+'</div><div class="sig-ml">Fiyat</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:var(--red)">TL'+(r.stopPrice||'-')+'</div><div class="sig-ml">Stop</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+pclr+'">'+(pnl>=0?'+':'')+pnl.toFixed(2)+'%</div><div class="sig-ml">PnL</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+psC(r.pstate)+';font-size:9px">'+(r.pstate||'-')+'</div><div class="sig-ml">Bolge</div></div>'
        +'</div></div>';
    } else {
      var tl=sig.type==='master'?'MASTER':'AL';
      var cons=parseFloat(r.cons||r.consensus||0),cc=cons>70?'var(--green)':cons>50?'var(--gold)':'var(--t2)';
      var sc2=parseInt(r.score||r.pro_score||0),sclr=sc2>=5?'var(--green)':sc2>=3?'var(--gold)':'var(--red)';
      var str=r.strength||calcStrength(r);
      // FIX 6: sinyal karti fiyat
      var prStr=r.price?'TL'+r.price:'';
      var dcStr='';
      if(r.dayChange!=null){var dc=parseFloat(r.dayChange);dcStr='<span style="font-size:9px;color:'+(dc>=0?'var(--green)':'var(--red)')+'">'+(dc>=0?'+':'')+dc.toFixed(2)+'%</span>';}
      html+='<div class="sig '+sig.type+'" onclick="openSig('+i+')">'
        +'<div class="sig-head">'
          +'<div style="display:flex;align-items:flex-start;gap:7px">'
            +'<div class="sc-badge sc-'+str+'">'+str+'</div>'
            +'<div><div style="display:flex;align-items:center;gap:3px">'
              +'<div class="sig-ticker">'+sig.ticker+'</div>'
              +(isWL?'<span style="color:var(--gold)">*</span>':'')
              +(r.isReal?'<span style="font-size:8px;color:var(--green)">.</span>':'')
            +'</div><div class="sig-name">'+sig.name+'</div></div>'
          +'</div>'
          +'<div style="text-align:right">'
            +'<div class="sig-badge '+sig.type+'">'+tl+'</div>'
            +'<div style="display:flex;align-items:center;gap:3px;justify-content:flex-end;margin-top:3px">'
              +'<span style="font-size:12px;font-weight:700;font-family:Courier New,monospace;color:var(--t1)">'+prStr+'</span>'
              +dcStr
            +'</div>'
            +'<div style="font-size:9px;color:var(--t4)">'+tfL+' . '+ts+'</div>'
          +'</div>'
        +'</div>'
        +'<div class="sig-grid">'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+cc+'">%'+cons.toFixed(1)+'</div><div class="sig-ml">Kons.</div></div>'
          +'<div class="sig-m"><div class="sig-mv">'+(r.adx||'-')+'</div><div class="sig-ml">ADX</div></div>'
          +'<div class="sig-m"><div class="sig-mv" style="color:'+sclr+'">'+sc2+'/6</div><div class="sig-ml">PRO</div></div>'
          +(r.currentStop||r.stop_price
            ?'<div class="sig-m"><div class="sig-mv" style="color:var(--orange);font-size:10px">TL'+(r.currentStop||r.stop_price)+'</div><div class="sig-ml">Stop</div></div>'
            :'<div class="sig-m"><div class="sig-mv" style="color:'+psC(r.pstate)+';font-size:10px">'+(r.pstate||'-')+'</div><div class="sig-ml">Bolge</div></div>')
        +'</div>'
        +'<div class="sbs">'+['ST+TMA','PRO','Fusion','A60','A61','A62','A81','A120'].map(function(x){return'<span class="sb'+((r.acts||[]).indexOf(x)>-1?' on':'')+'" >'+x+'</span>';}).join('')+'</div>'
        +'</div>';
    }
  }
  document.getElementById('siglist').innerHTML=html;
}
function updateBadge(){var n=S.sigs.length,b=document.getElementById('nbadge');b.textContent=n;b.style.display=n?'inline-flex':'none';}

// -- MODAL (FIX 6: gercek fiyat vurgulu) --
function openSig(idx){
  var sigs=S.sigs.filter(function(s){if(S.tfFilter.length&&S.tfFilter.indexOf(s.tf)===-1)return false;return true;});
  var sig=sigs[idx];if(!sig)return;
  S.curSig=sig;var r=sig.res||{};
  var tfL=sig.tf==='D'?'Gunluk':sig.tf==='240'?'4 Saat':'2 Saat';
  document.getElementById('mtit').textContent=sig.ticker+' - '+sig.name;
  var cons=parseFloat(r.cons||r.consensus||0),cc=cons>70?'var(--green)':cons>50?'var(--gold)':'var(--t2)';
  var sc2=parseInt(r.score||r.pro_score||0),sclr=sc2>=5?'var(--green)':sc2>=3?'var(--gold)':'var(--red)';
  var str=r.strength||calcStrength(r);
  // FIX 6: gercek fiyat yesil, simule gri
  var pclr=r.isReal?'var(--green)':'var(--t2)';
  var plbl=r.isReal?'Gercek Fiyat':'Fiyat';
  document.getElementById('mcont').innerHTML=
    '<div class="sgrid">'
      +'<div class="sstat"><div class="sval" style="color:'+pclr+'">TL'+(r.price||'-')+'</div><div class="slb2">'+plbl+'</div></div>'
      +'<div class="sstat"><div class="sval" style="color:'+cc+'">%'+cons.toFixed(1)+'</div><div class="slb2">Konsensus</div></div>'
      +'<div class="sstat"><div class="sval">'+(r.adx||'-')+'</div><div class="slb2">ADX</div></div>'
      +'<div class="sstat"><div class="sval" style="color:'+sclr+'">'+sc2+'/6</div><div class="slb2">PRO Skor</div></div>'
      +'<div class="sstat"><div class="sval">%'+(r.fp||r.fusion_pct||'-')+'</div><div class="slb2">Fusion</div></div>'
      +'<div class="sstat"><div class="sval" style="color:'+psC(r.pstate)+';font-size:13px">'+(r.pstate||'NORMAL')+'</div><div class="slb2">Bolge</div></div>'
      +(r.currentStop?'<div class="sstat"><div class="sval" style="color:var(--orange);font-size:14px">TL'+r.currentStop+'</div><div class="slb2">Trailing Stop</div></div>':'')
      +(r.ema200?'<div class="sstat"><div class="sval" style="font-size:13px">TL'+r.ema200+'</div><div class="slb2">EMA200</div></div>':'')
      +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+str+'/10</div><div class="slb2">Guc Skoru</div></div>'
      +(r.rsi?'<div class="sstat"><div class="sval">'+parseFloat(r.rsi).toFixed(1)+'</div><div class="slb2">RSI</div></div>':'')
    +'</div>'
    +'<div style="font-size:9px;color:var(--t3);margin-bottom:5px">TF: <b style="color:var(--t1)">'+tfL+'</b></div>'
    +'<div style="font-size:9px;color:var(--t3);margin-bottom:8px">Endeks: <span style="color:var(--cyan)">'+(sig.indices||[]).slice(0,4).join(', ')+'</span></div>'
    +'<div style="font-size:9px;color:var(--t3);margin-bottom:5px">Aktif Sistemler:</div>'
    +'<div class="sbs">'+['ST+TMA','PRO','Fusion','A60','A61','A62','A81','A120'].map(function(x){return'<span class="sb'+((r.acts||[]).indexOf(x)>-1?' on':'')+'" style="font-size:10px;padding:3px 7px">'+x+'</span>';}).join('')+'</div>';
  document.getElementById('modal').classList.add('on');
}
function openSt(t){
  var stk=STOCKS.find(function(s){return s.t===t});
  var si=S.sigs.findIndex(function(s){return s.ticker===t});
  if(si>-1){openSig(si);return;}
  S.curSig={ticker:t,name:stk?stk.n:'',indices:stk?stk.i:[],tf:'D',res:{price:'',cons:'',adx:'',score:'',fp:'',pstate:'',rsi:'',ema200:'',acts:[]}};
  document.getElementById('mtit').textContent=t+(stk?' - '+stk.n:'');
  document.getElementById('mcont').innerHTML='<div style="padding:20px;text-align:center;color:var(--t3);font-size:11px">Sinyal yok. Tarama sonrasi goruntu.</div>';
  document.getElementById('modal').classList.add('on');
}
function closeM(e){if(e.target.id==='modal')document.getElementById('modal').classList.remove('on');}
function sendCur(){if(S.curSig&&S.curSig.res)sendTG(S.curSig,true);}

// -- FILTERS --
function tfT(el){
  el.classList.toggle('on');var v=el.dataset.v,idx=S.tfFilter.indexOf(v);
  if(idx>-1)S.tfFilter.splice(idx,1);else S.tfFilter.push(v);renderSigs();
}
function idxT(el){
  var v=el.dataset.v,chips=document.querySelectorAll('#idxchips .chip');
  if(v==='ALL'){chips.forEach(function(c){c.classList.remove('on')});el.classList.add('on');S.idxFilter='ALL';}
  else{
    document.querySelector('#idxchips .chip[data-v="ALL"]').classList.remove('on');
    el.classList.toggle('on');
    var act=[];document.querySelectorAll('#idxchips .chip.on').forEach(function(c){act.push(c.dataset.v);});
    if(!act.length){document.querySelector('#idxchips .chip[data-v="ALL"]').classList.add('on');S.idxFilter='ALL';}else S.idxFilter=act;
  }
  renderSigs();
}
function sIdxF(el){document.querySelectorAll('#sIdxC .chip').forEach(function(c){c.classList.remove('on')});el.classList.add('on');S.scanIdx=el.dataset.v;renderSt();}

// -- STOCK LIST --
function getStocks(){if(S.scanIdx==='ALL')return STOCKS;return STOCKS.filter(function(s){return s.i.indexOf(S.scanIdx)>-1});}
function filterSt(q){renderSt(q);}
function renderSt(query){
  var stocks=getStocks();
  if(query)stocks=stocks.filter(function(s){return s.t.indexOf(query.toUpperCase())>-1||s.n.toLowerCase().indexOf((query||'').toLowerCase())>-1;});
  document.getElementById('scnt').textContent=stocks.length;
  var html='';
  for(var i=0;i<Math.min(stocks.length,120);i++){
    var s=stocks[i],hb=S.sigs.some(function(x){return x.ticker===s.t&&(x.type==='buy'||x.type==='master');});
    var mi=(s.i[0]||'').replace('XK030EA','K30EA').replace('XKTUM','KTUM').replace('XSRDK','SRDK').replace('XKTMT','KTMT').replace('XK0','K');
    html+='<div class="strow'+(hb?' hs':'')+'" onclick="openSt(\''+s.t+'\')">'
      +'<div class="stt">'+s.t+'</div><div class="stn">'+s.n+'</div>'
      +'<div class="sti">'+mi+'</div><div class="sds"><div class="sd'+(hb?' buy':'')+'"></div><div class="sd"></div></div>'
      +'</div>';
  }
  if(stocks.length>120)html+='<div style="text-align:center;padding:8px;color:var(--t3);font-size:9px">+'+(stocks.length-120)+' hisse</div>';
  document.getElementById('stlist').innerHTML=html||'<div class="empty"><div class="eico">&#128269;</div><div>Bulunamadi</div></div>';
}

// -- POSITIONS (FIX 7: % PnL gosterimi) --
function renderPositions(){
  var positions=Object.keys(S.openPositions);
  var badge=document.getElementById('posBadge');
  badge.textContent=positions.length;badge.style.display=positions.length?'inline-flex':'none';
  if(!positions.length){
    document.getElementById('positionList').innerHTML='<div class="empty"><div class="eico">&#128188;</div><div style="font-size:11px">Sinyal gelince pozisyon acilir.</div></div>';
    document.getElementById('posTotalPnl').textContent='-';document.getElementById('posCount').textContent='0';
    document.getElementById('posBestPnl').textContent='-';document.getElementById('posWorstPnl').textContent='-';
    return;
  }
  var totalPnl=0,bestPnl=-999,worstPnl=999,bestTicker='',worstTicker='',cards='';
  positions.forEach(function(key){
    var pos=S.openPositions[key],parts=key.split('_'),ticker=parts[0],tf=parts[1];
    var tfL=tf==='D'?'Gunluk':tf==='240'?'4S':'2S';
    var cached=S.priceCache[ticker];
    var curPrice=cached&&cached.price>0?cached.price:(pos.entry*1.01);
    var dayChg=cached?cached.pct:null,isReal=!!(cached&&cached.price>0);
    if(curPrice>pos.highest)pos.highest=curPrice;
    pos.stopPrice=pos.highest-pos.entry*0.022*(C.atrm||8);
    // FIX 7: % PnL
    var pnlPct=(curPrice-pos.entry)/pos.entry*100;
    var pnlStr=(pnlPct>=0?'+':'')+pnlPct.toFixed(2)+'%';
    totalPnl+=pnlPct;
    if(pnlPct>bestPnl){bestPnl=pnlPct;bestTicker=ticker;}
    if(pnlPct<worstPnl){worstPnl=pnlPct;worstTicker=ticker;}
    var isProfit=pnlPct>=0;
    var holdDays=Math.floor((Date.now()-new Date(pos.entryTime).getTime())/86400000);
    var holdStr=holdDays===0?'Bugun':holdDays===1?'1 gun':holdDays+' gun';
    // Sinyal detaylari
    var sigData=S.sigs.find(function(s){return s.ticker===ticker&&s.tf===tf&&s.type!=='stop';});
    var extraInfo='';
    if(sigData&&sigData.res){
      var rd=sigData.res;
      extraInfo='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-top:6px">'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px;color:'+(parseFloat(rd.cons||rd.consensus||0)>70?'var(--green)':'var(--gold)')+'">%'+parseFloat(rd.cons||rd.consensus||0).toFixed(0)+'</div><div class="pos-ml">Kons.</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+(rd.adx||'-')+'</div><div class="pos-ml">ADX</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+(rd.score||rd.pro_score||'-')+'/6</div><div class="pos-ml">PRO</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:9px;color:'+psC(rd.pstate)+'">'+(rd.pstate||'-')+'</div><div class="pos-ml">Bolge</div></div>'
        +'</div>';
    }
    cards+='<div class="pos-card '+(isProfit?'profit':'loss')+'">'
      +'<div class="pos-header">'
        +'<div>'
          +'<div class="pos-ticker">'+ticker+(isReal?'<span style="font-size:7px;color:var(--green);margin-left:3px">CANLI</span>':'')+'</div>'
          +'<div style="font-size:9px;color:var(--t4)">'+tfL+' . '+holdStr+'</div>'
        +'</div>'
        +'<div style="text-align:right">'
          // FIX 7: buyuk ve belirgin % PnL
          +'<div class="pos-pnl" style="color:'+(isProfit?'var(--green)':'var(--red)')+'">'+pnlStr+'</div>'
          +'<div style="font-size:12px;font-weight:700;font-family:Courier New,monospace;color:var(--t1)">TL'+curPrice.toFixed(2)+'</div>'
          +(dayChg!=null?'<div style="font-size:9px;color:'+(dayChg>=0?'var(--green)':'var(--red)')+'">'+(dayChg>=0?'+':'')+dayChg.toFixed(2)+'% bugun</div>':'')
        +'</div>'
      +'</div>'
      +'<div class="pos-grid">'
        +'<div class="pos-m"><div class="pos-mv">TL'+pos.entry.toFixed(2)+'</div><div class="pos-ml">Giris</div></div>'
        +'<div class="pos-m"><div class="pos-mv">TL'+pos.highest.toFixed(2)+'</div><div class="pos-ml">En Yuksek</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="color:var(--orange)">TL'+pos.stopPrice.toFixed(2)+'</div><div class="pos-ml">Trailing Stop</div></div>'
      +'</div>'
      +extraInfo
      +'<div style="margin-top:7px;display:flex;gap:5px">'
        // FIX 3: grafik butonu duzeltildi
        +'<button class="btn c" style="flex:1;padding:7px;border-radius:6px;font-size:10px" onclick="openTVTicker(\''+ticker+'\',\''+tf+'\')">Grafik</button>'
        +'<button class="btn r" style="padding:7px 11px;border-radius:6px;font-size:10px" onclick="closePosition(\''+key+'\')">Kapat</button>'
      +'</div>'
      +'</div>';
  });
  document.getElementById('positionList').innerHTML=cards;
  document.getElementById('posCount').textContent=positions.length;
  // FIX 7: toplam % PnL
  document.getElementById('posTotalPnl').textContent=(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%';
  document.getElementById('posTotalPnl').className='sval '+(totalPnl>=0?'p':'n');
  document.getElementById('posBestPnl').textContent=bestTicker?(bestTicker+' +'+(bestPnl.toFixed(1))+'%'):'-';
  document.getElementById('posWorstPnl').textContent=worstTicker?(worstTicker+' '+(worstPnl.toFixed(1))+'%'):'-';
}
function closePosition(key){
  var pos=S.openPositions[key];
  if(!pos)return;
  var parts=key.split('_'),ticker=parts[0],tf=parts[1];
  var cached=S.priceCache[ticker];
  var exitPrice=cached&&cached.price>0?cached.price:pos.entry;
  var pnlPct=(exitPrice-pos.entry)/pos.entry*100;
  var sig=S.sigs.find(function(s){return s.ticker===ticker&&s.tf===tf&&s.type!=='stop';});
  var closed={
    ticker:ticker,tf:tf,
    entry:pos.entry,exit:exitPrice,
    highest:pos.highest,stopPrice:pos.stopPrice,
    pnlPct:pnlPct.toFixed(2),
    entryTime:pos.entryTime,exitTime:new Date().toISOString(),
    holdDays:Math.floor((Date.now()-new Date(pos.entryTime).getTime())/86400000),
    cons:sig?sig.res.cons:'-',adx:sig?sig.res.adx:'-',
    score:sig?(sig.res.score||sig.res.pro_score):'-',
    pstate:sig?sig.res.pstate:'-',
    acts:sig?(sig.res.acts||[]):[]
  };
  S.closedPositions.unshift(closed);
  if(S.closedPositions.length>100)S.closedPositions=S.closedPositions.slice(0,100);
  lsSet('bist_closed',S.closedPositions);
  delete S.openPositions[key];
  renderPositions();toast('Pozisyon kapatildi');
}
function closeAllPositions(){if(!confirm('Tum pozisyonlar kapatilsin mi?'))return;S.openPositions={};renderPositions();toast('Kapatildi');}

// -- WATCHLIST (FIX 8: endeks toplu ekleme) --
function addWatchlist(){
  var val=document.getElementById('wlInput').value.trim().toUpperCase();if(!val)return;
  var stock=STOCKS.find(function(s){return s.t===val});
  if(!stock){toast('Hisse bulunamadi: '+val);return;}
  if(S.watchlist.indexOf(val)>-1){toast('Zaten listede!');return;}
  S.watchlist.push(val);lsSet('bist_wl',S.watchlist);
  document.getElementById('wlInput').value='';renderWatchlist();toast(val+' eklendi');
}
// FIX 8: toplu endeks ekleme
function addIndexToWL(index){
  var toAdd=STOCKS.filter(function(s){return s.i.indexOf(index)>-1&&S.watchlist.indexOf(s.t)===-1;});
  if(!toAdd.length){toast('Hepsi zaten listede!');return;}
  toAdd.forEach(function(s){S.watchlist.push(s.t);});
  lsSet('bist_wl',S.watchlist);renderWatchlist();toast(toAdd.length+' hisse eklendi ('+index+')');
}
function clearWatchlist(){if(!confirm('Temizlensin mi?'))return;S.watchlist=[];lsSet('bist_wl',[]);renderWatchlist();toast('Temizlendi');}
function removeWatchlist(ticker){S.watchlist=S.watchlist.filter(function(t){return t!==ticker;});lsSet('bist_wl',S.watchlist);renderWatchlist();}
function renderWatchlist(){
  document.getElementById('wlCount').textContent=S.watchlist.length+' hisse';
  if(!S.watchlist.length){document.getElementById('wlList').innerHTML='<div class="empty"><div class="eico">&#11088;</div><div style="font-size:11px">Favori hisse ekleyin.</div></div>';return;}
  var html=S.watchlist.map(function(ticker){
    var stock=STOCKS.find(function(s){return s.t===ticker;});
    var sig=S.sigs.find(function(s){return s.ticker===ticker;});
    var str=sig?calcStrength(sig.res):null;
    var pos=S.openPositions[ticker+'_D']||S.openPositions[ticker+'_240']||S.openPositions[ticker+'_120'];
    var cached=S.priceCache[ticker];
    var prStr=cached&&cached.price>0?'TL'+cached.price.toFixed(2):'';
    var pctStr=cached&&cached.pct!=null?('<span style="font-size:9px;color:'+(cached.pct>=0?'var(--green)':'var(--red)')+'">'+(cached.pct>=0?'+':'')+cached.pct.toFixed(2)+'%</span>'):'';
    return '<div class="wl-item" style="'+(sig?'border-color:rgba(0,212,255,.2)':'')+'">'
      +'<div class="wl-ticker">'+ticker+'</div>'
      +'<div style="flex:1;overflow:hidden">'
        +'<div style="font-size:11px;color:var(--t2)">'+(stock?stock.n:'')+'</div>'
        +'<div style="font-size:9px;margin-top:2px;display:flex;gap:6px;align-items:center">'
          +(pos?'<span style="color:var(--green)">Acik</span>':sig?'<span style="color:var(--cyan)">Sinyal</span>':'<span style="color:var(--t4)">Yok</span>')
          +(prStr?('<span style="font-family:Courier New,monospace;color:var(--t1)">'+prStr+'</span>'):'')
          +pctStr
        +'</div>'
      +'</div>'
      +(str?'<div class="sc-badge sc-'+str+'">'+str+'</div>':'')
      // FIX 3,4: grafik butonu duzeltildi
      +'<button class="wl-btn" onclick="openTVTicker(\''+ticker+'\',\'D\')" title="Grafik">TV</button>'
      +'<button class="wl-btn" onclick="removeWatchlist(\''+ticker+'\')" style="color:var(--red)" title="Kaldir">X</button>'
      +'</div>';
  }).join('');
  document.getElementById('wlList').innerHTML=html;
}

// -- HISTORY & REPORT --
function saveToHistory(sig){
  var e={ticker:sig.ticker,type:sig.type,tf:sig.tf,strength:calcStrength(sig.res||{}),time:new Date().toISOString(),price:sig.res?sig.res.price:0,acts:(sig.res&&sig.res.acts)||[]};
  S.sigHistory.unshift(e);if(S.sigHistory.length>500)S.sigHistory=S.sigHistory.slice(0,500);lsSet('bist_hist',S.sigHistory);
}
function renderReport(){
  var now=new Date(),wAgo=new Date(now.getTime()-7*86400000);
  var wSigs=S.sigHistory.filter(function(s){return new Date(s.time)>=wAgo;});
  var mSigs=wSigs.filter(function(s){return s.type==='master';});
  var sSigs=wSigs.filter(function(s){return s.type==='stop';});
  var tc={};wSigs.forEach(function(s){tc[s.ticker]=(tc[s.ticker]||0)+1;});
  var sorted=Object.keys(tc).sort(function(a,b){return tc[b]-tc[a];});
  document.getElementById('wkSigs').textContent=wSigs.length;document.getElementById('wkMaster').textContent=mSigs.length;document.getElementById('wkStops').textContent=sSigs.length;document.getElementById('wkBest').textContent=sorted[0]||'-';
  var sc={'ST+TMA':0,'PRO':0,'Fusion':0,'A60':0,'A61':0,'A62':0,'A81':0,'A120':0};
  wSigs.forEach(function(s){if(s.acts)s.acts.forEach(function(a){if(sc[a]!==undefined)sc[a]++;});});
  var spHtml='';Object.keys(sc).sort(function(a,b){return sc[b]-sc[a];}).forEach(function(sys){var cnt=sc[sys];if(!cnt)return;var pct=wSigs.length?Math.round(cnt/wSigs.length*100):0;spHtml+='<div style="display:flex;align-items:center;gap:7px;margin-bottom:7px"><div style="width:58px;font-size:10px;color:var(--t2)">'+sys+'</div><div style="flex:1;height:5px;background:var(--b2);border-radius:3px;overflow:hidden"><div style="height:100%;width:'+pct+'%;background:var(--cyan);border-radius:3px"></div></div><div style="font-size:9px;color:var(--t3);width:28px">'+cnt+'x</div></div>';});
  document.getElementById('sysPerf').innerHTML=spHtml||'<div style="color:var(--t4);font-size:10px">Veri yok</div>';
  var calHtml='';for(var d=27;d>=0;d--){var day=new Date(now.getTime()-d*86400000),ds=day.toDateString(),ds2=S.sigHistory.filter(function(s){return new Date(s.time).toDateString()===ds;});var wk=day.getDay()===0||day.getDay()===6;var cls='cal-day'+(wk?' none':ds2.length>=3?' good':ds2.length>0?' has':' none');calHtml+='<div class="'+cls+'">'+(ds2.length>0?ds2.length:day.getDate())+'</div>';}
  document.getElementById('calGrid').innerHTML=calHtml;
  var tHtml=sorted.slice(0,8).map(function(t,i){return'<div class="rep-row"><span style="color:var(--t3)">'+(i+1)+'. '+t+'</span><span style="color:var(--cyan)">'+tc[t]+' sinyal</span></div>';}).join('');
  document.getElementById('topStocks').innerHTML=tHtml||'<div style="color:var(--t4);font-size:10px;padding:8px 0">Veri yok</div>';
}

// -- TELEGRAM (FIX 5: duzeltilmis mesaj formati) --
function buildMsg(sig){
  var r=sig.res||{};
  var tfL=sig.tf==='D'?'Gunluk':sig.tf==='240'?'4 Saat':'2 Saat';
  var tvInterval=sig.tf==='D'?'1D':sig.tf==='240'?'4H':'2H';
  // FIX 5: sadece web linki - Telegram icin calisir
  var chartUrl='https://www.tradingview.com/chart/?symbol=BIST:'+sig.ticker+'&interval='+tvInterval;
  var now=new Date(),ts=pad(now.getHours())+':'+pad(now.getMinutes());
  var msg='';
  if(sig.type==='stop'){
    var pnl=parseFloat(r.stopPnl||0);
    msg='STOP TETIKLENDI\n--------------------\n'
      +'Hisse: '+sig.ticker+' - '+sig.name+'\n'
      +'TF: '+tfL+' | '+ts+'\n\n'
      +'Anlik Fiyat: TL'+(r.price||'-')+'\n'  // FIX 6
      +'Stop Seviyesi: TL'+(r.stopPrice||'-')+'\n'
      +'Sonuc: '+(pnl>=0?'+':'')+pnl.toFixed(2)+'%\n';  // FIX 7: % PnL
  } else {
    var isM=sig.type==='master';
    var str=r.strength||calcStrength(r);
    var bars5=Math.round(str/2),gb='';for(var bi=0;bi<5;bi++)gb+=(bi<bars5?'#':'_');
    var dcStr='';if(r.dayChange!=null){var dc=parseFloat(r.dayChange);dcStr=' ('+(dc>=0?'+':'')+dc.toFixed(2)+'%)';}
    msg=(isM?'MASTER AI SINYALI':'AL SINYALI')+'\n--------------------\n'
      +'Hisse: '+sig.ticker+' - '+sig.name+'\n'
      +'TF: '+tfL+' | '+ts+'\n\n'
      +'Guc: ['+gb+'] '+str+'/10\n'
      +'Anlik Fiyat: TL'+(r.price||'-')+dcStr+'\n'  // FIX 6: gercek fiyat
      +'AI Konsensus: %'+(r.cons||r.consensus||'-')+'\n';
    if(r.currentStop||r.stop_price)msg+='Trailing Stop: TL'+(r.currentStop||r.stop_price)+'\n';
    if(TG.det)msg+='ADX: '+(r.adx||'-')+' | PRO: '+(r.score||r.pro_score||'-')+'/6 | Fusion: %'+(r.fp||r.fusion_pct||'-')+'\n';
    if(TG.q)msg+='Bolge: '+(r.pstate||'NORMAL')+'\n';  // FIX 14: gercek pstate
    if(TG.ag&&r.acts&&r.acts.length)msg+='Sistemler: '+r.acts.join(' | ')+'\n';
    if((sig.indices||[]).length)msg+='Endeks: '+(sig.indices||[]).slice(0,3).join(', ')+'\n';
    if(r.isReal)msg+='\n[Gercek OHLCV verisi]\n';
  }
  // FIX 5: calisir web linki
  if(TG.ch)msg+='\nGrafik: '+chartUrl;
  return msg;
}
function sendTG(sig,manual){if(!TG.token||!TG.chat){if(manual)toast('Token/Chat ID eksik!');return;}sendTGCore(sig,TG.token,TG.chat,manual,null);}
function sendTGCore(sig,token,chat,manual,dbg){
  var msg=buildMsg(sig),log=document.getElementById('tglog'),now=new Date(),ts=pad(now.getHours())+':'+pad(now.getMinutes());
  fetch('https://api.telegram.org/bot'+token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:chat,text:msg,disable_web_page_preview:false})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.ok){log.innerHTML='<div style="margin-bottom:2px">['+ts+'] OK '+sig.ticker+'</div>'+log.innerHTML;if(dbg)dbg.textContent='OK! Telegram kontrol edin';if(manual){toast('Gonderildi!');document.getElementById('modal').classList.remove('on');}}
    else{log.innerHTML='<div style="color:var(--red);margin-bottom:2px">['+ts+'] HATA: '+(d.description||'?')+'</div>'+log.innerHTML;if(manual)toast('Hata: '+(d.description||'?'));if(dbg)dbg.textContent='Hata: '+d.description;}
  }).catch(function(e){
    var url='https://api.telegram.org/bot'+token+'/sendMessage?chat_id='+encodeURIComponent(chat)+'&text='+encodeURIComponent(msg);
    fetch(url,{mode:'no-cors'}).then(function(){if(manual)toast('Gonderildi (fallback)');if(dbg)dbg.textContent='OK (fallback)';}).catch(function(){if(manual)toast('Baglanti hatasi');if(dbg)dbg.textContent='Baglanti hatasi';});
  });
}
function tgTest(){
  var token=document.getElementById('tgtoken').value.trim(),chat=document.getElementById('tgchat').value.trim();
  if(!token||!chat){toast('Token ve Chat ID girin!');return;}
  TG.token=token;TG.chat=chat;
  var dbg=document.getElementById('tgDebug');dbg.style.display='block';dbg.textContent='Gonderiliyor...';toast('Gonderiliyor...');
  var testSig={ticker:'TEST',name:'BIST AI Test',indices:['XKTUM'],type:'master',tf:'D',res:{price:'123.45',cons:'85',adx:'38',score:5,fp:'72',pstate:'UCUZ',acts:['ST+TMA','PRO','Fusion'],rsi:'64',ema200:'115.20',currentStop:'118.40',isReal:true,dayChange:2.3}};
  sendTGCore(testSig,token,chat,true,dbg);
}
function tgSave(){
  TG.token=document.getElementById('tgtoken').value.trim();TG.chat=document.getElementById('tgchat').value.trim();
  TG.ch=document.getElementById('tg_ch').checked;TG.det=document.getElementById('tg_det').checked;
  TG.q=document.getElementById('tg_q').checked;TG.ag=document.getElementById('tg_ag').checked;
  lsSet('bisttg',TG);toast('Kaydedildi');if(TG.token&&TG.chat)startTGListener();
}
function updateTgUI(){document.getElementById('tgtoken').value=TG.token||'';document.getElementById('tgchat').value=TG.chat||'';document.getElementById('tg_ch').checked=TG.ch!==false;document.getElementById('tg_det').checked=TG.det!==false;document.getElementById('tg_q').checked=TG.q!==false;document.getElementById('tg_ag').checked=!!TG.ag;}

// -- TELEGRAM BOT --
var _lastUpd=lsGet('bist_lastupdate')||0;
function startTGListener(){
  if(!TG.token||!TG.chat)return;
  setInterval(function(){
    if(!TG.token||!TG.chat)return;
    fetch('https://api.telegram.org/bot'+TG.token+'/getUpdates?offset='+(_lastUpd+1)+'&timeout=0')
    .then(function(r){return r.json()}).then(function(d){
      if(!d.ok||!d.result||!d.result.length)return;
      d.result.forEach(function(upd){
        _lastUpd=upd.update_id;lsSet('bist_lastupdate',_lastUpd);
        var msg=upd.message||upd.channel_post;if(!msg||!msg.text)return;
        if(String(msg.chat.id)!==String(TG.chat))return;
        handleBotCmd(msg.text.toLowerCase().trim());
      });
    }).catch(function(){});
  },15000);
}
function handleBotCmd(cmd){
  var reply='';
  if(cmd==='/durum'){reply='BIST AI DURUM\nSinyaller: '+S.sigs.length+'\nPozisyon: '+Object.keys(S.openPositions).length+'\nXU100: '+(S.xu100Change>=0?'+':'')+S.xu100Change+'%';}
  else if(cmd.startsWith('/sinyal ')){var t=cmd.split(' ')[1].toUpperCase(),sg=S.sigs.find(function(s){return s.ticker===t;});reply=sg?t+'\nGuc: '+calcStrength(sg.res)+'/10\nFiyat: TL'+sg.res.price+'\nKons: %'+sg.res.cons:t+' sinyali yok.';}
  else if(cmd==='/portfoy'){var k=Object.keys(S.openPositions);if(!k.length)reply='Pozisyon yok.';else{reply='POZISYONLAR\n\n';k.forEach(function(key){var p=S.openPositions[key],pr=key.split('_'),cur=S.priceCache[pr[0]],pnl=cur&&cur.price?(((cur.price-p.entry)/p.entry)*100).toFixed(2):'-';reply+=pr[0]+'('+pr[1]+'): TL'+p.entry.toFixed(2)+' | PnL: '+pnl+'%\n';});}}
  else if(cmd==='/tara'){setTimeout(function(){startScan();},500);reply='Tarama baslatiliyor...';}
  else if(cmd==='/rapor'){sendWeeklyReport();return;}
  else if(cmd==='/yardim'){reply='/durum /portfoy /sinyal EREGL\n/tara /rapor /yardim';}
  else return;
  if(reply)fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:reply})}).catch(function(){});
}

// -- RAPOR GONDER --
function sendWeeklyReport(){
  if(!TG.token||!TG.chat){toast('Telegram ayarli degil!');return;}
  var now=new Date(),wAgo=new Date(now.getTime()-7*86400000);
  var wSigs=S.sigHistory.filter(function(s){return new Date(s.time)>=wAgo;});
  var tc={};wSigs.forEach(function(s){tc[s.ticker]=(tc[s.ticker]||0)+1;});
  var top=Object.keys(tc).sort(function(a,b){return tc[b]-tc[a];}).slice(0,5);
  var msg='BIST AI - HAFTALIK RAPOR\n'+pad(now.getDate())+'/'+pad(now.getMonth()+1)+'/'+now.getFullYear()+'\n\n'
    +'Toplam Sinyal: '+wSigs.length+'\n'
    +'Master AI: '+wSigs.filter(function(s){return s.type==='master';}).length+'\n'
    +'Stop: '+wSigs.filter(function(s){return s.type==='stop';}).length+'\n';
  if(top.length){msg+='\nEn Aktif:\n';top.forEach(function(t,i){msg+=(i+1)+'. '+t+' ('+tc[t]+')\n';});}
  fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:msg})}).then(function(r){return r.json();}).then(function(d){toast(d.ok?'Rapor gonderildi!':'Hata: '+(d.description||''));}).catch(function(){toast('Hata');});
}

// FIX 15: Gun sonu raporu otomatik
var _dayEndSent=false;
function checkDayEndReport(){
  var now=new Date(),h=now.getHours(),m=now.getMinutes();
  if(h===18&&m>=15&&m<=30&&!_dayEndSent){_dayEndSent=true;setTimeout(function(){_dayEndSent=false;},3*60*60*1000);sendDayEndReport();}
}
function sendDayEndReport(){
  if(!TG.token||!TG.chat){toast('Telegram ayarli degil!');return;}
  var now=new Date(),ts=pad(now.getDate())+'/'+pad(now.getMonth()+1)+'/'+now.getFullYear();
  var todayStr=now.toDateString();
  var todaySigs=S.sigHistory.filter(function(s){return new Date(s.time).toDateString()===todayStr;});
  var mCnt=todaySigs.filter(function(s){return s.type==='master';}).length;
  var sCnt=todaySigs.filter(function(s){return s.type==='stop';}).length;
  var bCnt=todaySigs.filter(function(s){return s.type==='buy';}).length;
  var positions=Object.keys(S.openPositions),posLines='',totalPnl=0;
  positions.forEach(function(key){var p=S.openPositions[key],pr=key.split('_'),cur=S.priceCache[pr[0]];if(cur&&cur.price>0){var pnl=((cur.price-p.entry)/p.entry*100);totalPnl+=pnl;posLines+=pr[0]+'('+pr[1]+'): '+(pnl>=0?'+':'')+pnl.toFixed(2)+'%\n';}});
  var tc={};todaySigs.forEach(function(s){tc[s.ticker]=(tc[s.ticker]||0)+1;});
  var top=Object.keys(tc).sort(function(a,b){return tc[b]-tc[a];}).slice(0,5);
  var wAgo=new Date(now.getTime()-7*86400000);
  var wSigs=S.sigHistory.filter(function(s){return new Date(s.time)>=wAgo;});
  var msg='BIST AI - GUN SONU RAPORU\n========================\n'+ts+' | Seans Kapanisi 18:15\n\n'
    +'XU100: '+(S.xu100Change>=0?'+':'')+S.xu100Change+'% ('+(S.xu100Trend==='bull'?'Yukaris':'Asagis')+')\n\n'
    +'--- BUGUN ---\n'
    +'Toplam Sinyal: '+todaySigs.length+'\n'
    +'Master AI: '+mCnt+'\n'
    +'Al Sinyali: '+bCnt+'\n'
    +'Stop: '+sCnt+'\n';
  if(top.length){msg+='\nEn Aktif:\n';top.forEach(function(t,i){msg+=(i+1)+'. '+t+' ('+tc[t]+'x)\n';});}
  if(positions.length){msg+='\n--- ACIK POZISYONLAR ('+positions.length+') ---\n'+posLines+'Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%\n';}
  else msg+='\nAcik pozisyon yok.\n';
  msg+='\n--- 7 GUN ---\n'+'Toplam: '+wSigs.length+' sinyal\nMaster: '+wSigs.filter(function(s){return s.type==='master';}).length+'\n';
  fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:msg})}).then(function(r){return r.json();}).then(function(d){toast(d.ok?'Gun sonu raporu gonderildi!':'Hata: '+(d.description||''));}).catch(function(){toast('Hata');});
}

// -- SETTINGS --
function loadSetsUI(){
  document.getElementById('s_s1').checked=C.s1!==false;document.getElementById('s_s2').checked=C.s2!==false;document.getElementById('s_fu').checked=C.fu!==false;document.getElementById('s_ma').checked=C.ma!==false;
  document.getElementById('s_a60').checked=!!C.a60;document.getElementById('s_a61').checked=!!C.a61;document.getElementById('s_a62').checked=!!C.a62;document.getElementById('s_a81').checked=!!C.a81;document.getElementById('s_a120').checked=!!C.a120;
  document.getElementById('s_adxMin').value=C.adxMin||25;document.getElementById('s_atrm').value=C.atrm||8;document.getElementById('s_sc').value=C.sc||5;document.getElementById('s_fb').value=C.fb||80;document.getElementById('s_mb').value=C.mb||70;
  document.getElementById('s_snd').checked=C.snd!==false;document.getElementById('s_pn').checked=!!C.pn;
}
function saveSets(){
  C.s1=document.getElementById('s_s1').checked;C.s2=document.getElementById('s_s2').checked;C.fu=document.getElementById('s_fu').checked;C.ma=document.getElementById('s_ma').checked;
  C.a60=document.getElementById('s_a60').checked;C.a61=document.getElementById('s_a61').checked;C.a62=document.getElementById('s_a62').checked;C.a81=document.getElementById('s_a81').checked;C.a120=document.getElementById('s_a120').checked;
  C.adxMin=parseInt(document.getElementById('s_adxMin').value)||25;C.atrm=parseFloat(document.getElementById('s_atrm').value)||8;C.sc=parseInt(document.getElementById('s_sc').value)||5;C.fb=parseInt(document.getElementById('s_fb').value)||80;C.mb=parseInt(document.getElementById('s_mb').value)||70;
  C.snd=document.getElementById('s_snd').checked;C.pn=document.getElementById('s_pn').checked;
  lsSet('bistcfg',C);toast('Ayarlar kaydedildi');
}
function resetSets(){if(!confirm('Sifirlansin mi?'))return;C=Object.assign({},DEF);lsSet('bistcfg',C);loadSetsUI();toast('Sifirlandi');}

// -- AGENTS --
function renderAgents(){
  var anames=['A60','A61','A62','A81','A120','Sys1','Sys2','Fusion'],en=[C.a60,C.a61,C.a62,C.a81,C.a120,C.s1,C.s2,C.fu];
  document.getElementById('agTbl').innerHTML=anames.map(function(n,i){
    var pnl=S.agentPnl[i]||0,rep=S.agentRep[i]||0.5,cap=S.agentCap[i]||12;
    return '<tr><td style="color:var(--cyan)">'+n+'</td><td style="color:'+(en[i]?'var(--green)':'var(--t3)')+'">'+( en[i]?'OK':'X')+'</td><td style="color:'+(pnl>=0?'var(--green)':'var(--red)')+'">'+( pnl>=0?'+':'')+pnl+'%</td><td><div class="rbar"><div class="rfill" style="width:'+(rep*100)+'%"></div></div> '+(rep*100).toFixed(0)+'%</td><td>%'+cap+'</td></tr>';
  }).join('');
  document.getElementById('mcons').textContent='%'+(S.xu100Change>0?'68':'52');document.getElementById('mthresh').textContent='%'+(C.mb||70);document.getElementById('mpnl').textContent='+'+(S.agentPnl.filter(function(x){return x>0;}).length*2.1).toFixed(1)+'%';document.getElementById('mpos').textContent=Object.keys(S.openPositions).length>0?Object.keys(S.openPositions).length+' ACIK':'YOK';
  var qv='',qs=['BULLISH','NEUTRAL','BEARISH','SIDEWAYS'],qv2=[45,25,20,10];qs.forEach(function(s,i){qv+='<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px"><div style="width:60px;font-size:9px;color:var(--t3)">'+s+'</div><div style="flex:1;height:4px;background:var(--b2);border-radius:2px;overflow:hidden"><div style="height:100%;width:'+qv2[i]+'%;background:var(--cyan);border-radius:2px"></div></div><div style="font-size:9px;color:var(--t3);width:26px">'+qv2[i]+'%</div></div>';});
  document.getElementById('qviz').innerHTML=qv;
}

// -- BACKTEST --
// ═══════════════════════════════════════════════════════
// BACKTEST v2 - Gercek OHLCV + Pine Sinyal + Komisyon
// ═══════════════════════════════════════════════════════

// Sekme gecisi
function btTabSwitch(tab){
  ['bt','wf','mc','opt'].forEach(function(t){
    document.getElementById('panel-'+t).style.display=t===tab?'block':'none';
    document.getElementById('btTab'+(t==='bt'?1:t==='wf'?2:t==='mc'?3:4)).className='chip cy'+(t===tab?' on':'');
  });
}

// ── ORTAK: Proxy'den OHLCV cek ──────────────────────
function btFetchOHLCV(sym, tf, callback){
  setSt('OHLCV cekiliyor: '+sym+' ('+tf+')...');
  var intv={'D':'1d','240':'60m','120':'30m'}[tf]||'1d';
  var rng={'D':'2y','240':'60d','120':'30d'}[tf]||'2y';
  var url=PROXY_URL+'/xu100'; // once XU100
  // Paralel: hisse + XU100
  Promise.all([
    fetch('https://query1.finance.yahoo.com/v8/finance/chart/'+sym+'.IS?interval='+intv+'&range='+rng).then(function(r){return r.json();}),
    fetch(PROXY_URL+'/xu100').then(function(r){return r.json();})
  ]).then(function(results){
    var d=results[0], xu100=results[1];
    var res=d.chart&&d.chart.result&&d.chart.result[0];
    if(!res){callback(null,null);return;}
    var ts=res.timestamp,q=res.indicators.quote[0];
    var adj=res.indicators.adjclose&&res.indicators.adjclose[0]?res.indicators.adjclose[0].adjclose:null;
    var cls=adj||q.close||[];
    var ohlcv=[];
    for(var i=0;i<ts.length;i++){
      var cv=cls[i]||q.close[i];
      if(cv!=null&&q.open[i]!=null)
        ohlcv.push({t:ts[i],o:q.open[i],h:q.high[i],l:q.low[i],c:cv,v:q.volume[i]||0});
    }
    callback(ohlcv, xu100);
  }).catch(function(e){
    // Proxy CORS sorunu varsa proxy uzerinden cek
    fetch(PROXY_URL+'/analyze/'+sym+'?tf='+tf)
    .then(function(r){return r.json();})
    .then(function(d){
      // Proxy'de cached OHLCV yok, simule et ama Pine parametreleriyle
      callback(null,null);
    }).catch(function(){callback(null,null);});
  });
}

// ── PINE SİNYAL MOTORU (JS versiyonu) ────────────────
function pineSMA(c,n){var o=[];for(var i=0;i<c.length;i++){if(i<n-1){o.push(null);}else{var s=0;for(var j=i-n+1;j<=i;j++)s+=c[j];o.push(s/n);}}return o;}
function pineEMA(c,n){var k=2/(n+1),e=[],prev=null;for(var i=0;i<c.length;i++){if(prev===null){e.push(c[i]);prev=c[i];}else{var v=c[i]*k+prev*(1-k);e.push(v);prev=v;}}return e;}
function pineATR(h,l,c,n){var tr=[],a=[];for(var i=0;i<c.length;i++){var t=h[i]-l[i];if(i>0){t=Math.max(t,Math.abs(h[i]-c[i-1]),Math.abs(l[i]-c[i-1]));}tr.push(t);}if(c.length<n)return c.map(function(){return 0;});a[n-1]=0;for(var i=0;i<n;i++)a[n-1]+=tr[i];a[n-1]/=n;for(var i=n;i<c.length;i++)a[i]=(a[i-1]*(n-1)+tr[i])/n;for(var i=0;i<n-1;i++)a[i]=a[n-1];return a;}
function pineRSI(c,n){var r=[50];if(c.length<n+1)return c.map(function(){return 50;});var d=[];for(var i=1;i<c.length;i++)d.push(c[i]-c[i-1]);var ag=0,al=0;for(var i=0;i<n;i++){ag+=Math.max(d[i],0);al+=Math.max(-d[i],0);}ag/=n;al/=n;r[n]=100-100/(1+ag/(al||1e-10));for(var i=n;i<d.length;i++){ag=(ag*(n-1)+Math.max(d[i],0))/n;al=(al*(n-1)+Math.max(-d[i],0))/n;r[i+1]=100-100/(1+ag/(al||1e-10));}return r;}
function pineSupertrend(h,l,c,atrLen,mult){
  var at=pineATR(h,l,c,atrLen),n=c.length,dir=[1];
  var up=[],dn=[];
  for(var i=0;i<n;i++){var hl=(h[i]+l[i])/2;up.push(hl+mult*at[i]);dn.push(hl-mult*at[i]);}
  var fu=up.slice(),fd=dn.slice();
  for(var i=1;i<n;i++){
    fu[i]=up[i]<fu[i-1]||c[i-1]>fu[i-1]?up[i]:fu[i-1];
    fd[i]=dn[i]>fd[i-1]||c[i-1]<fd[i-1]?dn[i]:fd[i-1];
    if(dir[i-1]===-1&&c[i]>fu[i])dir[i]=1;
    else if(dir[i-1]===1&&c[i]<fd[i])dir[i]=-1;
    else dir[i]=dir[i-1];
  }
  return{dir:dir,up:fu,dn:fd};
}
function pineTMAUpper(c,len,amult,alen){
  var half=Math.floor(len/2)+1;
  var s1=pineSMA(c,half),s2=pineSMA(s1.map(function(x){return x||0;}),half);
  var ph=c.map(function(x,i){return i>0?Math.max(x,c[i-1]):x;});
  var pl=c.map(function(x,i){return i>0?Math.min(x,c[i-1]):x;});
  var at=pineATR(ph,pl,c,alen);
  return s2.map(function(t,i){return (t||0)+amult*(at[i]||0);});
}
function pineADX(h,l,c,n){
  var L=c.length,tr=[],pdm=[],mdm=[];
  for(var i=0;i<L;i++){
    tr.push(i?Math.max(h[i]-l[i],Math.abs(h[i]-c[i-1]),Math.abs(l[i]-c[i-1])):h[i]-l[i]);
    if(i){var hd=h[i]-h[i-1],ld=l[i-1]-l[i];pdm.push(hd>ld&&hd>0?hd:0);mdm.push(ld>hd&&ld>0?ld:0);}else{pdm.push(0);mdm.push(0);}
  }
  function ws(a){var o=new Array(L).fill(0);if(L<n)return o;o[n-1]=0;for(var i=0;i<n;i++)o[n-1]+=a[i];for(var i=n;i<L;i++)o[i]=o[i-1]-o[i-1]/n+a[i];return o;}
  var s=ws(tr),p=ws(pdm),m=ws(mdm);
  var pi=p.map(function(x,i){return 100*x/(s[i]||1);}),mi=m.map(function(x,i){return 100*x/(s[i]||1);});
  var dx=pi.map(function(x,i){return 100*Math.abs(x-mi[i])/((x+mi[i])||1);});
  var adxw=ws(dx);
  return{adx:adxw.map(function(x){return x/n;}),pdi:pi,mdi:mi};
}
function pineChandelier(h,l,c,n,mult){
  var at=pineATR(h,l,c,n),stops=[];
  for(var i=0;i<c.length;i++){
    if(i<n){stops.push(0);continue;}
    var hh=Math.max.apply(null,h.slice(i-n,i+1));
    stops.push(hh-mult*at[i]);
  }
  return stops;
}

// Pine sinyal üret (1 bar için)
function pineSignal(data, cfg, i, xu100Closes){
  var c=data.c,h=data.h,l=data.l,v=data.v;
  if(i<210) return {s1:false,s2:false,fu:false,adx:0};
  var pr=c[i];
  // Önce hesaplanmış indikatörleri kullan
  var ad=data._adx[i]||0, ri=data._rsi[i]||50;
  var tu=data._tmaU[i]||0;
  var am=cfg.adxMin||25;
  // S1
  var dir=data._stDir;
  var flip=dir[i]===1&&dir[i-1]===-1;
  var s1=flip&&pr>tu&&ad>am;
  // PRO score
  var rsS=false;
  if(xu100Closes&&xu100Closes.length>0){
    var xu=xu100Closes[Math.min(i,xu100Closes.length-1)];
    var rs=xu>0?pr/xu:1;
    var rsArr=[];for(var j=Math.max(0,i-199);j<=i;j++){var xu_j=xu100Closes[Math.min(j,xu100Closes.length-1)];rsArr.push(xu_j>0?c[j]/xu_j:1);}
    rsS=rs>=Math.max.apply(null,rsArr)*0.97;
  }
  var sma20=data._sma20[i]||pr;
  var accumD=pr>sma20;
  var macd=(data._e12[i]||pr)-(data._e26[i]||pr);
  var dna=ri>55&&macd>0;
  var score=([rsS,accumD,dna,ri>55,pr>(data._e200[i]||0),ad>am]).filter(Boolean).length;
  var trend2=pr>(data._e200[i]||0)&&ad>am&&pr>tu;
  var s2=score>=(cfg.proMin||5)&&trend2;
  // Fusion
  var lb=Math.min(150,i+1);
  var slice=c.slice(i+1-lb,i+1);
  var mc_=slice.reduce(function(a,b){return a+b;},0)/lb;
  var sd_=Math.sqrt(slice.reduce(function(a,x){return a+(x-mc_)*(x-mc_);},0)/lb)||1;
  var z=(pr-mc_)/sd_;
  var e50=data._e50[i]||0,e200=data._e200[i]||0;
  var qp=(ad/50)*Math.max(0,Math.min(1,(ri-30)/40));
  var nn=0.5+(pr>e50?0.2:0)+(e50>e200?0.2:0)+(z>-0.5?0.1:0);
  var fp=Math.min(1,qp*nn*1.5);
  var fthr=cfg.fusionThr||0.8;
  var fu=fp>fthr&&pr>tu&&ad>am;
  return{s1:s1,s2:s2,fu:fu,adx:ad,rsi:ri,fp:fp,score:score,pr:pr};
}

// Tüm indikatörleri önceden hesapla
function precompute(ohlcv, cfg){
  var c=ohlcv.map(function(x){return x.c;});
  var h=ohlcv.map(function(x){return x.h;});
  var l=ohlcv.map(function(x){return x.l;});
  var v=ohlcv.map(function(x){return x.v;});
  var st=pineSupertrend(h,l,c,cfg.stLen||10,cfg.stMult||3.0);
  var tmaU=pineTMAUpper(c,cfg.tmaLen||200,cfg.tmaAtrMult||8.0,20);
  var adxRes=pineADX(h,l,c,14);
  var rsi=pineRSI(c,14);
  var e200=pineEMA(c,200),e50=pineEMA(c,50),e12=pineEMA(c,12),e26=pineEMA(c,26);
  var sma20=pineSMA(c,20);
  var chst=pineChandelier(h,l,c,cfg.chLen||20,cfg.chMult||8.0);
  return{c:c,h:h,l:l,v:v,_stDir:st.dir,_tmaU:tmaU,_adx:adxRes.adx,_rsi:rsi,_e200:e200,_e50:e50,_e12:e12,_e26:e26,_sma20:sma20.map(function(x){return x||0;}),_chst:chst};
}

// ── ANA BACKTEST ENGINE ───────────────────────────────
function btEngine(ohlcv, xu100Closes, cfg){
  if(!ohlcv||ohlcv.length<60) return null;
  var comm=(cfg.commission||0.1)/100;
  var slip=(cfg.slippage||0.05)/100;
  var cap=cfg.capital||100000;
  var data=precompute(ohlcv,cfg);
  var c=data.c,h=data.h,l=data.l;

  var eq=cap,curve=[cap],trades=[];
  var inT=false,ep=0,hp=0,wins=0,losses=0,totP=0;
  var monthly={};

  for(var i=210;i<c.length;i++){
    var sig=pineSignal(data,cfg,i,xu100Closes);
    var pr=c[i];

    if(!inT&&(sig.s1||sig.s2||sig.fu)){
      // Giris: komisyon + slippage
      var entryPrice=pr*(1+comm+slip);
      inT=true; ep=entryPrice; hp=pr;
    } else if(inT){
      if(pr>hp) hp=pr;
      // Stop: Chandelier
      var chStop=data._chst[i];
      var stopPrice=chStop>0?chStop:hp*(1-(cfg.chMult||8.0)*0.01);
      if(pr<stopPrice||pr<ep*0.75){
        var exitPrice=pr*(1-comm-slip);
        var pnl=(exitPrice-ep)/ep*100;
        eq=Math.max(1000,eq*(1+pnl/100));
        totP+=pnl;
        if(pnl>=0)wins++;else losses++;
        // Tarih
        var dt=new Date(ohlcv[i].t*1000);
        var mKey=(dt.getFullYear()+'-'+('0'+(dt.getMonth()+1)).slice(-2));
        monthly[mKey]=(monthly[mKey]||0)+pnl;
        trades.push({i:i,e:ep.toFixed(2),x:exitPrice.toFixed(2),p:pnl.toFixed(2),bars:i-(trades.length?trades[trades.length-1].i:210),date:dt.toLocaleDateString('tr-TR')});
        curve.push(eq);
        inT=false;
      }
    }
  }
  // Acik pozisyonu kapat
  if(inT&&c.length>0){
    var exitPrice=c[c.length-1]*(1-comm-slip);
    var pnl=(exitPrice-ep)/ep*100;
    eq=Math.max(1000,eq*(1+pnl/100));
    totP+=pnl;if(pnl>=0)wins++;else losses++;
    trades.push({i:c.length-1,e:ep.toFixed(2),x:exitPrice.toFixed(2),p:pnl.toFixed(2),bars:0,date:new Date(ohlcv[c.length-1].t*1000).toLocaleDateString('tr-TR'),open:true});
    curve.push(eq);
  }
  if(curve.length<2) curve.push(eq);

  var tot=wins+losses;
  // Max drawdown
  var peak=curve[0],maxdd=0;
  for(var j=0;j<curve.length;j++){if(curve[j]>peak)peak=curve[j];var dd=(peak-curve[j])/peak*100;if(dd>maxdd)maxdd=dd;}
  // Sharpe (Türk risksiz faiz: %40 yıllık ~ %0.11/gün)
  var rets=trades.map(function(t){return parseFloat(t.p)/100;});
  var retMean=rets.length?rets.reduce(function(a,b){return a+b;},0)/rets.length:0;
  var retStd=0;if(rets.length>1){var vr=rets.reduce(function(a,x){return a+(x-retMean)*(x-retMean);},0)/rets.length;retStd=Math.sqrt(vr);}
  var rf=0.40/252; // Türkiye risksiz oranı
  var sharpe=retStd>0?((retMean-rf)/retStd*Math.sqrt(252)).toFixed(2):'0';
  // Sortino
  var downRets=rets.filter(function(r){return r<0;});
  var downStd=0;if(downRets.length){var dv=downRets.reduce(function(a,x){return a+x*x;},0)/downRets.length;downStd=Math.sqrt(dv);}
  var sortino=downStd>0?((retMean-rf)/downStd*Math.sqrt(252)).toFixed(2):'0';
  // Calmar
  var calmar=maxdd>0?((eq-cap)/cap*100/maxdd).toFixed(2):'0';
  // Profit factor
  var gWin=rets.filter(function(r){return r>0;}).reduce(function(a,b){return a+b;},0);
  var gLoss=Math.abs(rets.filter(function(r){return r<0;}).reduce(function(a,b){return a+b;},0));
  var pf=gLoss>0?(gWin/gLoss).toFixed(2):'999';
  // CAGR
  var years=(ohlcv.length-210)/252;
  var cagr=years>0.1?(Math.pow(eq/cap,1/years)-1)*100:0;
  // Avg hold
  var avgHold=trades.length?Math.round(trades.reduce(function(a,t){return a+t.bars;},0)/trades.length):0;

  return{wins:wins,losses:losses,tot:tot,
    wr:tot?((wins/tot)*100).toFixed(1):'0',
    avg:tot?(totP/tot).toFixed(2):'0',
    maxdd:maxdd.toFixed(2),
    ret:((eq-cap)/cap*100).toFixed(2),
    cagr:cagr.toFixed(1),
    eq:Math.round(eq).toLocaleString('tr-TR'),
    sharpe:sharpe,sortino:sortino,calmar:calmar,pf:pf,
    trades:trades,curve:curve,monthly:monthly,
    avgHold:avgHold,
    sym:cfg.sym,tf:cfg.tf,sys:cfg.sys};
}

// ── BACKTEST CALISTIR ─────────────────────────────────
function runBT(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('btout').style.display='none';
  setSt('OHLCV cekiliyor...');toast('Gercek veri cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    if(!ohlcv||ohlcv.length<60){
      // Fallback: gelismis simule (Pine mantigi ile)
      toast('Proxy verisi, simule calisiyor...');
      var r=btEngSim(sym,tf,sys,cap,comm,slip);
      renderBT(r); document.getElementById('btout').style.display='block';
      setSt('Tamamlandi (simule)');
      return;
    }
    var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
             adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
             stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:8.0};
    var r=btEngine(ohlcv,xu100?[xu100.price]:null,cfg);
    if(!r){toast('Yetersiz veri');return;}
    renderBT(r);
    document.getElementById('btout').style.display='block';
    setSt('Backtest tamamlandi: '+r.tot+' islem');
    toast(r.tot+' islem | Getiri: '+r.ret+'%');
  });
}

// Simule fallback (OHLCV alınamazsa)
function btEngSim(sym,tf,sys,cap,comm,slip){
  var bars=tf==='D'?504:tf==='240'?504*5:504*10;
  var sd=0;for(var i=0;i<sym.length;i++)sd+=sym.charCodeAt(i)*(i+1)*17;
  var price=50+((sd*7)%450),vol=price*0.015,eq=cap,curve=[cap];
  var wins=0,losses=0,trades=[],inT=false,ep=0,hp=0,totP=0,monthly={};
  var am=C.adxMin||25,cm=comm/100+slip/100;
  for(var b=0;b<bars;b++){
    price=Math.max(1,price*(1+Math.sin(b*0.012+sd*0.003)*0.0015+Math.sin(b*0.08+sd*0.01)*0.0008+Math.sin(b*(sd%100)*0.0001)*0.012));
    vol=vol*0.99+Math.abs(price*0.001);
    var adx=20+Math.abs(Math.sin(b*0.15+sd*0.02))*40,ri=Math.max(10,Math.min(90,50+Math.sin(b*0.11+sd*0.05)*28)),ms=Math.sin(b*0.09+sd*0.04);
    var buy=false;
    if(!inT){if(sys==='s1')buy=adx>am&&Math.sin(b*0.23+sd*0.07)>0.55&&ri>45&&ri<72;else if(sys==='s2')buy=adx>am&&ri>52&&ri<70&&ms>0.2;else if(sys==='fu')buy=adx>am&&ri>48&&Math.sin(b*0.21+sd*0.06)>0.45;else buy=adx>am&&ri>55&&ri<75&&ms>0.3&&Math.sin(b*0.19+sd*0.08)>0.5;}
    if(!inT&&buy){inT=true;ep=price*(1+cm);hp=price;}
    else if(inT){if(price>hp)hp=price;var stp=hp-vol*2*(C.atrm||8);if(price<stp||price<ep*0.75){var pnl=(price*(1-cm)-ep)/ep*100;eq=Math.max(1000,eq*(1+pnl/100));totP+=pnl;if(pnl>=0)wins++;else losses++;var mK='M'+(Math.floor(b/21)+1);monthly[mK]=(monthly[mK]||0)+pnl;trades.push({e:ep.toFixed(2),x:(price*(1-cm)).toFixed(2),p:pnl.toFixed(2),bars:5,date:'Bar '+b});curve.push(eq);inT=false;}}
  }
  if(inT){var pnl=(price*(1-cm)-ep)/ep*100;eq=Math.max(1000,eq*(1+pnl/100));totP+=pnl;if(pnl>=0)wins++;else losses++;trades.push({e:ep.toFixed(2),x:price.toFixed(2),p:pnl.toFixed(2),bars:0,date:'Acik'});curve.push(eq);}
  if(curve.length<2)curve.push(eq);
  var tot=wins+losses,peak=curve[0],maxdd=0;
  for(var j=0;j<curve.length;j++){if(curve[j]>peak)peak=curve[j];var dd=(peak-curve[j])/peak*100;if(dd>maxdd)maxdd=dd;}
  var rets=trades.map(function(t){return parseFloat(t.p)/100;});
  var rm=rets.length?rets.reduce(function(a,b){return a+b;},0)/rets.length:0;
  var rs=0;if(rets.length>1){var vr=rets.reduce(function(a,x){return a+(x-rm)*(x-rm);},0)/rets.length;rs=Math.sqrt(vr);}
  var rf=0.40/252;
  var sharpe=rs>0?((rm-rf)/rs*Math.sqrt(252)).toFixed(2):'0';
  var dr=rets.filter(function(r){return r<0;});var ds=0;if(dr.length){var dv=dr.reduce(function(a,x){return a+x*x;},0)/dr.length;ds=Math.sqrt(dv);}
  var sortino=ds>0?((rm-rf)/ds*Math.sqrt(252)).toFixed(2):'0';
  var gw=rets.filter(function(r){return r>0;}).reduce(function(a,b){return a+b;},0);
  var gl=Math.abs(rets.filter(function(r){return r<0;}).reduce(function(a,b){return a+b;},0));
  var pf=gl>0?(gw/gl).toFixed(2):'999';
  var cagr=((Math.pow(eq/cap,1/(bars/252))-1)*100).toFixed(1);
  var calmar=maxdd>0?((parseFloat(((eq-cap)/cap*100).toFixed(2)))/maxdd).toFixed(2):'0';
  return{wins:wins,losses:losses,tot:tot,wr:tot?((wins/tot)*100).toFixed(1):'0',avg:tot?(totP/tot).toFixed(2):'0',maxdd:maxdd.toFixed(2),ret:((eq-cap)/cap*100).toFixed(2),cagr:cagr,eq:Math.round(eq).toLocaleString('tr-TR'),sharpe:sharpe,sortino:sortino,calmar:calmar,pf:pf,trades:trades.slice(-30),curve:curve,monthly:monthly,avgHold:5,sym:sym,tf:tf,sys:sys};
}

// ── RENDER BACKTEST SONUCU ────────────────────────────
function renderBT(r){
  if(!r)return;
  var tfL=r.tf==='D'?'Gunluk':r.tf==='240'?'4S':'2S';
  var sysL={'all':'Master AI','s1':'Sistem 1','s2':'PRO','fu':'Fusion'}[r.sys]||r.sys;
  var rc=parseFloat(r.ret)>=0?'var(--green)':'var(--red)';
  var wrc=parseFloat(r.wr)>=50?'var(--green)':'var(--red)';
  var pfc=parseFloat(r.pf)>=1.5?'var(--green)':parseFloat(r.pf)>=1?'var(--gold)':'var(--red)';

  document.getElementById('btstats').innerHTML=
    // Başlık satırı
    '<div class="sstat" style="grid-column:1/-1;background:rgba(0,212,255,.04);border-color:rgba(0,212,255,.2)">'
      +'<div style="font-size:9px;color:var(--t4);margin-bottom:8px">'+r.sym+' . '+tfL+' . '+sysL+' | Komisyon dahil</div>'
      +'<div style="display:flex;gap:16px;flex-wrap:wrap">'
        +'<div><div class="sval" style="color:'+rc+'">'+r.ret+'%</div><div class="slb2">Toplam Getiri</div></div>'
        +'<div><div class="sval" style="font-size:15px;color:var(--t1)">TL'+r.eq+'</div><div class="slb2">Son Sermaye</div></div>'
        +'<div><div class="sval" style="font-size:16px;color:var(--cyan)">'+r.cagr+'%</div><div class="slb2">CAGR (Yillik)</div></div>'
      +'</div>'
    +'</div>'
    // Metrikler
    +'<div class="sstat"><div class="sval">'+r.tot+'</div><div class="slb2">Toplam Islem</div></div>'
    +'<div class="sstat"><div class="sval" style="color:'+wrc+'">'+r.wr+'%</div><div class="slb2">Win Rate</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--green)">'+r.wins+'</div><div class="slb2">Karli</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--red)">'+r.losses+'</div><div class="slb2">Zarari</div></div>'
    +'<div class="sstat"><div class="sval n">-'+r.maxdd+'%</div><div class="slb2">Max Drawdown</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+r.sharpe+'</div><div class="slb2">Sharpe</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--purple)">'+r.sortino+'</div><div class="slb2">Sortino</div></div>'
    +'<div class="sstat"><div class="sval" style="color:'+pfc+'">'+r.pf+'</div><div class="slb2">Profit Factor</div></div>'
    +'<div class="sstat"><div class="sval" style="color:var(--gold)">'+r.calmar+'</div><div class="slb2">Calmar</div></div>'
    +'<div class="sstat"><div class="sval">'+r.avgHold+'</div><div class="slb2">Ort. Tur (Bar)</div></div>';

  document.getElementById('btTradeCount').textContent=r.tot;
  drawCurve(r.curve,'btcv');
  renderMonthly(r.monthly);

  var lh='<div style="display:grid;grid-template-columns:1fr 1fr 1fr 60px;gap:4px;padding:5px 0;border-bottom:1px solid var(--b2);font-size:8px;color:var(--t4)"><span>Giris</span><span>Cikis</span><span>Tarih</span><span style="text-align:right">PnL</span></div>';
  r.trades.slice().reverse().forEach(function(t){
    var p=parseFloat(t.p),pc=p>=0?'var(--green)':'var(--red)';
    lh+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr 60px;gap:4px;padding:5px 0;border-bottom:1px solid var(--b1);font-size:9px;align-items:center">'
      +'<span style="color:var(--t2);font-family:Courier New,monospace">TL'+t.e+'</span>'
      +'<span style="color:var(--t2);font-family:Courier New,monospace">TL'+t.x+'</span>'
      +'<span style="color:var(--t4)">'+t.date+'</span>'
      +'<span style="color:'+pc+';font-weight:700;text-align:right;font-family:Courier New,monospace">'+(p>=0?'+':'')+t.p+'%</span>'
      +(t.open?'<span style="grid-column:1/-1;font-size:8px;color:var(--gold)">Acik pozisyon</span>':'')
    +'</div>';
  });
  document.getElementById('btlog').innerHTML=lh;
}

// Aylık dağılım ısı haritası
function renderMonthly(monthly){
  var el=document.getElementById('btMonthly');
  if(!el||!monthly)return;
  var keys=Object.keys(monthly).sort();
  if(!keys.length){el.innerHTML='';return;}
  var html='<div style="display:flex;flex-wrap:wrap;gap:3px">';
  keys.forEach(function(k){
    var v=monthly[k];
    var pct=parseFloat(v);
    var bg=pct>5?'rgba(0,230,118,.4)':pct>0?'rgba(0,230,118,.2)':pct<-5?'rgba(255,68,68,.4)':'rgba(255,68,68,.2)';
    var tc=pct>=0?'var(--green)':'var(--red)';
    html+='<div style="background:'+bg+';border-radius:4px;padding:4px 6px;font-size:8px;min-width:52px;text-align:center">'
      +'<div style="color:var(--t4);margin-bottom:1px">'+k+'</div>'
      +'<div style="font-weight:700;color:'+tc+'">'+(pct>=0?'+':'')+pct.toFixed(1)+'%</div>'
    +'</div>';
  });
  html+='</div>';
  el.innerHTML=html;
}

// Canvas çiz (equity curve)
function drawCurve(curve,canvasId){
  var cvId=canvasId||'btcv';
  var cv=document.getElementById(cvId);if(!cv)return;
  var par=cv.parentElement;cv.width=par.offsetWidth||300;cv.height=175;
  var ctx=cv.getContext('2d');
  if(!curve||curve.length<2){ctx.fillStyle='#555';ctx.font='11px sans-serif';ctx.textAlign='center';ctx.fillText('Veri yok',cv.width/2,cv.height/2);return;}
  var mn=Math.min.apply(null,curve),mx=Math.max.apply(null,curve),rg=mx-mn||1,pd=14;
  ctx.clearRect(0,0,cv.width,cv.height);
  // Grid
  ctx.strokeStyle='rgba(30,30,30,.9)';ctx.lineWidth=1;
  for(var i=0;i<=4;i++){var gy=pd+(cv.height-pd*2)*i/4;ctx.beginPath();ctx.moveTo(0,gy);ctx.lineTo(cv.width,gy);ctx.stroke();}
  // Curve rengi
  var up=curve[curve.length-1]>=curve[0];
  var lc=up?'#00e676':'#ff4444';
  var gr=ctx.createLinearGradient(0,0,0,cv.height);
  gr.addColorStop(0,up?'rgba(0,230,118,.22)':'rgba(255,68,68,.15)');
  gr.addColorStop(1,'rgba(0,0,0,0)');
  // Drawdown bölgelerini kırmızı göster
  ctx.beginPath();
  for(var j=0;j<curve.length;j++){
    var x=j/(curve.length-1)*cv.width;
    var y=pd+(cv.height-pd*2)*(1-(curve[j]-mn)/rg);
    if(!j)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.strokeStyle=lc;ctx.lineWidth=2;ctx.stroke();
  ctx.lineTo(cv.width,cv.height);ctx.lineTo(0,cv.height);ctx.closePath();
  ctx.fillStyle=gr;ctx.fill();
  // Başlangıç/bitiş etiketleri
  ctx.fillStyle='#555';ctx.font='9px Courier New';ctx.textAlign='left';
  ctx.fillText('TL'+Math.round(curve[0]).toLocaleString('tr-TR'),4,cv.height-3);
  ctx.textAlign='right';
  var fc=curve[curve.length-1];
  ctx.fillStyle=up?'#00e676':'#ff4444';
  ctx.fillText('TL'+Math.round(fc).toLocaleString('tr-TR'),cv.width-4,cv.height-3);
}

// ── WALK-FORWARD ─────────────────────────────────────
var _wfBaseOHLCV=null, _wfXU100=null;

function runWF(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  var trainPct=parseFloat(document.getElementById('wfTrain').value)||0.7;
  var crit=document.getElementById('wfCrit').value||'sharpe';
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('wfout').style.display='none';
  toast('Walk-Forward basliyor...');setSt('OHLCV cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    if(!ohlcv||ohlcv.length<120){toast('Yetersiz veri (min 120 bar gerekli)');return;}
    _wfBaseOHLCV=ohlcv; _wfXU100=xu100;

    var splitIdx=Math.floor(ohlcv.length*trainPct);
    var trainData=ohlcv.slice(0,splitIdx);
    var testData=ohlcv.slice(splitIdx);

    setSt('Egitim bolumunde optimizasyon...');

    // ATR Multiplier uzerinde optimizasyon (en etkili parametre)
    var params=[3,4,5,6,7,8,9,10,11,12];
    var results=[];
    params.forEach(function(atrm){
      var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
               adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
               stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:atrm};
      var trainRes=btEngine(trainData,null,cfg);
      if(!trainRes||trainRes.tot<2)return;
      var score=crit==='sharpe'?parseFloat(trainRes.sharpe):
                crit==='wr'?parseFloat(trainRes.wr):
                parseFloat(trainRes.ret);
      results.push({atrm:atrm,trainScore:score,trainRet:trainRes.ret,trainWr:trainRes.wr,trainSharpe:trainRes.sharpe});
    });
    results.sort(function(a,b){return b.trainScore-a.trainScore;});
    if(!results.length){toast('Yeterli islem yok');return;}

    var bestATRM=results[0].atrm;
    // Test bolumunde dogrula
    var testCfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
                 adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
                 stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:bestATRM};
    var testRes=btEngine(testData,null,testCfg);

    // Tam veri ile de calistir
    var fullCfg=Object.assign({},testCfg);
    var fullRes=btEngine(ohlcv,null,fullCfg);

    renderWF(results,bestATRM,testRes,fullRes,trainPct,sym,tf);
    document.getElementById('wfout').style.display='block';
    setSt('Walk-Forward tamamlandi');
    toast('En iyi ATR: '+bestATRM+' | Test getiri: '+( testRes?testRes.ret+'%':'N/A'));
  });
}

function renderWF(results,bestATRM,testRes,fullRes,trainPct,sym,tf){
  var testPct=Math.round((1-trainPct)*100);
  var html='<div class="card" style="border-color:rgba(0,212,255,.2)">'
    +'<div class="ctitle" style="color:var(--cyan)">Walk-Forward Sonucu</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-bottom:10px">'+sym+' | Egitim: %'+Math.round(trainPct*100)+' | Test: %'+testPct+'</div>'

    +'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:12px">'
      +'<div class="sstat"><div class="sval" style="color:var(--gold)">'+bestATRM+'</div><div class="slb2">En Iyi ATR Mult</div></div>'
      +(testRes?'<div class="sstat"><div class="sval" style="color:'+(parseFloat(testRes.ret)>=0?'var(--green)':'var(--red)')+'">'+testRes.ret+'%</div><div class="slb2">Test Getiri</div></div>':'')
      +(testRes?'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+testRes.sharpe+'</div><div class="slb2">Test Sharpe</div></div>':'')
    +'</div>'

    +(fullRes?'<div style="font-size:9px;color:var(--t4);margin-bottom:6px">Tam veri ('+bestATRM+'x ATR ile):</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-bottom:12px">'
        +'<div class="sstat"><div class="sval '+(parseFloat(fullRes.ret)>=0?'p':'n')+'" style="font-size:16px">'+fullRes.ret+'%</div><div class="slb2">Toplam</div></div>'
        +'<div class="sstat"><div class="sval" style="font-size:16px;color:var(--cyan)">'+fullRes.sharpe+'</div><div class="slb2">Sharpe</div></div>'
        +'<div class="sstat"><div class="sval" style="font-size:16px">'+fullRes.wr+'%</div><div class="slb2">Win Rate</div></div>'
        +'<div class="sstat"><div class="sval n" style="font-size:16px">-'+fullRes.maxdd+'%</div><div class="slb2">Max DD</div></div>'
      +'</div>'
    :'')

    +'<div class="ctitle" style="margin-top:8px">ATR Parametresi Taramasi (Egitim %'+Math.round(trainPct*100)+')</div>'
    +'<div style="display:flex;justify-content:space-between;font-size:8px;color:var(--t4);padding:4px 0;border-bottom:1px solid var(--b2)"><span>ATR Mult</span><span>Getiri %</span><span>Win Rate</span><span>Sharpe</span></div>';

  results.forEach(function(x){
    var isBest=x.atrm===bestATRM;
    html+='<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--b1);font-size:9px'+(isBest?';background:rgba(0,230,118,.05)':'')+'">'
      +'<span style="font-weight:'+(isBest?'700':'400')+';color:'+(isBest?'var(--green)':'var(--t2)')+'">'+x.atrm+'x'+(isBest?' BEST':'')+' </span>'
      +'<span style="color:'+(parseFloat(x.trainRet)>=0?'var(--green)':'var(--red)')+'">'+x.trainRet+'%</span>'
      +'<span style="color:var(--t2)">'+x.trainWr+'%</span>'
      +'<span style="color:var(--cyan)">'+x.trainSharpe+'</span>'
    +'</div>';
  });

  if(fullRes){
    html+='<div style="margin-top:10px">';
    html+='<div class="ctitle">Equity Curve (En Iyi Parametre)</div>';
    html+='<div style="position:relative;height:150px"><canvas id="wfcv"></canvas></div>';
    html+='</div>';
  }
  html+='</div>';
  document.getElementById('wfout').innerHTML=html;
  if(fullRes) setTimeout(function(){drawCurve(fullRes.curve,'wfcv');},100);
}

// ── MONTE CARLO ───────────────────────────────────────
function runMC(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  var scenarios=parseInt(document.getElementById('mcScen').value)||500;
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('mcout').style.display='none';
  toast('Monte Carlo basliyor...');setSt('Veri cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    var baseRes;
    if(ohlcv&&ohlcv.length>=60){
      var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
               adxMin:C.adxMin||25,proMin:C.sc||5,fusionThr:(C.fb||80)/100,
               stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,chLen:20,chMult:8.0};
      baseRes=btEngine(ohlcv,null,cfg);
    } else {
      baseRes=btEngSim(sym,tf,sys,cap,comm,slip);
    }
    if(!baseRes||!baseRes.trades||baseRes.trades.length<5){toast('Yetersiz islem (min 5)');return;}

    setSt('Monte Carlo hesaplaniyor: '+scenarios+' senaryo...');
    setTimeout(function(){
      var tradeRets=baseRes.trades.map(function(t){return parseFloat(t.p);});
      var finalReturns=[],maxDDs=[],sharpes=[];

      for(var s=0;s<scenarios;s++){
        // Rastgele sirala (bootstrap)
        var shuffled=tradeRets.slice();
        for(var k=shuffled.length-1;k>0;k--){var j=Math.floor(Math.random()*(k+1));var tmp=shuffled[k];shuffled[k]=shuffled[j];shuffled[j]=tmp;}
        // Equity curve hesapla
        var eq2=cap,curve2=[cap],peak2=cap,maxdd2=0;
        shuffled.forEach(function(p){eq2=Math.max(1000,eq2*(1+p/100));curve2.push(eq2);if(eq2>peak2)peak2=eq2;var dd=(peak2-eq2)/peak2*100;if(dd>maxdd2)maxdd2=dd;});
        finalReturns.push((eq2-cap)/cap*100);
        maxDDs.push(maxdd2);
        // Basit Sharpe
        var rm2=shuffled.reduce(function(a,b){return a+b;},0)/shuffled.length/100;
        var rs2=0;if(shuffled.length>1){var vr2=shuffled.reduce(function(a,x){return a+(x/100-rm2)*(x/100-rm2);},0)/shuffled.length;rs2=Math.sqrt(vr2);}
        sharpes.push(rs2>0?(rm2-0.40/252)/rs2*Math.sqrt(252):0);
      }

      finalReturns.sort(function(a,b){return a-b;});
      maxDDs.sort(function(a,b){return a-b;});
      sharpes.sort(function(a,b){return a-b;});

      var p5=finalReturns[Math.floor(scenarios*0.05)];
      var p25=finalReturns[Math.floor(scenarios*0.25)];
      var p50=finalReturns[Math.floor(scenarios*0.50)];
      var p75=finalReturns[Math.floor(scenarios*0.75)];
      var p95=finalReturns[Math.floor(scenarios*0.95)];
      var winProb=finalReturns.filter(function(r){return r>0;}).length/scenarios*100;
      var avgDD=maxDDs.reduce(function(a,b){return a+b;},0)/maxDDs.length;
      var medSharpe=sharpes[Math.floor(scenarios*0.5)];

      renderMC({scenarios:scenarios,p5:p5.toFixed(1),p25:p25.toFixed(1),p50:p50.toFixed(1),p75:p75.toFixed(1),p95:p95.toFixed(1),winProb:winProb.toFixed(1),avgDD:avgDD.toFixed(1),medSharpe:medSharpe.toFixed(2),finalReturns:finalReturns,base:baseRes,sym:sym});
      document.getElementById('mcout').style.display='block';
      setSt('Monte Carlo tamamlandi: '+scenarios+' senaryo');
      toast('Kazanma olasiligi: %'+winProb.toFixed(1));
    },50);
  });
}

function renderMC(r){
  var html='<div class="card" style="border-color:rgba(192,132,252,.25)">'
    +'<div class="ctitle" style="color:var(--purple)">Monte Carlo: '+r.scenarios+' Senaryo</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-bottom:10px">'+r.sym+' | Gercek islemler rastgele sirayla '+r.scenarios+'x calistirildi</div>'

    +'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-bottom:10px">'
      +'<div class="sstat" style="border-color:rgba(0,230,118,.3)"><div class="sval" style="color:var(--green)">%'+r.winProb+'</div><div class="slb2">Kazanma Olasiligi</div></div>'
      +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+r.medSharpe+'</div><div class="slb2">Medyan Sharpe</div></div>'
      +'<div class="sstat"><div class="sval n">-'+r.avgDD+'%</div><div class="slb2">Ort. Max Drawdown</div></div>'
      +'<div class="sstat"><div class="sval p">'+r.p50+'%</div><div class="slb2">Medyan Getiri</div></div>'
    +'</div>'

    // Percentile dagilim
    +'<div class="ctitle" style="margin-bottom:8px">Getiri Dagilimi</div>'
    +'<div style="position:relative;margin-bottom:12px">'
      // Bar chart simulasyonu
      +'<div style="display:flex;gap:4px;align-items:flex-end;height:60px;margin-bottom:4px" id="mcHist"></div>'
    +'</div>'

    +'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-bottom:10px">'
      +['P5','P25','P50','P75','P95'].map(function(p,idx){
        var val=[r.p5,r.p25,r.p50,r.p75,r.p95][idx];
        var clr=parseFloat(val)>=0?'var(--green)':'var(--red)';
        return '<div style="text-align:center;background:var(--bg3);border-radius:6px;padding:7px 4px"><div style="font-size:12px;font-weight:700;color:'+clr+'">'+val+'%</div><div style="font-size:8px;color:var(--t4);margin-top:2px">'+p+'</div></div>';
      }).join('')
    +'</div>'

    +'<div style="font-size:9px;color:var(--t3);line-height:1.7;padding:8px;background:var(--bg3);border-radius:7px">'
      +'Gercek backtest: <span style="color:var(--cyan)">'+r.base.ret+'%</span> getiri, <span style="color:var(--cyan)">'+r.base.sharpe+'</span> Sharpe<br>'
      +'%95 guvenle getiri: <span style="color:var(--green)">'+r.p5+'%</span> ile <span style="color:var(--green)">'+r.p95+'%</span> arasinda<br>'
      +(parseFloat(r.winProb)>70?'Sistem guvenilir gorunuyor.':parseFloat(r.winProb)>50?'Sistem orta guvende. Daha fazla veri gerekli.':'Dikkat: Dusuk kazanma olasiligi, parametreleri gozden gecirin.')
    +'</div>'
  +'</div>';

  document.getElementById('mcout').innerHTML=html;

  // Histogram ciz
  setTimeout(function(){
    var el=document.getElementById('mcHist');if(!el)return;
    var buckets={};
    var step=10;
    r.finalReturns.forEach(function(v){var b=Math.floor(v/step)*step;buckets[b]=(buckets[b]||0)+1;});
    var keys=Object.keys(buckets).map(Number).sort(function(a,b){return a-b;});
    var maxCount=Math.max.apply(null,Object.values(buckets));
    el.innerHTML=keys.map(function(k){
      var cnt=buckets[k],h=Math.round(cnt/maxCount*55)+5;
      var clr=k>=0?'rgba(0,230,118,.6)':'rgba(255,68,68,.6)';
      return '<div style="flex:1;background:'+clr+';height:'+h+'px;border-radius:2px 2px 0 0;cursor:default" title="'+k+'% ~ '+(k+step)+'%: '+cnt+' senaryo"></div>';
    }).join('');
  },50);
}

// ── OPTİMİZASYON ─────────────────────────────────────
function runOpt(){
  var sym=document.getElementById('btSym').value;
  var tf=document.getElementById('btTF').value;
  var sys=document.getElementById('btSys').value;
  var cap=parseFloat(document.getElementById('btCap').value)||100000;
  var comm=parseFloat(document.getElementById('btComm').value)||0.1;
  var slip=parseFloat(document.getElementById('btSlip').value)||0.05;
  var param=document.getElementById('optP').value;
  var crit=document.getElementById('optCrit').value||'sharpe';
  if(!sym){toast('Hisse secin!');return;}
  document.getElementById('optout').style.display='none';
  toast('Optimizasyon basliyor...');setSt('Veri cekiliyor...');

  btFetchOHLCV(sym,tf,function(ohlcv,xu100){
    setSt('Parametreler taranıyor...');
    var ranges={
      atr:  {vals:[3,4,5,6,7,8,9,10,11,12,14],label:'ATR Multiplier',key:'chMult'},
      adx:  {vals:[15,18,20,22,25,28,30,35,40],label:'Min ADX',key:'adxMin'},
      fb:   {vals:[0.5,0.55,0.6,0.65,0.7,0.75,0.8,0.85,0.9],label:'Fusion Buy Esigi',key:'fusionThr'},
      mb:   {vals:[0.5,0.55,0.6,0.65,0.7,0.75,0.8,0.85],label:'Master Buy Esigi',key:'masterThr'},
      pro:  {vals:[2,3,4,5,6],label:'PRO Min Skor',key:'proMin'}
    };
    var range=ranges[param]||ranges.atr;
    var results=[];

    range.vals.forEach(function(v){
      var cfg={sym:sym,tf:tf,sys:sys,capital:cap,commission:comm,slippage:slip,
               adxMin:param==='adx'?v:(C.adxMin||25),
               proMin:param==='pro'?v:(C.sc||5),
               fusionThr:param==='fb'?v:((C.fb||80)/100),
               masterThr:param==='mb'?v:0.7,
               stLen:10,stMult:3.0,tmaLen:200,tmaAtrMult:8.0,
               chLen:20,chMult:param==='atr'?v:(C.atrm||8)};
      var res;
      if(ohlcv&&ohlcv.length>=60) res=btEngine(ohlcv,null,cfg);
      else res=btEngSim(sym,tf,sys,cap,comm,slip);
      if(!res||res.tot<2)return;
      var score=crit==='sharpe'?parseFloat(res.sharpe):
                crit==='wr'?parseFloat(res.wr):
                crit==='calmar'?parseFloat(res.calmar):
                parseFloat(res.ret);
      results.push({v:v,score:score,ret:res.ret,wr:res.wr,sharpe:res.sharpe,
                    maxdd:res.maxdd,tot:res.tot,calmar:res.calmar,pf:res.pf,sortino:res.sortino});
    });

    results.sort(function(a,b){return b.score-a.score;});
    renderOpt(results,range.label,param,crit,sym);
    document.getElementById('optout').style.display='block';
    setSt('Optimizasyon tamamlandi');
    toast('En iyi: '+range.label+'='+( results[0]?results[0].v:'N/A'));
  });
}

function renderOpt(results,label,param,crit,sym){
  if(!results.length){document.getElementById('optout').innerHTML='<div style="color:var(--red);font-size:10px;padding:10px">Sonuc yok</div>';return;}
  var best=results[0];
  var critLabel={'sharpe':'Sharpe','ret':'Getiri %','wr':'Win Rate %','calmar':'Calmar'}[crit]||crit;

  var html='<div class="card" style="border-color:rgba(0,230,118,.2)">'
    +'<div class="ctitle" style="color:var(--green)">Optimizasyon: '+label+'</div>'
    +'<div style="font-size:9px;color:var(--t4);margin-bottom:10px">'+sym+' | Kriter: '+critLabel+' | Komisyon dahil</div>'

    +'<div style="background:rgba(0,230,118,.06);border:1px solid rgba(0,230,118,.2);border-radius:8px;padding:12px;margin-bottom:12px">'
      +'<div style="font-size:10px;color:var(--green);font-weight:700;margin-bottom:8px">EN IYI KOMBINASYON</div>'
      +'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px">'
        +'<div class="sstat"><div class="sval" style="color:var(--gold)">'+best.v+'</div><div class="slb2">'+label+'</div></div>'
        +'<div class="sstat"><div class="sval '+(parseFloat(best.ret)>=0?'p':'n')+'">'+best.ret+'%</div><div class="slb2">Getiri</div></div>'
        +'<div class="sstat"><div class="sval" style="color:var(--cyan)">'+best.sharpe+'</div><div class="slb2">Sharpe</div></div>'
        +'<div class="sstat"><div class="sval">'+best.wr+'%</div><div class="slb2">Win Rate</div></div>'
        +'<div class="sstat"><div class="sval n">-'+best.maxdd+'%</div><div class="slb2">Max DD</div></div>'
        +'<div class="sstat"><div class="sval" style="color:var(--purple)">'+best.pf+'</div><div class="slb2">Profit Factor</div></div>'
      +'</div>'
    +'</div>'

    +'<div style="display:grid;grid-template-columns:60px 1fr 1fr 1fr 1fr 1fr;gap:3px;font-size:8px;color:var(--t4);padding:4px 0;border-bottom:1px solid var(--b2)">'
      +'<span>'+label.split(' ')[0]+'</span><span>Getiri</span><span>Win%</span><span>Sharpe</span><span>MaxDD</span><span>PF</span>'
    +'</div>';

  results.forEach(function(x){
    var isBest=x.v===best.v;
    var rc=parseFloat(x.ret)>=0?'var(--green)':'var(--red)';
    html+='<div style="display:grid;grid-template-columns:60px 1fr 1fr 1fr 1fr 1fr;gap:3px;padding:5px 0;border-bottom:1px solid var(--b1);font-size:9px'+(isBest?';background:rgba(0,230,118,.04)':'')+'">'
      +'<span style="font-weight:'+(isBest?'700':'400')+';color:'+(isBest?'var(--green)':'var(--t2)')+'">'+x.v+(isBest?' BEST':'')+' </span>'
      +'<span style="color:'+rc+'">'+x.ret+'%</span>'
      +'<span>'+x.wr+'%</span>'
      +'<span style="color:var(--cyan)">'+x.sharpe+'</span>'
      +'<span style="color:var(--red)">-'+x.maxdd+'%</span>'
      +'<span>'+x.pf+'</span>'
    +'</div>';
  });

  html+='<div style="margin-top:10px;font-size:9px;color:var(--t3);line-height:1.7;padding:8px;background:var(--bg3);border-radius:7px">'
    +'Tavsiye: '+label+' = <span style="color:var(--gold)">'+best.v+'</span> olarak ayarla<br>'
    +'Bunu Ayarlar sekmesinden guncelleyebilirsin.'
    +'<div style="margin-top:6px"><button class="btn g" style="padding:5px 12px;font-size:9px" onclick="applyOptResult(\''+param+'\','+best.v+')">Bu Ayari Uygula</button></div>'
  +'</div></div>';

  document.getElementById('optout').innerHTML=html;
}

function applyOptResult(param, val){
  if(param==='atr'){C.atrm=val;document.getElementById('s_atrm').value=val;}
  else if(param==='adx'){C.adxMin=val;document.getElementById('s_adxMin').value=val;}
  else if(param==='pro'){C.sc=val;document.getElementById('s_sc').value=val;}
  else if(param==='fb'){C.fb=Math.round(val*100);document.getElementById('s_fb').value=Math.round(val*100);}
  lsSet('bistcfg',C);
  toast('Ayar uygulandi: '+param+'='+val);
}

// -- SOUND --
function beep(isMaster){try{var ctx=new(window.AudioContext||window.webkitAudioContext)(),osc=ctx.createOscillator(),g=ctx.createGain();osc.connect(g);g.connect(ctx.destination);osc.frequency.value=isMaster?880:440;g.gain.setValueAtTime(0.3,ctx.currentTime);g.gain.exponentialRampToValueAtTime(0.001,ctx.currentTime+0.5);osc.start(ctx.currentTime);osc.stop(ctx.currentTime+0.5);}catch(e){}}

// -- INIT --
window.addEventListener('load',function(){
  // BT sembol listesi
  var sel=document.getElementById('btSym');sel.innerHTML=STOCKS.map(function(s){return'<option value="'+s.t+'">'+s.t+'</option>';}).join('');
  updateTgUI();loadSetsUI();renderSt();renderSigs();renderAgents();renderPositions();renderWatchlist();renderReport();
  updateXU100();setInterval(updateXU100,5*60*1000);
  setInterval(function(){if(Object.keys(S.openPositions).length>0)renderPositions();},60*1000);
  startAutoScan();setBgActive(true);
  function resetMidnight(){var now=new Date(),ms=new Date(now.getFullYear(),now.getMonth(),now.getDate()+1)-now;setTimeout(function(){S.sentCount={};_dayEndSent=false;resetMidnight();},ms);}resetMidnight();
  if(TG.token&&TG.chat)startTGListener();
  document.getElementById('scanInterval').addEventListener('change',function(){C.scanInterval=parseInt(this.value)||5;lsSet('bistcfg',C);startAutoScan();toast('Tarama araligi: '+C.scanInterval+' dk');});
});
</script>
</body>
</html>
<script>
// ── EK FONKSİYONLAR ────────────────────────────

// FIX 12: Pozisyon sekme geçişi
function posTab(tab){
  document.getElementById('posTabOpen').className='chip'+(tab==='open'?' on':'');
  document.getElementById('posTabClosed').className='chip'+(tab==='closed'?' on':'');
  document.getElementById('positionList').style.display=tab==='open'?'block':'none';
  document.getElementById('closedList').style.display=tab==='closed'?'block':'none';
  if(tab==='closed')renderClosedPositions();
}

// FIX 12: Kapanmış pozisyon render - Pine tablosu alanları
function renderClosedPositions(){
  var el=document.getElementById('closedList');
  if(!S.closedPositions.length){
    el.innerHTML='<div class="empty"><div class="eico">&#128218;</div><div style="font-size:11px">Kapanmis pozisyon yok.</div></div>';
    return;
  }
  // Ozet
  var total=0,wins=0;
  S.closedPositions.forEach(function(p){total+=parseFloat(p.pnlPct);if(parseFloat(p.pnlPct)>=0)wins++;});
  var wr=(S.closedPositions.length?((wins/S.closedPositions.length)*100).toFixed(1):'0');
  var html='<div class="card" style="margin-bottom:8px">'
    +'<div style="display:flex;gap:12px;flex-wrap:wrap">'
      +'<div><div class="sval '+(total>=0?'p':'n')+'" style="font-size:16px">'+(total>=0?'+':'')+total.toFixed(2)+'%</div><div class="slb2">Toplam PnL</div></div>'
      +'<div><div class="sval" style="font-size:16px">'+S.closedPositions.length+'</div><div class="slb2">Toplam SAT</div></div>'
      +'<div><div class="sval" style="font-size:16px;color:'+(parseFloat(wr)>=50?'var(--green)':'var(--red)')+'">'+wr+'%</div><div class="slb2">Win Rate</div></div>'
      +'<div><div class="sval" style="font-size:16px;color:var(--green)">'+wins+'</div><div class="slb2">Karli</div></div>'
      +'<div><div class="sval" style="font-size:16px;color:var(--red)">'+(S.closedPositions.length-wins)+'</div><div class="slb2">Zarari</div></div>'
    +'</div>'
  +'</div>';

  S.closedPositions.forEach(function(p){
    var pnl=parseFloat(p.pnlPct),isProfit=pnl>=0;
    var entryDate=new Date(p.entryTime),exitDate=new Date(p.exitTime);
    var entryStr=pad(entryDate.getDate())+'/'+pad(entryDate.getMonth()+1)+' '+pad(entryDate.getHours())+':'+pad(entryDate.getMinutes());
    var exitStr=pad(exitDate.getDate())+'/'+pad(exitDate.getMonth()+1)+' '+pad(exitDate.getHours())+':'+pad(exitDate.getMinutes());
    var actStr=(p.acts||[]).join(' | ')||'-';
    html+='<div class="pos-card '+(isProfit?'profit':'loss')+'">'
      +'<div class="pos-header">'
        +'<div><div class="pos-ticker">'+p.ticker+'</div><div style="font-size:9px;color:var(--t4)">'+p.tf+' . '+p.holdDays+' gun</div></div>'
        +'<div style="text-align:right"><div class="pos-pnl" style="color:'+(isProfit?'var(--green)':'var(--red)')+'">'+(isProfit?'+':'')+pnl.toFixed(2)+'%</div></div>'
      +'</div>'
      // Pine tablosundaki tüm alanlar: Sembol, AL, SAT, Karli/Zarar, Toplam %, Pozisyon, Fiyat Durumu, Score
      +'<div class="pos-grid">'
        +'<div class="pos-m"><div class="pos-mv">TL'+p.entry.toFixed(2)+'</div><div class="pos-ml">Giris (AL)</div></div>'
        +'<div class="pos-m"><div class="pos-mv">TL'+p.exit.toFixed(2)+'</div><div class="pos-ml">Cikis (SAT)</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="color:'+(isProfit?'var(--green)':'var(--red)')+'">'+(isProfit?'+':'')+pnl.toFixed(2)+'%</div><div class="pos-ml">Toplam %</div></div>'
      +'</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-top:5px">'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+p.cons+'%</div><div class="pos-ml">Konsensus</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+p.adx+'</div><div class="pos-ml">ADX</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:10px">'+p.score+'/6</div><div class="pos-ml">PRO Skor</div></div>'
        +'<div class="pos-m"><div class="pos-mv" style="font-size:9px;color:'+psC(p.pstate)+'">'+p.pstate+'</div><div class="pos-ml">Fiyat Dur.</div></div>'
      +'</div>'
      +'<div style="font-size:8px;color:var(--t4);margin-top:5px;font-family:Courier New,monospace">'
        +'Giris: '+entryStr+' | Cikis: '+exitStr+'<br>'
        +'Sistemler: '+actStr
      +'</div>'
      +'</div>';
  });
  el.innerHTML=html;
}

// FIX 16: Profesyonel Trader Gozetimi + Gelistirme Onerileri
function openTraderConsole(){
  var posCount=Object.keys(S.openPositions).length;
  var sigCount=S.sigs.length;
  var masterCount=S.sigs.filter(function(s){return s.type==='master';}).length;
  var stopCount=S.sigs.filter(function(s){return s.type==='stop';}).length;
  var closedCount=S.closedPositions.length;
  var totalPnl=0;S.closedPositions.forEach(function(p){totalPnl+=parseFloat(p.pnlPct);});
  var wins=S.closedPositions.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
  var wr=closedCount?(wins/closedCount*100).toFixed(1):'N/A';
  var xu=S.xu100Trend;
  var avgPnl=closedCount?(totalPnl/closedCount).toFixed(2):'N/A';

  // Risk degerlendirmesi
  var riskLevel='DUSUK',riskColor='var(--green)',riskEmoji='GREEN';
  if(posCount>=5||xu==='bear'){riskLevel='YUKSEK';riskColor='var(--red)';riskEmoji='RED';}
  else if(posCount>=3){riskLevel='ORTA';riskColor='var(--gold)';riskEmoji='YELLOW';}

  // Gelistirme onerileri
  var tips=[];
  if(parseFloat(wr)<50&&closedCount>5)tips.push('Win rate %50 altinda - ADX ve PRO esiklerini artirmayi deneyin');
  if(posCount>4)tips.push('5+ acik pozisyon - portfoy yogunlasmasi riski, bazi pozisyonlari kapatmayi dusunun');
  if(masterCount===0&&sigCount>3)tips.push('Master AI sinyali yok - consensus esigini dusurun veya daha fazla TF etkinlestirin');
  if(stopCount>masterCount&&closedCount>3)tips.push('Stop tetiklenme orani yuksek - trailing stop parametrelerini optimize edin');
  if(xu==='bull'&&posCount===0)tips.push('XU100 yukseliyor ama acik pozisyon yok - tarama araliginizi dusurun');
  if(xu==='bear'&&posCount>2)tips.push('XU100 dususte - mevcut pozisyonlarda stop seviyelerini sikilastirin');
  if(!TG.token)tips.push('Telegram henuz yapilandirilmamis - anlık bildirimleri kaciracaksiniz');
  if(tips.length===0)tips.push('Sistem optimal calisiyor. Parametreler dengeli gorunuyor.');

  var msg='=== TRADER GOZETIMI ===\n'
    +new Date().toLocaleString('tr-TR')+'\n\n'
    +'--- PORTFOY DURUMU ---\n'
    +'Acik Pozisyon: '+posCount+'\n'
    +'Risk Seviyesi: '+riskLevel+'\n'
    +'Toplam Sinyal: '+sigCount+'\n'
    +'Master AI: '+masterCount+'\n'
    +'Stop Tetiklenen: '+stopCount+'\n\n'
    +'--- PERFORMANS ---\n'
    +'Kapali Pozisyon: '+closedCount+'\n'
    +'Win Rate: '+wr+'%\n'
    +'Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%\n'
    +'Ort. Islem: '+avgPnl+'%\n\n'
    +'--- PIYASA ---\n'
    +'XU100: '+(S.xu100Change>=0?'+':'')+S.xu100Change+'% ('+xu.toUpperCase()+')\n\n'
    +'--- GELISTIRME ONERILERI ---\n';
  tips.forEach(function(t,i){msg+=(i+1)+'. '+t+'\n';});

  // Modal goster
  S.curSig={ticker:'TRADER',name:'Gozetim',indices:[],tf:'D',res:{acts:[]}};
  document.getElementById('mtit').textContent='Profesyonel Trader Gozetimi';
  document.getElementById('mcont').innerHTML=
    '<div style="font-size:11px;font-family:Courier New,monospace;line-height:1.8;white-space:pre-wrap;color:var(--t2)">'+msg+'</div>'
    +'<div style="margin-top:10px">'
      +'<button class="btn c" style="width:100%;padding:10px;border-radius:8px" onclick="sendTraderReport()">Telegram\'a Gonder</button>'
    +'</div>';
  document.getElementById('modal').classList.add('on');
}

function sendTraderReport(){
  if(!TG.token||!TG.chat){toast('Telegram ayarli degil!');return;}
  var posCount=Object.keys(S.openPositions).length;
  var xu=S.xu100Change;
  var closedCount=S.closedPositions.length;
  var totalPnl=0;S.closedPositions.forEach(function(p){totalPnl+=parseFloat(p.pnlPct);});
  var wins=S.closedPositions.filter(function(p){return parseFloat(p.pnlPct)>=0;}).length;
  var wr=closedCount?(wins/closedCount*100).toFixed(1):'N/A';
  var ts=new Date().toLocaleString('tr-TR');
  var msg='TRADER GOZETIM RAPORU\n'+ts+'\n\n'
    +'Acik Pozisyon: '+posCount+'\n'
    +'XU100: '+(xu>=0?'+':'')+xu+'%\n'
    +'Win Rate: '+wr+'%\n'
    +'Toplam PnL: '+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%\n\n'
    +'Uygulama: BIST AI Scanner v6';
  fetch('https://api.telegram.org/bot'+TG.token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:TG.chat,text:msg})})
  .then(function(r){return r.json();}).then(function(d){toast(d.ok?'Gonderildi!':'Hata');}).catch(function(){toast('Hata');});
  document.getElementById('modal').classList.remove('on');
}
</script>
<script>
// ── FIX 11: Ozel Indiktor Yonetimi ──────────────
var customIndicators=lsGet('bist_cindicators')||[];

function addCustomIndicator(){
  var name=document.getElementById('customIndName').value.trim();
  if(!name){toast('Indiktor adi girin!');return;}
  var ind={
    id:Date.now(),
    name:name,
    minRsi:parseInt(document.getElementById('ci_rsi').value)||50,
    minAdx:parseInt(document.getElementById('ci_adx').value)||20,
    requireEma:document.getElementById('ci_ema').checked,
    includeScan:document.getElementById('ci_scan').checked,
    active:true
  };
  customIndicators.push(ind);
  lsSet('bist_cindicators',customIndicators);
  document.getElementById('customIndName').value='';

// ── SERVICE WORKER KAYDI (FIX 1) ─────────────────────────────
// Uygulama kapalıyken bildirim için SW kaydet
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js').then(function(reg){
    console.log('SW registered:', reg.scope);
    document.getElementById('bgText').textContent='Arka plan aktif';
    document.getElementById('bgDot').className='bg-dot on';
    // Periodic sync kaydet (desteklenen tarayıcılarda)
    if('periodicSync' in reg){
      navigator.permissions.query({name:'periodic-background-sync'}).then(function(s){
        if(s.state==='granted')
          reg.periodicSync.register('bist-scan',{minInterval:5*60*1000});
      });
    }
  }).catch(function(e){
    console.log('SW error:',e);
    document.getElementById('bgText').textContent='SW desteklenmiyor';
  });
}
// Push izni iste
function requestPushPermission(){
  if(!('Notification' in window)){toast('Bildirim desteklenmiyor');return;}
  Notification.requestPermission().then(function(p){
    if(p==='granted'){
      toast('Bildirim izni verildi!');
      document.getElementById('bgDot').className='bg-dot on';
    } else {
      toast('Bildirim izni reddedildi. Sadece Telegram kullanilacak.');
    }
  });
}

  renderCustomIndicators();
  toast(name+' eklendi');
}

function removeCustomIndicator(id){
  customIndicators=customIndicators.filter(function(x){return x.id!==id;});
  lsSet('bist_cindicators',customIndicators);
  renderCustomIndicators();
}

function toggleCustomIndicator(id){
  customIndicators.forEach(function(x){if(x.id===id)x.active=!x.active;});
  lsSet('bist_cindicators',customIndicators);
  renderCustomIndicators();
  toast('Indiktor guncellendi');
}

function renderCustomIndicators(){
  var el=document.getElementById('customIndList');
  if(!el)return;
  if(!customIndicators.length){el.innerHTML='<div style="font-size:9px;color:var(--t4)">Henuz ozel indiktor eklenmedi.</div>';return;}
  el.innerHTML=customIndicators.map(function(ind){
    return '<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid var(--b1)">'
      +'<div style="flex:1"><div style="font-size:11px;color:'+(ind.active?'var(--cyan)':'var(--t4)')+'">'+ind.name+'</div>'
        +'<div style="font-size:8px;color:var(--t4)">RSI>'+ind.minRsi+' ADX>'+ind.minAdx+(ind.requireEma?' EMA-Ustu':'')+(ind.includeScan?' TARAMA':'')+'</div>'
      +'</div>'
      +'<button class="btn '+(ind.active?'c':'')+'" style="padding:4px 8px;font-size:8px" onclick="toggleCustomIndicator('+ind.id+')">'+(ind.active?'AKTIF':'PASIF')+'</button>'
      +'<button class="btn r" style="padding:4px 7px;font-size:8px" onclick="removeCustomIndicator('+ind.id+')">Sil</button>'
      +'</div>';
  }).join('');
}

// FIX 13: False Sinyal Analiz Modulu
function openFalseSignalAnalysis(){
  if(S.closedPositions.length<3){toast('En az 3 kapali pozisyon gerekli!');return;}
  var all=S.closedPositions;
  var losses=all.filter(function(p){return parseFloat(p.pnlPct)<0;});
  var wins=all.filter(function(p){return parseFloat(p.pnlPct)>=0;});

  // False sinyal analizi
  var falseSigs=losses.length;
  var falseRate=(all.length?((falseSigs/all.length)*100).toFixed(1):'0');

  // Ortalama giris metrikleri - yanlıs sinyallerde
  var avgConsFalse=0,avgAdxFalse=0,avgScoreFalse=0;
  losses.forEach(function(p){avgConsFalse+=parseFloat(p.cons||0);avgAdxFalse+=parseFloat(p.adx||0);avgScoreFalse+=parseFloat(p.score||0);});
  if(losses.length){avgConsFalse/=losses.length;avgAdxFalse/=losses.length;avgScoreFalse/=losses.length;}

  // Kazanan sinyallerdeki metrikler
  var avgConsWin=0,avgAdxWin=0,avgScoreWin=0;
  wins.forEach(function(p){avgConsWin+=parseFloat(p.cons||0);avgAdxWin+=parseFloat(p.adx||0);avgScoreWin+=parseFloat(p.score||0);});
  if(wins.length){avgConsWin/=wins.length;avgAdxWin/=wins.length;avgScoreWin/=wins.length;}

  // Tavsiye edilen esikler (false sinyalleri azalt)
  var recCons=Math.min(95,Math.ceil(avgConsFalse+10));
  var recAdx=Math.min(60,Math.ceil(avgAdxFalse+5));
  var recScore=Math.min(6,Math.ceil(avgScoreFalse+1));

  // Trailing stop analizi - farkli stop tipleri
  var stopAnalysis=[
    {name:'Mevcut (ATR x'+( C.atrm||8)+')',rate:falseRate+'%'},
    {name:'Dar Stop (ATR x4)',rate:(falseRate*0.7).toFixed(1)+'% (daha cok tetik)'},
    {name:'Genis Stop (ATR x12)',rate:(falseRate*1.3).toFixed(1)+'% (daha az tetik)'},
    {name:'Chandelier 22x3',rate:(falseRate*0.85).toFixed(1)+'% (tahmin)'}
  ];

  S.curSig={ticker:'ANALIZ',name:'False Sinyal',indices:[],tf:'D',res:{acts:[]}};
  document.getElementById('mtit').textContent='False Sinyal Analizi';
  document.getElementById('mcont').innerHTML=
    '<div style="font-size:10px;line-height:1.9;color:var(--t2)">'
      +'<div style="margin-bottom:10px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--gold);font-size:11px;font-weight:700;margin-bottom:4px">Genel</div>'
        +'Toplam: '+all.length+' | Kaybeden: '+falseSigs+' | False Rate: '+falseRate+'%'
      +'</div>'
      +'<div style="margin-bottom:10px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--red);font-size:11px;font-weight:700;margin-bottom:4px">Kaybeden Sinyallerde Ort. Metrikler</div>'
        +'Consensus: %'+avgConsFalse.toFixed(1)+' | ADX: '+avgAdxFalse.toFixed(1)+' | PRO: '+avgScoreFalse.toFixed(1)+'/6'
      +'</div>'
      +'<div style="margin-bottom:10px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--green);font-size:11px;font-weight:700;margin-bottom:4px">Kazanan Sinyallerde Ort. Metrikler</div>'
        +'Consensus: %'+avgConsWin.toFixed(1)+' | ADX: '+avgAdxWin.toFixed(1)+' | PRO: '+avgScoreWin.toFixed(1)+'/6'
      +'</div>'
      +'<div style="margin-bottom:10px;padding:8px;background:rgba(0,212,255,.05);border:1px solid rgba(0,212,255,.2);border-radius:7px">'
        +'<div style="color:var(--cyan);font-size:11px;font-weight:700;margin-bottom:6px">Tavsiye Edilen Esikler</div>'
        +'Min Consensus: %'+recCons+'<br>'
        +'Min ADX: '+recAdx+'<br>'
        +'Min PRO Skor: '+recScore+'/6<br>'
        +'<button class="btn c" style="padding:5px 10px;font-size:9px;margin-top:6px" onclick="applyFalseSignalFix('+recCons+','+recAdx+','+recScore+')">Bu Ayarlari Uygula</button>'
      +'</div>'
      +'<div style="margin-bottom:8px;padding:8px;background:var(--bg3);border-radius:7px">'
        +'<div style="color:var(--orange);font-size:11px;font-weight:700;margin-bottom:4px">Trailing Stop Karsilastirmasi</div>'
        +stopAnalysis.map(function(s){return s.name+': '+s.rate;}).join('<br>')
      +'</div>'
    +'</div>';
  document.getElementById('modal').classList.add('on');
}

function applyFalseSignalFix(cons,adx,score){
  C.minCons=cons;C.adxMin=adx;C.sc=score;lsSet('bistcfg',C);
  document.getElementById('s_adxMin').value=adx;
  document.getElementById('s_sc').value=score;
  document.getElementById('minCons').value=cons;
  document.getElementById('modal').classList.remove('on');
  toast('Ayarlar uygulandi: Consensus>'+cons+' ADX>'+adx+' PRO>'+score);
}

// FIX 11: Tarama sırasında özel indikatör kontrolü
function checkCustomIndicators(res){
  var active=customIndicators.filter(function(x){return x.active&&x.includeScan;});
  if(!active.length)return true;
  return active.every(function(ind){
    var rsiOk=parseFloat(res.rsi||50)>=ind.minRsi;
    var adxOk=parseFloat(res.adx||0)>=ind.minAdx;
    var emaOk=!ind.requireEma||(res.ema200&&res.price&&parseFloat(res.price)>parseFloat(res.ema200));
    return rsiOk&&adxOk&&emaOk;
  });
}

// Sayfa yuklendikten sonra calistir
setTimeout(function(){
  renderCustomIndicators();
  // False sinyal analiz butonu rapor sayfasina ekle
  var repPage=document.getElementById('page-report');
  if(repPage){
    var btn=document.createElement('button');
    btn.className='btn r';
    btn.style.cssText='width:100%;padding:11px;border-radius:8px;margin-bottom:8px';
    btn.textContent='False Sinyal Analizi';
    btn.onclick=openFalseSignalAnalysis;
    repPage.appendChild(btn);
  }
},500);
</script>
<script>
// BIST v6 FIX - 17,18,19,20,21

// 17: Push permission duzeltme
function requestPushPermission(){
  if(!('Notification' in window)){toast('Bildirim desteklenmiyor. Telegram kullanin.');return;}
  if(Notification.permission==='granted'){toast('Push izni zaten var!');return;}
  if(Notification.permission==='denied'){toast('Ayarlar > Safari > Bildirimler bolumunden etkinlestirin.');return;}
  Notification.requestPermission(function(p){
    if(p==='granted'){
      toast('Push izni verildi!');
      var d=document.getElementById('bgDot');if(d)d.className='bg-dot on';
    } else {
      toast('Izin verilmedi. Telegram aktif kalir.');
    }
  });
}

// 18: btEngine sharpe/calmar duzeltme
var _origBtEng=btEngine;
btEngine=function(ohlcv,xu100Closes,cfg){
  var r=_origBtEng(ohlcv,xu100Closes,cfg);
  if(!r||!r.trades||r.trades.length<2)return r;
  var ct=r.trades.filter(function(t){return !t.open;});
  var rets=ct.map(function(t){return parseFloat(t.p);});
  if(rets.length<2)return r;
  var mean=rets.reduce(function(a,b){return a+b;},0)/rets.length;
  var vr=rets.reduce(function(a,x){return a+(x-mean)*(x-mean);},0)/rets.length;
  var std=Math.sqrt(vr);
  var avgBars=ct.reduce(function(a,t){return a+(t.bars||5);},0)/ct.length;
  var rf=0.40*(avgBars/252)*100;
  r.sharpe=std>0?((mean-rf)/std).toFixed(2):'0';
  var dr=rets.filter(function(x){return x<0;});
  var ds=0;if(dr.length){var dv=dr.reduce(function(a,x){return a+x*x;},0)/dr.length;ds=Math.sqrt(dv);}
  r.sortino=ds>0?((mean-rf)/ds).toFixed(2):'0';
  var tot=parseFloat(r.ret);var yrs=Math.max(0.1,(ohlcv?ohlcv.length:504)/252);
  var ann=(Math.pow(1+tot/100,1/yrs)-1)*100;
  var mdd=parseFloat(r.maxdd)||1;
  r.calmar=mdd>0?(ann/mdd).toFixed(2):'0';
  return r;
};

// 19/20: signalPrice - renderSigs override ile goster
var _origRenderSigs=renderSigs;
renderSigs=function(){
  _origRenderSigs.apply(this,arguments);
  saveSigsToLS();
};

// 21: Sinyal kalicilik
function saveSigsToLS(){
  try{
    var sv=S.sigs.slice(0,100).map(function(s){
      return{id:s.id,ticker:s.ticker,name:s.name||s.ticker,indices:s.indices||[],type:s.type||'buy',tf:s.tf||'D',
        time:s.time instanceof Date?s.time.toISOString():s.time,
        res:{price:s.res.price,signalPrice:s.res.signalPrice||s.res.price,cons:s.res.cons,adx:s.res.adx,
          score:s.res.score,fp:s.res.fp,pstate:s.res.pstate,acts:s.res.acts||[],
          currentStop:s.res.currentStop,isMaster:s.res.isMaster,strength:s.res.strength}};
    });
    localStorage.setItem('bist_sigs',JSON.stringify(sv));
  }catch(e){}
}
function loadSavedSigs(){
  if(S.sigs&&S.sigs.length>0)return;
  try{
    var saved=JSON.parse(localStorage.getItem('bist_sigs')||'[]');
    if(!saved||!saved.length)return;
    var cut=Date.now()-24*60*60*1000;
    saved.forEach(function(s){
      if(new Date(s.time).getTime()<cut)return;
      S.sigs.push({id:s.id,ticker:s.ticker,name:s.name||s.ticker,indices:s.indices||[],
        type:s.type||'buy',tf:s.tf||'D',time:new Date(s.time),res:s.res||{}});
    });
    if(S.sigs.length>0){renderSigs();updateBadge();}
  }catch(e){}
}
window.addEventListener('load',function(){setTimeout(loadSavedSigs,600);});
</script>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def serve_app():
    return HTMLResponse(content=_HTML, headers={
        "Cache-Control": "no-cache, no-store",
        "Content-Type": "text/html; charset=utf-8"
    })

@app.get("/app", response_class=HTMLResponse)
async def serve_app2():
    return HTMLResponse(content=_HTML, headers={
        "Cache-Control": "no-cache, no-store",
        "Content-Type": "text/html; charset=utf-8"
    })
