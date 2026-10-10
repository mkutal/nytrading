import unittest
from datetime import datetime, timezone
from pathlib import Path

from runtime_config import load_runtime

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def test_percent_is_converted_to_fraction_and_tp_is_loaded(self):
        runtime = load_runtime(ROOT, environ={"SIZING_RISK_PERCENT": "1", "TP_RR": "1.7"})
        self.assertEqual(runtime.costs.risk, .01)
        self.assertEqual(runtime.costs.target, 1.7)

    def test_ny_clock_follows_dst(self):
        window = load_runtime(ROOT, environ={"NEW_YORK_START": "10:00", "NEW_YORK_END": "13:30"}).window
        for date, utc_hour in (("2026-01-05", 15), ("2026-07-06", 14)):
            start, end, flat = window.times(datetime.fromisoformat(date).replace(tzinfo=timezone.utc))
            self.assertEqual(start.hour, utc_hour)
            self.assertEqual((end-start).total_seconds(), 3.5*3600)
            self.assertEqual(end, flat)

    def test_invalid_risk_and_target_fail_before_simulation(self):
        for env in ({"SIZING_RISK_PERCENT": "100"}, {"SIZING_RISK_PERCENT": "nan"},
                    {"TP_RR": "0"}, {"INITIAL_EQUITY_USD": "inf"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                load_runtime(ROOT, environ=env)

    def test_invalid_session_fails_before_simulation(self):
        for env in ({"NEW_YORK_START": "13:30", "NEW_YORK_END": "10:00"},
                    {"NEW_YORK_START": "25:00"}, {"NEW_YORK_TIMEZONE": "UTC"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                load_runtime(ROOT, environ=env)

    def test_data_mount_is_used_and_secrets_are_ignored(self):
        runtime = load_runtime(ROOT, environ={"DATA_DIR": "/data",
            "TWELVE_DATA_API_KEY": "unused-test-placeholder",
            "DISCORD_WEBHOOK_URL": "unused-test-placeholder"})
        self.assertEqual(runtime.data_dir, Path("/data"))
        self.assertNotIn("unused-test-placeholder", repr(runtime))


if __name__ == "__main__":
    unittest.main()
