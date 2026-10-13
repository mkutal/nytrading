import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from engine import Bar, Signal, load, plan_entry, run
from discover import candidates
from live_clients import TwelveData, Discord, FeedError, DeliveryError, webhook_url
from live_state import State, StateError
from runtime_config import load_runtime
from scanner import active, cycle, latest_signal, main

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = load_runtime(ROOT,environ={})
        self.when = datetime(2026,7,6,14,tzinfo=UTC)  # 10:00 NY
        self.state = State(Path(self.tmp.name),100000)
        self.setup = Signal(1,10,3985,4100)
        self.bars = {
            "5min": [Bar(self.when,4000,4001,3999,4000)],
            "15min": [Bar(self.when-timedelta(minutes=15*i),4000,4001,3999,4000) for i in range(1000,0,-1)],
            "1h": [Bar(self.when-timedelta(hours=i),4000,4001,3999,4000) for i in range(121,0,-1)],
        }

    def tearDown(self):
        self.state.release()
        self.tmp.cleanup()

    def prototypes(self):
        return patch("scanner.candidates",return_value={"band_reversion":{self.when:self.setup}})

    def test_window_weekends_dst_and_exclusive_end(self):
        self.assertTrue(active(self.when,self.runtime.window))
        self.assertFalse(active(self.when-timedelta(seconds=1),self.runtime.window))
        self.assertFalse(active(self.when+timedelta(hours=3,minutes=30),self.runtime.window))
        self.assertFalse(active(self.when-timedelta(days=2),self.runtime.window))

    def test_stale_signal_never_sends(self):
        with self.prototypes() as mocked:
            self.assertIsNone(latest_signal(self.when+timedelta(seconds=91),self.bars,self.runtime.window))
            mocked.assert_not_called()

    def test_notification_latency_cannot_turn_into_a_stale_entry(self):
        discord = unittest.mock.Mock()
        discord.send.return_value = "start-receipt"
        with self.prototypes() as mocked:
            result = cycle(self.when, self.bars, self.runtime, self.state, discord,
                refresh_now=lambda: self.when + timedelta(seconds=91))
        self.assertIsNone(result)
        mocked.assert_not_called()
        discord.send.assert_called_once()
        self.assertIn("Session started", discord.send.call_args.args[0])
        self.assertIsNone(self.state.data["position"])

    def test_unclosed_candles_are_removed(self):
        self.bars["15min"].append(Bar(self.when,1,1,1,1))
        self.bars["1h"].append(Bar(self.when,1,1,1,1))
        with self.prototypes() as mocked:
            self.assertIsNotNone(latest_signal(self.when,self.bars,self.runtime.window))
            m,h=mocked.call_args.args
            self.assertTrue(all(b.time+timedelta(minutes=15)<=self.when for b in m))
            self.assertTrue(all(b.time+timedelta(hours=1)<=self.when for b in h))

    def test_delayed_closed_candle_and_missing_entry_fail_closed(self):
        with self.prototypes():
            self.bars["5min"]=[]
            with self.assertRaises(FeedError):latest_signal(self.when,self.bars,self.runtime.window)
            self.bars["15min"]=[]
            with self.assertRaises(FeedError):latest_signal(self.when,self.bars,self.runtime.window)

    def test_send_once_and_restart_keeps_reservation(self):
        discord = unittest.mock.Mock()
        discord.send.return_value="123"
        with self.prototypes():
            content=cycle(self.when,self.bars,self.runtime,self.state,discord)
            self.assertIn("Take profit · 100% · 1.7R",content)
            cycle(self.when+timedelta(seconds=20),self.bars,self.runtime,self.state,discord)
        self.assertEqual(discord.send.call_count, 2)
        self.assertIn("Session started", discord.send.call_args_list[0].args[0])
        self.assertEqual(discord.send.call_args_list[1].args[0], content)
        self.state.release()
        self.state=State(Path(self.tmp.name),100000)
        with self.prototypes():cycle(self.when+timedelta(seconds=30),self.bars,self.runtime,self.state,discord)
        self.assertEqual(discord.send.call_count, 2)

    def test_ambiguous_delivery_is_not_blindly_retried(self):
        discord=unittest.mock.Mock()
        discord.send.side_effect=["start-receipt", DeliveryError("Discord delivery unconfirmed")]
        with self.prototypes():
            cycle(self.when,self.bars,self.runtime,self.state,discord)
            cycle(self.when+timedelta(seconds=20),self.bars,self.runtime,self.state,discord)
        self.assertEqual(discord.send.call_count, 2)
        self.assertIn("Session started", discord.send.call_args_list[0].args[0])
        self.assertIn("BUY · XAUUSD", discord.send.call_args_list[1].args[0])
        self.assertEqual(next(iter(self.state.data["seen"].values()))["status"],"delivery_unconfirmed")

    def test_dry_run_does_not_call_discord(self):
        discord=unittest.mock.Mock()
        with self.prototypes():cycle(self.when,self.bars,self.runtime,self.state,discord,True)
        discord.send.assert_not_called()

    def test_single_worker_lock(self):
        with self.assertRaises(StateError):State(Path(self.tmp.name),100000)

    def open_position(self):
        self.state.day_at(self.when)
        planned,_=plan_entry(self.setup,4000,100000,100000,self.runtime.costs)
        _,_,deadline=self.runtime.window.times(self.when)
        self.state.reserve("fixture",self.when,deadline,planned,self.runtime.costs)
        return planned

    def test_stop_wins_when_both_levels_hit(self):
        p=self.open_position()
        both=Bar(self.when,4000,p["tp"]+10,p["sl"]-10,4000)
        self.state.advance([both],self.when+timedelta(minutes=5))
        self.assertEqual(self.state.data["trades"][0]["reason"],"SL_BOTH")
        self.assertIsNone(self.state.data["position"])

    def test_missing_paper_bar_halts_instead_of_fabricating_outcome(self):
        self.open_position()
        self.state.advance([],self.when+timedelta(minutes=10))
        self.assertEqual(self.state.data["halted"],"missing_paper_execution_bar")
        self.assertIsNotNone(self.state.data["position"])

    def test_delayed_m5_does_not_finalize_old_forming_bar(self):
        self.open_position()
        with self.assertRaises(FeedError):
            cycle(self.when+timedelta(minutes=5),self.bars,self.runtime,self.state,dry_run=True)
        self.assertEqual(self.state.data["position"]["cursor"],self.when.isoformat())
        self.assertFalse(self.state.data["trades"])

    def test_gap_loss_halts_virtual_account(self):
        self.open_position()
        gap=Bar(self.when,3000,3001,2999,3000)
        self.state.advance([gap],self.when+timedelta(minutes=5))
        self.assertEqual(self.state.data["halted"],"paper_maximum_loss")

    def test_deadline_closes_without_tp_sl(self):
        self.open_position()
        bars=[Bar(self.when+timedelta(minutes=5*i),4000,4001,3999,4000) for i in range(42)]
        self.state.advance(bars,self.when+timedelta(hours=3,minutes=30))
        self.assertEqual(self.state.data["trades"][0]["reason"],"DEADLINE")


