"""Financial invariants and causality, with deterministic fixtures (no real feed)."""
import unittest
from datetime import datetime,timedelta,timezone
from dataclasses import replace
from engine import Bar,Config,ema,atr,signals,bounds,size,exit_bar,run
UTC=timezone.utc

def dt(s): return datetime.fromisoformat(s).replace(tzinfo=UTC)
def series(start,n,price=100):
 return [Bar(start+timedelta(minutes=5*i),price,price+.01,price-.01,price) for i in range(n)]
class EngineTests(unittest.TestCase):
 def test_ema_seed(self): self.assertEqual(ema([1,2,3,4],3),[None,None,2,3])
 def test_atr_wilder(self):
  bars=[Bar(dt('2026-04-01'),100,102,98,100)]*16
  self.assertEqual(atr(bars)[14:], [4,4])
 def test_config_rejects_nan(self):
  with self.assertRaises(ValueError): Config(spread=float('nan'))
 def test_config_risk_guard(self):
  with self.assertRaises(ValueError): Config(risk=.05)
 def test_size_includes_fees(self):
  c=Config();lots=size(100000,4000,10,c)
  self.assertLessEqual(lots*(100*(10+.05)+6),1000)
  self.assertLess(lots,.995)
 def test_margin_cap(self):
  c=Config(leverage=1)
  self.assertLessEqual(size(100000,5000,1,c)*5000*100,100000)
 def test_both_stop_first_long(self):
  p=dict(side=1,sl=90,tp=120);b=Bar(dt('2026-04-01'),100,130,80,110)
  price,status,_=exit_bar(p,b,Config(spread=0));self.assertEqual(status,'SL_BOTH');self.assertEqual(price,89.95)
 def test_both_stop_first_short(self):
  p=dict(side=-1,sl=110,tp=80);b=Bar(dt('2026-04-01'),100,120,70,90)
  price,status,_=exit_bar(p,b,Config(spread=0));self.assertEqual(status,'SL_BOTH');self.assertEqual(price,110.05)
 def test_stop_gap(self):
  p=dict(side=1,sl=90,tp=120);b=Bar(dt('2026-04-01'),80,85,79,81)
  self.assertEqual(exit_bar(p,b,Config(spread=0))[:2],(79.95,'SL_GAP'))
 def test_tp_gap_no_improvement(self):
  p=dict(side=1,sl=90,tp=120);b=Bar(dt('2026-04-01'),130,135,129,131)
  self.assertEqual(exit_bar(p,b,Config(spread=0))[:2],(120,'TP_GAP'))
 def test_dst_ny_mismatch_weeks(self):
  self.assertEqual(bounds(dt('2026-03-20'), 'ny')[0].hour,12)
  self.assertEqual(bounds(dt('2026-03-20'), 'day')[0].hour,7)
  self.assertEqual(bounds(dt('2026-04-03'), 'day')[0].hour,6)
 def test_london_local_DST(self):
  self.assertEqual(bounds(dt('2026-01-05'),'london')[0].hour,8)
  self.assertEqual(bounds(dt('2026-06-05'),'london')[0].hour,7)
 def test_closed_hourly_only_and_prefix_invariance(self):
  start=dt('2026-03-01');h=[Bar(start+timedelta(hours=i),100+i*.1,101+i*.1,99+i*.1,100.1+i*.1) for i in range(500)]
  m=[Bar(start+timedelta(minutes=15*i),100+i*.025,101+i*.025,99+i*.025,100.1+i*.025) for i in range(2000)]
  cutoff=start+timedelta(hours=470)
  full=signals(m,h);prefix=signals([b for b in m if b.time+timedelta(minutes=15)<=cutoff],[b for b in h if b.time+timedelta(hours=1)<=cutoff])
  self.assertTrue(prefix);self.assertEqual(prefix,{t:v for t,v in full.items() if t<=cutoff})
  changed=h[:];changed[-1]=replace(changed[-1],close=99999,high=99999)
  self.assertEqual(prefix,{t:v for t,v in signals(m,changed).items() if t<=cutoff})
 def simulate(self,side=1,spread=.3):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000)
  c=Config(spread=spread)
  return run(bars,{t:(side,10)},c,'day',t,t+timedelta(hours=12))
 def test_flat_trade_charges_spread_slippage_and_full_fee(self):
  r,tr,e,_=self.simulate();p=tr[0]
  self.assertEqual(p['reason'],'DEADLINE')
  self.assertAlmostEqual(p['pnl'],-p['lots']*(100*(.3+2*.05)+6))
  self.assertAlmostEqual(r['net_usd'],p['pnl'])
 def test_short_cost_symmetry(self):
  a=self.simulate(1)[0];b=self.simulate(-1)[0];self.assertAlmostEqual(a['net_usd'],b['net_usd'])
 def test_single_concurrent_position(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000)
  r,tr,*_=run(bars,{b.time:(1,10) for b in bars},Config(),'day',t,t+timedelta(hours=12))
  self.assertEqual(len(tr),1)
 def test_signal_at_end_cannot_enter(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,145,4000)
  r,*_=run(bars,{t+timedelta(hours=12):(1,10)},Config(),'day',t,t+timedelta(hours=13))
  self.assertEqual(r['trades'],0)
 def test_no_entry_before_signal(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000);signal=t+timedelta(minutes=15)
  _,tr,*_=run(bars,{signal:(1,10)},Config(),'day',t,t+timedelta(hours=12))
  self.assertEqual(tr[0]['entry_time'],signal.isoformat())
 def test_weekend_no_entries(self):
  t=dt('2026-04-04T06:00:00');bars=series(t,144,4000)
  r,*_=run(bars,{t:(1,10)},Config(),'day',t,t+timedelta(hours=12));self.assertEqual(r['trades'],0)
 def test_gap_open_breach_and_stops_account(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000)
  bars[1]=Bar(t+timedelta(minutes=5),3000,3001,2999,3000)
  r,tr,*_=run(bars,{t:(1,10)},Config(),'day',t,t+timedelta(hours=12))
  self.assertTrue(r['ftmo_breach']);self.assertIn('maximum_loss',r['breaches'][0]['rules']);self.assertEqual(len(tr),1)
 def test_aggregate_losses_guard(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000)
  bars=[replace(b,low=3980) for b in bars]
  r,tr,*_=run(bars,{b.time:(1,10) for b in bars},Config(),'day',t,t+timedelta(hours=12))
  self.assertEqual(len(tr),4);self.assertFalse(r['ftmo_breach']);self.assertGreater(r['rejected']['risk_headroom'],0)
 def test_missing_execution_not_invented(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000);del bars[1]
  with self.assertRaisesRegex(RuntimeError,'Missing execution'):run(bars,{t:(1,10)},Config(),'day',t,t+timedelta(hours=12))
 def test_future_gap_does_not_suppress_prior_entry(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000);bars[0]=replace(bars[0],low=3980);del bars[60]
  r,tr,*_=run(bars,{t:(1,10)},Config(),'day',t,t+timedelta(hours=12))
  self.assertEqual(len(tr),1)
if __name__=='__main__':unittest.main()
