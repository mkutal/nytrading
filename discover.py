"""Three separate price-based hypotheses. Fixed rules, no fitting to 60% target."""
import bisect,json,math,statistics
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from engine import Bar,Signal,Config,atr,run,load,write_csv
UTC=timezone.utc

def efficiency(bars,n=20):
    closes=[b.close for b in bars]; result=[None]*len(bars)
    for i in range(n,len(bars)):
        path=sum(abs(closes[j]-closes[j-1]) for j in range(i-n+1,i+1))
        result[i]=abs(closes[i]-closes[i-n])/path if path else 0
    return result


def candidates(m15,h1):
    av=atr(m15); er=efficiency(h1)
    htimes=[b.time+timedelta(hours=1) for b in h1]
    out={name:{} for name in ('failed_breakout','band_reversion','compression_breakout')}
    for i,b in enumerate(m15):
        t=b.time+timedelta(minutes=15); j=bisect.bisect_right(htimes,t)-1
        if i<100 or j<20 or t-htimes[j]>=timedelta(hours=1):continue
        # Never cross a missing indicator bar within the required M15 window.
        recent=m15[i-20:i]
        if any(y.time-x.time!=timedelta(minutes=15) for x,y in zip(m15[i-20:i+1],m15[i-19:i+1])):continue
        a=av[i];high=max(x.high for x in recent);low=min(x.low for x in recent)
        fraction=(b.close-b.low)/(b.high-b.low) if b.high>b.low else .5
        if er[j]<=.3:
            if b.low<low-.1*a and b.close>low and b.close>b.open and fraction>=.65:
                out['failed_breakout'][t]=Signal(1,a,b.low-.2*a,high)
            elif b.high>high+.1*a and b.close<high and b.close<b.open and fraction<=.35:
                out['failed_breakout'][t]=Signal(-1,a,b.high+.2*a,low)
            prices=[x.close for x in recent];mean=statistics.mean(prices);sd=statistics.pstdev(prices)
            # Bands fixed from prior bars, not recalculated using future prices.
            if sd and b.low<mean-2*sd and b.close>mean-2*sd and b.close>b.open and fraction>=.65:
                out['band_reversion'][t]=Signal(1,a,b.low-.2*a,mean)
            elif sd and b.high>mean+2*sd and b.close<mean+2*sd and b.close<b.open and fraction<=.35:
                out['band_reversion'][t]=Signal(-1,a,b.high+.2*a,mean)
        # Prior four-bar box must be small relative to prior ATR (before breakout).
        box=m15[i-4:i]; top=max(x.high for x in box); bottom=min(x.low for x in box)
        old=av[i-1]
        if top-bottom<=1.5*old and b.high-b.low>=1.2*old:
            if b.close>top+.1*old and fraction>=.75:
                out['compression_breakout'][t]=Signal(1,a,b.low-.2*a)
            elif b.close<bottom-.1*old and fraction<=.25:
                out['compression_breakout'][t]=Signal(-1,a,b.high+.2*a)
    return out


def wilson(k,n):
    if not n:return None
    z=1.96;p=k/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [center-half,center+half]