class HttpTests(unittest.TestCase):
    def test_provider_query_utc_and_values_validated(self):
        payload={"meta":{"timezone":"UTC","symbol":"XAU/USD"},"values":[
            {"datetime":"2026-07-06 14:00:00","open":"4000","high":"4001","low":"3999","close":"4000"}]}
        opener=unittest.mock.Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        bars=TwelveData("placeholder",opener).bars("5min",300)
        self.assertEqual(bars[0].time.tzinfo,UTC)
        self.assertIn("timezone=UTC",opener.call_args.args[0].full_url)

    def test_provider_errors_do_not_reveal_credentials(self):
        opener=unittest.mock.Mock(side_effect=URLError("secret-in-request-url"))
        with self.assertRaises(FeedError) as exc:TwelveData("secret-in-request-url",opener).bars("5min",300)
        self.assertNotIn("secret-in-request-url",str(exc.exception))

    def test_wrong_feed_timezone_rejected(self):
        opener=unittest.mock.Mock(return_value=io.BytesIO(json.dumps({"meta":{"timezone":"Europe/Prague","symbol":"XAU/USD"},"values":[]}).encode()))
        with self.assertRaises(FeedError):TwelveData("placeholder",opener).bars("5min",300)

    def test_webhook_host_restricted_and_confirmation_required(self):
        self.assertIn("wait=true",webhook_url("https://discord.com/api/webhooks/123/test-placeholder"))
        with self.assertRaises(ValueError):webhook_url("https://example.com/api/webhooks/123/test-placeholder")
        opener=unittest.mock.Mock(return_value=io.BytesIO(b'{"id":"456"}'))
        self.assertEqual(Discord("https://discord.com/api/webhooks/123/test-placeholder",opener).send("fixture"),"456")
        payload=json.loads(opener.call_args.args[0].data)
        self.assertEqual(payload["allowed_mentions"],{"parse":[]})


