"""NY Trading: offline NY-only entry point. No live execution or notifications."""
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from engine import load,run,write_csv
from discover import candidates
from runtime_config import load_runtime

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=('working','historical_candidate'),default='working')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    try:
        runtime=load_runtime(root,args.profile)
    except (ValueError,TypeError) as exc:
        parser.error(str(exc))
    c,window=runtime.costs,runtime.window
    missing=[name+'.json' for name in ('5min','15min','1h')
             if not (runtime.data_dir/(name+'.json')).is_file()]
    if missing:
        parser.error('Missing local OHLC files: '+', '.join(missing)+'. See data/README.md and DATA_DIR.')
    bars={k:load(runtime.data_dir/(k+'.json'),n)[0] for k,n in [('5min',5),('15min',15),('1h',60)]}
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
