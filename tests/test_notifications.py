import io
import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from engine import Bar, Signal, UTC, plan_entry
from live_clients import DeliveryError, FeedError
from live_state import State
from notifications import flush, message, on_clock, result_message
from runtime_config import load_runtime
from scanner import cycle, log_handlers, main

ROOT = Path(__file__).resolve().parents[1]


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name), 100000)
        self.runtime = load_runtime(ROOT, environ={})
        self.when = datetime(2026, 7, 6, 14, tzinfo=UTC)
        self.deadline = self.runtime.window.times(self.when)[2]
        self.discord = Mock()
        self.discord.send.return_value = "receipt"

    def tearDown(self):
        self.state.release()
        self.tmp.cleanup()

    def reopen(self):
        self.state.release()
        self.state = State(Path(self.tmp.name), 100000)

    def open_position(self, side=1):
        self.state.day_at(self.when)
        setup = Signal(side, 10, 3985 if side == 1 else 4015,
                       4100 if side == 1 else 3900)
        planned, rejection = plan_entry(setup, 4000, 100000, 100000, self.runtime.costs)
        self.assertIsNone(rejection)
        self.state.reserve("fixture", self.when, self.deadline, planned, self.runtime.costs)
        self.state.delivery_status("fixture", "sent", "entry-receipt")
        return planned

    def test_entry_matches_layout_lots_and_only_prague_times(self):
        for side in (1, -1):
            setup = Signal(side, 10, 3985 if side == 1 else 4015,
                           4100 if side == 1 else 3900)
            planned, _ = plan_entry(setup, 4000, 100000, 100000, self.runtime.costs)
            content = message(self.when, planned, self.runtime.costs, 100000,
                              self.deadline, self.runtime.window)
            self.assertIn("🟢 BUY" if side == 1 else "🔴 SELL", content)
            self.assertIn("New York · 16:00 Prague", content)
            self.assertIn("**Take profit · 100% · 1.7R**", content)
            self.assertIn("**Volume (total)**  0.65 lots", content)
            self.assertIn("**Price risk at SL**  $988.00", content)
            self.assertIn("**Planned risk incl. costs**  $995.15", content)
            self.assertIn("1% of $100,000.00", content)
            self.assertIn("Close at session end 19:30 · Prague time", content)
            self.assertIn("Europe/Prague", content)
            self.assertNotIn("UTC", content)
            self.assertNotIn("10:00", content)
            self.assertNotIn("13:30", content)
            self.assertNotIn("BULLISH", content)
            self.assertLess(len(content), 2000)

    def test_end_notice_with_no_trades_once_across_restart(self):
        on_clock(self.when, self.runtime.window, self.state)
        flush(self.state, self.when, self.discord)
        self.discord.send.assert_not_called()
        self.reopen()
        on_clock(self.deadline, self.runtime.window, self.state)
        flush(self.state, self.deadline, self.discord)
        self.assertIn("Ending the session", self.discord.send.call_args.args[0])
        self.assertIn("19:30 Prague", self.discord.send.call_args.args[0])
        self.reopen()
        on_clock(self.deadline + timedelta(seconds=20), self.runtime.window, self.state)
        flush(self.state, self.deadline + timedelta(seconds=20), self.discord)
        self.discord.send.assert_called_once()

    def test_no_weekend_or_startup_historical_session_spam(self):
        for when in (self.when - timedelta(days=2), self.deadline + timedelta(minutes=10)):
            on_clock(when, self.runtime.window, self.state)
            flush(self.state, when, self.discord)
        self.discord.send.assert_not_called()
        self.assertEqual(self.state.data["sessions"], {})

    def test_prague_session_end_follows_dst_mismatch(self):
        for day, hour, expected in ((12, 14, "19:30"), (26, 14, "18:30")):
            when = datetime(2026, 10, day, hour, tzinfo=UTC)
            deadline = self.runtime.window.times(when)[2]
            on_clock(when, self.runtime.window, self.state)
            on_clock(deadline, self.runtime.window, self.state)
            flush(self.state, deadline, self.discord)
            content = self.discord.send.call_args.args[0]
            self.assertIn(expected + " Prague", content)
            self.assertNotIn("13:30", content)

    def test_deadline_notice_then_model_result_with_lots_and_net_r(self):
        planned = self.open_position()
        bars = [Bar(self.when + timedelta(minutes=5*i), 4000, 4001, 3999, 4000)
                for i in range(43)]
        cycle(self.deadline, {"5min": bars}, self.runtime, self.state, self.discord)
        contents = [call.args[0] for call in self.discord.send.call_args_list]
        self.assertEqual(len(contents), 2)
        self.assertIn("Ending the session", contents[0])
        self.assertIn("SESSION CLOSED", contents[1])
        self.assertIn("0.65 lots", contents[1])
        trade = self.state.data["trades"][0]
        self.assertEqual(trade["reason"], "DEADLINE")
        self.assertAlmostEqual(trade["r"], trade["pnl"] / planned["risk_usd"])
        self.assertIn(f"{trade['r']:+.2f}R", contents[1])
        self.assertIn("19:30 Prague", contents[1])
        self.reopen()
        cycle(self.deadline + timedelta(seconds=20), {"5min": bars}, self.runtime,
              self.state, self.discord)
        self.assertEqual(self.discord.send.call_count, 2)

    def test_take_profit_result_survives_restart_before_delivery(self):
        p = self.open_position()
        hit = Bar(self.when, 4000, p["tp"] + 1, 3999, p["tp"])
        self.state.advance([hit], self.when + timedelta(minutes=5))
        self.reopen()
        flush(self.state, self.when + timedelta(minutes=5), self.discord)
        self.assertIn("TAKE PROFIT HIT", self.discord.send.call_args.args[0])
        self.assertGreater(self.state.data["trades"][0]["pnl"], 0)
        flush(self.state, self.when + timedelta(minutes=6), self.discord)
        self.discord.send.assert_called_once()

    def test_ambiguous_touch_reports_conservative_stop(self):
        p = self.open_position()
        hit = Bar(self.when, 4000, p["tp"] + 1, p["sl"] - 1, 4000)
        self.state.advance([hit], self.when + timedelta(minutes=5))
        flush(self.state, self.when + timedelta(minutes=5), self.discord)
        content = self.discord.send.call_args.args[0]
        self.assertIn("STOP LOSS HIT", content)
        self.assertIn("conservative SL-first", content)
        self.assertLess(self.state.data["trades"][0]["r"], 0)

    def test_session_notice_survives_feed_outage_without_claiming_trade_closed(self):
        self.open_position()
        with self.assertRaises(FeedError):
            cycle(self.deadline, {"5min": []}, self.runtime, self.state, self.discord)
        self.discord.send.assert_called_once()
        content = self.discord.send.call_args.args[0]
        self.assertIn("Close any remaining position", content)
        self.assertNotIn("Reference exit", content)
        self.assertIsNotNone(self.state.data["position"])
        self.assertEqual(self.state.data["trades"], [])

    def test_ambiguous_notice_delivery_is_not_retried_after_restart(self):
        on_clock(self.when, self.runtime.window, self.state)
        on_clock(self.deadline, self.runtime.window, self.state)
        self.discord.send.side_effect = DeliveryError("Discord delivery unconfirmed")
        with self.assertLogs("nytrading", level="ERROR"):
            flush(self.state, self.deadline, self.discord)
        self.reopen()
        flush(self.state, self.deadline, self.discord)
        self.discord.send.assert_called_once()
        self.assertEqual(next(iter(self.state.data["notifications"].values()))["status"],
                         "delivery_unconfirmed")

    def test_dry_run_notices_never_send_and_are_separate(self):
        on_clock(self.when, self.runtime.window, self.state)
        on_clock(self.deadline, self.runtime.window, self.state)
        flush(self.state, self.deadline, self.discord, dry_run=True)
        self.discord.send.assert_not_called()
        self.assertEqual(next(iter(self.state.data["notifications"].values()))["status"], "dry_run")

    def test_pending_old_session_is_retired_instead_of_sending_close_instruction(self):
        on_clock(self.when, self.runtime.window, self.state)
        on_clock(self.deadline, self.runtime.window, self.state)
        flush(self.state, self.deadline + timedelta(days=1), self.discord)
        self.discord.send.assert_not_called()
        self.assertEqual(next(iter(self.state.data["notifications"].values()))["status"], "expired")

    def test_state_upgrade_preserves_equity_open_position_and_seen_signals(self):
        self.open_position()
        original = json.loads(self.state.path.read_text())
        legacy = {key: value for key, value in original.items()
                  if key not in ("sessions", "notifications")}
        self.state.path.write_text(json.dumps(legacy))
        self.reopen()
        for key in legacy:
            self.assertEqual(self.state.data[key], legacy[key])
        on_clock(self.deadline, self.runtime.window, self.state)
        flush(self.state, self.deadline, self.discord)
        self.discord.send.assert_called_once()
        self.assertIn("Ending the session", self.discord.send.call_args.args[0])

    def test_worker_sends_session_end_when_idle_without_market_data_calls(self):
        import os
        on_clock(self.when, self.runtime.window, self.state)
        self.state.release()
        env = {"TWELVE_DATA_API_KEY": "test-placeholder", "DATA_DIR": self.tmp.name,
               "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/test-placeholder"}
        try:
            with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["scanner.py", "--once"]), \
                 patch("scanner.datetime") as clock, patch("scanner.signal.signal"), \
                 patch("scanner.TwelveData") as client, patch("scanner.Discord", return_value=self.discord), \
                 self.assertLogs("nytrading", level="INFO"):
                clock.now.return_value = self.deadline
                self.assertEqual(main(), 0)
                client.return_value.bars.assert_not_called()
                self.discord.send.assert_called_once()
                self.assertIn("Ending the session", self.discord.send.call_args.args[0])
        finally:
            self.state = State(Path(self.tmp.name), 100000)


class LoggingTests(unittest.TestCase):
    def test_info_goes_to_stdout_and_errors_remain_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        logger = logging.Logger("isolated-logging-test", level=logging.INFO)
        with patch("sys.stdout", out), patch("sys.stderr", err):
            logger.handlers = log_handlers()
            logger.info("READY fixture")
            logger.warning("WARNING fixture")
            logger.error("ERROR fixture")
        self.assertEqual(out.getvalue().strip(), "READY fixture")
        self.assertIn("WARNING fixture", err.getvalue())
        self.assertIn("ERROR fixture", err.getvalue())
        self.assertNotIn("READY", err.getvalue())


if __name__ == "__main__":
    unittest.main()
