"""NY Trading: offline NY-only entry point. No live execution or notifications."""
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from engine import Config,Window,load,run,write_csv
from discover import candidates

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=('working','historical_candidate'),default='working')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    settings=json.loads((root/'settings.json').read_text())
    if settings['london_enabled'] or settings['broker_execution_enabled'] or settings['discord_enabled']:
        raise ValueError('This NY-only offline entry point cannot enable other sessions or live services')
    if args.profile=='working':
        target=settings['target_r'];begin=settings['entry_start']
    else:
        target=settings['challenger']['target_r'];begin=settings['challenger']['entry_start']
    h,m=map(int,begin.split(':'));end_h,end_m=map(int,settings['force_flat'].split(':'))
    window=Window('ny','America/New_York',h,m,0,(end_h-h)*60+end_m-m,(end_h-h)*60+end_m-m)
    c=Config(initial=settings['initial_usd'],risk=settings['risk_fraction'],target=target,
             minimum_room_r=settings['minimum_room_r'],commission=settings['commission_roundtrip_per_lot'])
    bars={k:load(root/'data'/(k+'.json'),n)[0] for k,n in [('5min',5),('15min',15),('1h',60)]}
    sig=candidates([b for b in bars['15min'] if b.time.weekday()<5],
                   [b for b in bars['1h'] if b.time.weekday()<5])['band_reversion']
    summary,trades,equity,_=run(bars['5min'],sig,c,'ny',datetime(2026,4,1,tzinfo=timezone.utc),
            datetime(2026,10,3,tzinfo=timezone.utc),windows=(window,))
    summary.update(project='NY Trading',profile=args.profile,status='research_only_not_deployed',
                   london_enabled=False,independent_validation=False)
    out=root/'results'/args.profile;out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
    write_csv(out/'trades.csv',trades);write_csv(out/'equity.csv',equity)
    print(json.dumps({k:summary[k] for k in ('project','profile','trades','net_usd','win_rate','profit_factor')},indent=2))
if __name__=='__main__':main()
