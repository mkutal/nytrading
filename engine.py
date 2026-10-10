"""Offline EMA pullback research. Standard library only. No network or secrets."""
from __future__ import annotations
import bisect, csv, hashlib, json, math
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
UTC=timezone.utc
PRAGUE=ZoneInfo('Europe/Prague')
FIVE=timedelta(minutes=5)

@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float

@dataclass(frozen=True)
class Config:
    initial: float=100000
    risk: float=.01
    atr_multiple: float=1.5
    target: float=2
    minimum_room_r: float=0  # zero follows TP; positive freezes eligibility across TP studies
    spread: float=.30  # USD/oz, full spread; scenario, not broker measurement
    slippage: float=.05  # adverse USD/oz per market/stop fill
    commission: float=6  # round trip USD per lot, user confirmed
    contract: float=100  # reference ounces/lot, needs broker verification
    lot_step: float=.01
    min_lot: float=.01
    max_lot: float=50
    daily_guard: float=.04  # voluntary stop before FTMO 5% limit
    leverage: float=30  # conservative reference margin scenario
    def __post_init__(self):
        for k,v in asdict(self).items():
            if not math.isfinite(v) or v<0: raise ValueError('Invalid configuration '+k)
        if not (0<self.risk<self.daily_guard<.05 and self.initial>0 and self.contract>0
                and self.atr_multiple>0 and self.target>0 and self.lot_step>0 and self.leverage>0):
            raise ValueError('Invalid risk/contract configuration')


@dataclass(frozen=True)
class Signal:
    side: int
    atr: float
    stop: float | None = None
    room_price: float | None = None
    min_atr_risk: float = .5
    max_atr_risk: float = 2


@dataclass(frozen=True)
class Window:
    name: str
    zone: str
    open_hour: int
    open_minute: int
    entry_from: int
    entry_until: int
    close_after: int

    def times(self,t):
        local=t.astimezone(ZoneInfo(self.zone))
        opened=local.replace(hour=self.open_hour,minute=self.open_minute,second=0,microsecond=0)
        # Define offsets in local clock time; these windows never cross a DST switch.
        return tuple((opened+timedelta(minutes=x)).astimezone(UTC)
                     for x in (self.entry_from,self.entry_until,self.close_after))

    def __post_init__(self):
        if not self.entry_from<self.entry_until<=self.close_after:
            raise ValueError('Invalid entry/holding window')
        ZoneInfo(self.zone)


def load(path, minutes):
    raw=json.loads(Path(path).read_text())
    rows=[]
    for r in raw:
        t=datetime.fromisoformat(r['datetime'].replace('Z','+00:00'))
        # Only valid for these files: download manifests explicitly request UTC.
        if t.tzinfo is None: t=t.replace(tzinfo=UTC)
        t=t.astimezone(UTC)
        b=Bar(t,*(float(r[k]) for k in ('open','high','low','close')))
        if t.second or t.microsecond or (t.hour*60+t.minute)%minutes: raise ValueError('Misaligned time')
        if not all(math.isfinite(x) and x>0 for x in (b.open,b.high,b.low,b.close)): raise ValueError('Invalid price')
        if not b.low<=min(b.open,b.close)<=max(b.open,b.close)<=b.high: raise ValueError('Invalid OHLC')
        rows.append(b)
    rows.sort(key=lambda b:b.time)
    if not rows or len({b.time for b in rows})!=len(rows): raise ValueError('Empty/duplicate timestamps')
    gaps=Counter(int((b.time-a.time).total_seconds()/60) for a,b in zip(rows,rows[1:]) if b.time-a.time!=timedelta(minutes=minutes))
    return rows,dict(count=len(rows),first=rows[0].time.isoformat(),last_open=rows[-1].time.isoformat(),
        sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),gap_minutes=dict(gaps),volume_present=sum('volume' in r for r in raw))


def ema(values,n):
    out=[None]*len(values)
    if len(values)<n: return out
    v=sum(values[:n])/n; out[n-1]=v
    for i in range(n,len(values)):
        v+=2/(n+1)*(values[i]-v); out[i]=v
    return out


def atr(rows,n=14):
    out=[None]*len(rows)
    tr=[max(b.high-b.low,abs(b.high-a.close),abs(b.low-a.close)) for a,b in zip(rows,rows[1:])]
    if len(tr)<n: return out
    v=sum(tr[:n])/n; out[n]=v
    for i in range(n+1,len(rows)):
        v+=(tr[i-1]-v)/n; out[i]=v
    return out


