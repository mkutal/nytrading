import unittest
from pathlib import Path
from datetime import timedelta
from engine import Signal,Config,run,load
from discover import candidates,wilson
from test_engine import dt,series

class DiscoveryTests(unittest.TestCase):
 @unittest.skipUnless(all((Path(__file__).resolve().parents[1]/'data'/name).exists() for name in ('15min.json','1h.json')), 'Historical data is local; see data/README.md')
 def test_all_prototypes_are_prefix_invariant_on_actual_data(self):
  p=Path(__file__).resolve().parents[1]/'data'
  m=[b for b in load(p/'15min.json',15)[0] if b.time.weekday()<5]
  h=[b for b in load(p/'1h.json',60)[0] if b.time.weekday()<5]
  end=dt('2026-07-01')
  full=candidates(m,h)
  prefix=candidates([b for b in m if b.time+timedelta(minutes=15)<=end],
                    [b for b in h if b.time+timedelta(hours=1)<=end])
  for name in full:
   self.assertTrue(prefix[name],name)
   self.assertEqual(prefix[name],{t:v for t,v in full[name].items() if t<=end},name)
 def test_structural_stop_and_target_from_executable_entry(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000)
  _,tr,*_=run(bars,{t:Signal(1,10,3985,4100)},Config(),'day',t,t+timedelta(hours=12))
  self.assertEqual(tr[0]['sl'],3985)
  self.assertAlmostEqual(tr[0]['tp']-tr[0]['entry'],2*(tr[0]['entry']-3985))
 def test_insufficient_room_for_true_2R_rejected(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000)
  r,*_=run(bars,{t:Signal(1,10,3985,4010)},Config(),'day',t,t+timedelta(hours=12))
  self.assertEqual(r['trades'],0);self.assertEqual(r['rejected']['insufficient_2R_room'],1)
 def test_stop_inverted_by_gap_is_rejected(self):
  t=dt('2026-04-01T06:00:00');bars=series(t,144,4000)
  r,*_=run(bars,{t:Signal(1,10,4001)},Config(),'day',t,t+timedelta(hours=12))
  self.assertEqual(r['trades'],0);self.assertEqual(r['rejected']['invalid_structure_stop'],1)
 def test_small_sample_interval_is_wide(self):
  lo,hi=wilson(5,11);self.assertLess(lo,.3);self.assertGreater(hi,.6)
if __name__=='__main__':unittest.main()