def main():
    root=Path(__file__).resolve().parent;data=root/'data';out=root/'discovery';out.mkdir(exist_ok=True)
    bars={k:load(data/(k+'.json'),m)[0] for k,m in [('5min',5),('15min',15),('1h',60)]}
    # Remove UTC weekends only in indicator series; retain original execution history.
    m=[b for b in bars['15min'] if b.time.weekday()<5];h=[b for b in bars['1h'] if b.time.weekday()<5]
    sigs=candidates(m,h)
    design=dict(prototypes=['failed_breakout','band_reversion','compression_breakout'],
        parameters='rolling range/bands=20 M15, hourly efficiency20 <=0.30 for reversals; candle close location >=0.65 or <=0.35; ATR14 stop pad0.2; stop distance0.5–2ATR; compression prior4 range<=1.5 priorATR + expansion1.2 priorATR; close beyond box0.1ATR, close location0.75',
        costs=asdict(Config()),periods=['full 2026-04-01–2026-10-03','later 2026-08-01–2026-10-03'],
        warning='Discovery, not validation; later period has been inspected before. No claims of 60% performance. Weekend removal is a sensitivity convention, not broker calendar reconstruction.')
    (out/'design.json').write_text(json.dumps(design,indent=2))
    results={}
    for name,sig in sigs.items():
        for session in ('day','london','ny'):
            for period,start in [('full','2026-04-01'),('later','2026-08-01')]:
                for cost,c in [('base',Config()),('stress',Config(spread=.6,slippage=.15))]:
                    key=f'{name}_{session}_{period}_{cost}'
                    r,tr,eq,_=run(bars['5min'],sig,c,session,datetime.fromisoformat(start).replace(tzinfo=UTC),datetime(2026,10,3,tzinfo=UTC))
                    tp=sum(x['reason'].startswith('TP') for x in tr);n=len(tr)
                    r.update(tp_hits=tp,tp_rate=tp/n if n else None,tp_rate_wilson95=wilson(tp,n),signals_total=len(sig))
                    results[key]=r;write_csv(out/(key+'_trades.csv'),tr)
                    if cost=='base':write_csv(out/(key+'_equity.csv'),eq)
    (out/'results.json').write_text(json.dumps(results,indent=2,allow_nan=False))
    lines=['# Samostatné nové strategie XAUUSD: průzkum','',
        'Všechny tři hypotézy mají pevný cenový TP 2R bez částečných výstupů a bez posunu SL. Deadline session může obchod uzavřít dříve. Win rate znamená čistě ziskové obchody; TP rate znamená zásah celého cíle 2R. Tyto veličiny se nezaměňují.','',
        'Model: $100 000, risk 1 % dynamicky včetně komise a předpokládaného skluzu při SL, komise $6/lot round trip, spread $0,30/oz, skluz $0,05/oz. Spread/kontrakt/margin jsou scénáře. FTMO 2-Step pravidla a interní ochrany stejné jako v engine.py.','',
        '| Hypotéza | Session | Období | N | Ziskové | TP 2R | PF | E[R] | USD | DD* |','|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    def fmt(v,percent=False):return '—' if v is None else (f'{v:.1%}' if percent else f'{v:.2f}')
    for k,r in results.items():
        if not k.endswith('_base'):continue
        name=k.split('_'+r['session']+'_')[0];period='later' if '_later_' in k else 'full'
        lines.append(f"| {name} | {r['session']} | {period} | {r['trades']} | {fmt(r['win_rate'],True)} | {fmt(r['tp_rate'],True)} | {fmt(r['profit_factor'])} | {fmt(r['expected_r'])} | {r['net_usd']:+,.0f} | {r['max_dd_adverse_bound']:.1%} |")
    lines+=['','*Nepříznivé M5 equity extrémy se SL-first, bez všech příznivých intrabar vrcholů. Není měřeno na tickových kotacích.','',
        '## Přesná interpretace','',
        'failed_breakout: cena prorazí maximum/minimum předchozích 20 M15 a zavře zpět v rozpětí. Pro BUY prorazí low alespoň o0.1ATR, zavře nad low, tělo je růstové, close je v horních35 % svíčky; SELL opačně. H1 efficiency20 <=0.30. SL za knotem +0.2ATR. Od nového fillu musí zbývat do protějšího okraje alespoň 2 cenová R. Nejde o důkaz „lovu stopů“: OHLC neukazuje objednávky ani likviditu.','',
        'band_reversion: stejné potvrzení obratu a režim, návrat z pásma mean20 ±2 standardní odchylky předchozích M15. Do středu mean20 musí zbývat alespoň2R. Je to cena vs statistický průměr, nikoli VWAP; objem pro VWAP chybí.','',
        'compression_breakout: proražení čtyř předchozích M15 svíček po malém rozpětí (<=1.5ATR), signální rozpětí >=1.2ATR a close v horní/dolní čtvrtině za hranicí boxu0.1ATR. SL za signální svíčkou s0.2ATR bufferem. Bez EMA a bez pevného úvodního session rozpětí.','',
        'Při vstupu na následujícím M5 open se stop odvozený z uzavřené svíčky zachová; pokud je mimo0.5–2ATR nebo pro obrat chybí prostor do 2R, vstup se odmítne. Tím se nepředstírá fill na už známém close.','',
        'Žádná optimalizace parametrů podle výsledků; tři předem napsané hypotézy, tři session, dva cenové nákladové scénáře. Srovnání více variant samo vytváří výběrové zkreslení: nejlepší kombinace není ověřenou strategií. Srpen–říjen není nezávislý holdout, protože byl dříve zkoumán. Wilson intervaly TP rate v JSON jsou jen orientační při závislosti obchodů.','',
        'Více filtrů může zvýšit win rate nebo snížit počet obchodů, ale nelze předpokládat, že zvýší čistou očekávanou hodnotu. Ziskové uzavření před TP není výhra +2R. Výsledky neobsahují news filtr, historický makro kalendář ani skutečné broker bid/ask kotace. Riziko a DD nemají význam garance budoucích výsledků.','',
        '60 % zásahů +2R při40 % ztrát -1R odpovídá0.8R/obchod a PF3 před náklady při pouze těchto dvou výstupech. Jde o silnou hypotézu, ne parametr, který lze nastavit.','']
    (out/'report.md').write_text('\n'.join(lines))
    print('\n'.join(lines[:26]))
if __name__=='__main__':main()