def signals(m15,h1):
    """Map signal availability time to side and ATR; only completed bars."""
    htimes=[b.time+timedelta(hours=1) for b in h1]
    hf=ema([b.close for b in h1],50); hs=ema([b.close for b in h1],200)
    mf=ema([b.close for b in m15],50); av=atr(m15)
    result={}
    for i,b in enumerate(m15):
        t=b.time+timedelta(minutes=15)
        j=bisect.bisect_right(htimes,t)-1
        # Long warmup mitigates seed dependence; current H1 must be fresh.
        if i<250 or j<400 or t-htimes[j]>=timedelta(hours=1): continue
        if m15[i-1].time+timedelta(minutes=15)!=b.time: continue
        if hf[j]>hs[j] and b.low<=mf[i]<=b.close and b.close>b.open:
            result[t]=(1,av[i])
        elif hf[j]<hs[j] and b.high>=mf[i]>=b.close and b.close<b.open:
            result[t]=(-1,av[i])
    return result


def bounds(t,session):
    zone,start,end={'day':('Europe/Prague',(8,0),(20,0)),
        'london':('Europe/London',(8,0),(13,0)),
        'ny':('America/New_York',(8,30),(13,30))}[session]
    local=t.astimezone(ZoneInfo(zone))
    return tuple(local.replace(hour=h,minute=m,second=0,microsecond=0).astimezone(UTC) for h,m in (start,end))


def size(balance,entry,distance,c):
    # Include round-trip fees + assumed adverse stop slippage in planned 1% risk.
    per_lot=c.contract*(distance+c.slippage)+c.commission
    lots=math.floor(min(balance*c.risk/per_lot,c.max_lot,balance*c.leverage/(entry*c.contract))/c.lot_step)*c.lot_step
    return lots if lots>=c.min_lot else 0


def exit_bar(p,b,c):
    """Executable bid/ask for a declared mid-price reference; SL wins ties."""
    s=p['side']; shift=-s*c.spread/2
    op,hi,lo,cl=(x+shift for x in (b.open,b.high,b.low,b.close))
    stop,tp=p['sl'],p['tp']
    if s*(op-stop)<=0: return op-s*c.slippage,'SL_GAP',op
    if s*(op-tp)>=0: return tp,'TP_GAP',op  # no favorable improvement
    slhit=lo<=stop if s==1 else hi>=stop
    tphit=hi>=tp if s==1 else lo<=tp
    adverse=max(lo,stop) if s==1 else min(hi,stop)
    if slhit: return stop-s*c.slippage,'SL_BOTH' if tphit else 'SL',adverse
    if tphit: return tp,'TP',adverse
    return None,None,adverse