class WorkerStartupTests(unittest.TestCase):
    def test_railway_without_volume_stops_without_http(self):
        env={"TWELVE_DATA_API_KEY":"local-placeholder","SCANNER_DRY_RUN":"true",
             "RAILWAY_ENVIRONMENT_ID":"local-test","DATA_DIR":"/data"}
        with patch.dict(os.environ,env,clear=True),patch("sys.argv",["scanner.py","--check-config"]),\
             patch("scanner.TwelveData") as client, self.assertLogs("nytrading",level="ERROR") as logs:
            self.assertEqual(main(),2)
            client.assert_not_called()
            self.assertIn("Volume",logs.output[0])

    def test_mounted_volume_configuration_validates_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            env={"TWELVE_DATA_API_KEY":"local-placeholder","SCANNER_DRY_RUN":"true",
                 "RAILWAY_ENVIRONMENT_ID":"local-test","DATA_DIR":directory,
                 "RAILWAY_VOLUME_MOUNT_PATH":directory}
            with patch.dict(os.environ,env,clear=True),patch("sys.argv",["scanner.py","--check-config"]),\
                 patch("scanner.TwelveData") as client,patch("sys.stdout",new_callable=io.StringIO) as output:
                self.assertEqual(main(),0)
                self.assertEqual(json.loads(output.getvalue())["status"],"configuration_valid")
                client.assert_not_called()

    def test_idle_worker_starts_without_fetching_or_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            env={"TWELVE_DATA_API_KEY":"local-placeholder","SCANNER_DRY_RUN":"true","DATA_DIR":directory}
            with patch.dict(os.environ,env,clear=True),patch("sys.argv",["scanner.py","--once"]),\
                 patch("scanner.datetime") as clock,patch("scanner.signal.signal"),\
                 patch("scanner.TwelveData") as client,patch("scanner.Discord") as discord,\
                 self.assertLogs("nytrading",level="INFO") as logs:
                clock.now.return_value=datetime(2026,7,4,14,tzinfo=UTC)
                self.assertEqual(main(),0)
                client.return_value.bars.assert_not_called()
                discord.assert_not_called()
                self.assertTrue(any("NY_TRADING_READY" in line for line in logs.output))


class HistoricalPaperReplay(unittest.TestCase):
    @unittest.skipUnless(all((ROOT/"data"/(name+".json")).exists() for name in ("5min","15min","1h")),"Historical OHLC stays local")
    def test_paper_lifecycle_matches_selected_backtest(self):
        bars={name:load(ROOT/"data"/(name+".json"),minutes)[0] for name,minutes in (("5min",5),("15min",15),("1h",60))}
        runtime=load_runtime(ROOT,environ={})
        sigs=candidates([b for b in bars["15min"] if b.time.weekday()<5],[b for b in bars["1h"] if b.time.weekday()<5])["band_reversion"]
        start=datetime(2026,4,1,tzinfo=UTC);end=datetime(2026,10,3,tzinfo=UTC)
        expected=run(bars["5min"],sigs,runtime.costs,"ny",start,end,windows=(runtime.window,))[0]
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory),runtime.costs.initial,True)
            try:
                with patch("scanner.candidates",return_value={"band_reversion":sigs}),patch("scanner.LOG"),patch("notifications.LOG"):
                    for i,b in enumerate(bars["5min"]):
                        if not start<=b.time<end:continue
                        if not state.data["position"] and (b.time not in sigs or not active(b.time,runtime.window)):continue
                        snapshot=dict(bars)
                        snapshot["5min"]=bars["5min"][max(0,i-300):i+1]
                        cycle(b.time,snapshot,runtime,state,dry_run=True)
                self.assertIsNone(state.data["position"])
                self.assertIsNone(state.data["halted"])
                self.assertEqual(len(state.data["trades"]),expected["trades"])
                self.assertAlmostEqual(state.data["balance"],expected["final_equity"],places=6)
            finally:state.release()


if __name__=="__main__":unittest.main()