def run(m5,sigs,c,session,start,end,*,windows=None,record_equity=True,target_by_window=None):
    if start>=end: raise ValueError('Invalid period')
    if target_by_window and any(not math.isfinite(v) or v<=0 for v in target_by_window.values()):
        raise ValueError('Invalid per-window target')
    lookup={b.time:b for b in m5}
    balance=c.initial; peak=balance; dd=0; previous_time=None; position=None; trades=[]; equity=[]; breaches=[]
    day=None; day_balance=balance; locked=False; stopped=False; coverage={}; rejected=Counter()
    def mark(t,value,event):
        nonlocal peak,dd,stopped
        peak=max(peak,value); dd=max(dd,(peak-value)/peak)
        if record_equity:
            equity.append(dict(time=t.isoformat(),equity=value,balance=balance,event=event))
        rules=[]
        if value<day_balance-c.initial*.05: rules.append('daily_loss')
        if value<c.initial*.90: rules.append('maximum_loss')
        if rules:
            breaches.append(dict(time=t.isoformat(),equity=value,day_start_balance=day_balance,rules=rules,event=event))
            stopped=True
    def close(t,price,reason):
        nonlocal balance,position,locked
        p=position
        gross=p['side']*(price-p['entry'])*p['units']
        balance+=gross-c.commission*p['lots']/2
        net=gross-c.commission*p['lots']
        trades.append(dict(entry_time=p['time'].isoformat(),exit_time=t.isoformat(),side=p['side'],
            entry=p['entry'],sl=p['sl'],tp=p['tp'],exit=price,lots=p['lots'],risk_usd=p['risk_usd'],
            pnl=net,r=net/p['risk_usd'],reason=reason,session=session))
        position=None; mark(t,balance,reason)
        locked=balance<=day_balance-c.initial*c.daily_guard
    for b in m5:
        t=b.time
        if t<start or t>=end: continue
        d=t.astimezone(PRAGUE).date()
        if day!=d:
            if position: raise RuntimeError('Unexpected overnight position; swaps not modelled')
            day=d; day_balance=balance; locked=False
        entry_allowed=True; window_name=session
        if windows:
            choices=[(w,*w.times(t)) for w in windows]
            matching=[x for x in choices if x[1]<=t<x[3]]
            entering=[x for x in matching if t<x[2]]
            w,left,cutoff,right=(entering or matching or choices)[0]
            entry_allowed=left<=t<cutoff; window_name=w.name
        else:
            left,right=bounds(t,session)
        key=window_name+':'+left.isoformat()
        if key not in coverage:
            missing=[]; x=left
            while x<right:
                if x not in lookup: missing.append(x.isoformat())
                x+=FIVE
            coverage[key]=dict(missing_count=len(missing),first_missing=missing[:5])
        active=t.weekday()<5 and left<=t<right
        # Coverage audit never controls entry decisions: no future-data gate.
        if previous_time is not None and t-previous_time!=FIVE and active and previous_time>=left:
            if position: raise RuntimeError('Missing execution bar while a position is open: outcome unknowable')
            rejected['observed_execution_gap']+=1
        previous_time=t
        mark(t,balance if not position else balance+position['side']*(b.open-position['side']*c.spread/2-position['entry'])*position['units'],'open')
        if position and (t>=position['deadline'] or t+FIVE>end):
            close(t,b.open-position['side']*(c.spread/2+c.slippage),'DEADLINE')
        if stopped:
            if position: close(t,b.open-position['side']*(c.spread/2+c.slippage),'FTMO_BREACH_OPEN')
            break
        if position is None and active and entry_allowed and t in sigs and not locked:
            target=target_by_window.get(window_name,c.target) if target_by_window else c.target
            signal=sigs[t]
            if isinstance(signal,Signal):
                s,a=signal.side,signal.atr
            else:
                s,a=signal
            if s not in (-1,1) or not math.isfinite(a) or a<=0:
                raise ValueError('Invalid signal')
            entry=b.open+s*(c.spread/2+c.slippage)
            distance=c.atr_multiple*a
            if isinstance(signal,Signal) and signal.stop is not None:
                distance=s*(entry-signal.stop)
                if not signal.min_atr_risk*a<=distance<=signal.max_atr_risk*a:
                    rejected['invalid_structure_stop']+=1; continue
                if signal.room_price is not None and s*(signal.room_price-entry)<max(target,c.minimum_room_r)*distance:
                    rejected['insufficient_2R_room']+=1; continue
            lots=size(balance,entry,distance,c); risk=lots*(c.contract*(distance+c.slippage)+c.commission)
            if not lots: rejected['minimum_lot']+=1; continue
            if balance-risk<=max(day_balance-c.initial*c.daily_guard,c.initial*.90):
                rejected['risk_headroom']+=1; continue
            position=dict(time=t,side=s,entry=entry,sl=entry-s*distance,tp=entry+s*distance*target,
                          lots=lots,units=lots*c.contract,risk_usd=risk,deadline=right,window=window_name)
            balance-=c.commission*lots/2
            mark(t,balance-s*(entry-(b.open-s*c.spread/2))*position['units'],'entry')
        if position:
            price,reason,adverse=exit_bar(position,b,c)
            p=position
            worst=adverse-p['side']*(c.slippage if reason and reason.startswith('SL') else 0)
            mark(t,balance+p['side']*(worst-p['entry'])*p['units'],'intrabar_adverse_bound')
            if price is not None: close(t+FIVE,price,reason)
            elif t+FIVE>=p['deadline'] or t+FIVE>=end:
                close(t+FIVE,b.close-p['side']*(c.spread/2+c.slippage),'DEADLINE')
            elif stopped:
                close(t+FIVE,worst,'FTMO_BREACH_BOUND')
            else:
                mark(t+FIVE,balance+p['side']*(b.close-p['side']*c.spread/2-p['entry'])*p['units'],'close')
        if stopped: break
    if position: raise RuntimeError('Unresolved terminal position')
    profits=[x['pnl'] for x in trades]; wins=sum(x>0 for x in profits); loss=-sum(x for x in profits if x<0)
    days=len({datetime.fromisoformat(x['entry_time']).astimezone(PRAGUE).date() for x in trades})
    summary=dict(session=session,config=asdict(c),target_by_window=target_by_window,start=start.isoformat(),end=end.isoformat(),trades=len(trades),
        win_rate=wins/len(trades) if trades else None,profit_factor=sum(x for x in profits if x>0)/loss if loss else None,
        expected_r=sum(x['r'] for x in trades)/len(trades) if trades else None,net_usd=balance-c.initial,
        final_equity=balance,max_dd_adverse_bound=dd,ftmo_breach=bool(breaches),breaches=breaches,
        stopped_at_first_breach=stopped,trading_days=days,target_10pct_at_end=balance>=110000 and days>=4 and not breaches,
        last_trade_exit=trades[-1]['exit_time'] if trades else None,
        insufficient_total_headroom=balance-balance*c.risk<=c.initial*.90,
        incomplete_sessions=sum(v['missing_count']>0 for v in coverage.values()),rejected=dict(rejected),
        ambiguous_stop_first=sum(x['reason']=='SL_BOTH' for x in trades))
    return summary,trades,equity,coverage


def write_csv(path,rows):
    if not rows: Path(path).write_text(''); return
    with Path(path).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
