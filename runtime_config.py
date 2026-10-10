"""Validated non-secret configuration for the offline NY backtest."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from engine import Config, Window


@dataclass(frozen=True)
class Runtime:
    costs: Config
    window: Window
    data_dir: Path


def _clock(value: str, name: str) -> int:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError(f"{name} must use HH:MM")
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def load_runtime(root: Path, profile: str = "working",
                 environ: Mapping[str, str] | None = None) -> Runtime:
    """Environment overrides settings.json; secrets are never read or returned."""
    env = os.environ if environ is None else environ
    settings = json.loads((root / "settings.json").read_text())
    if any(settings[name] for name in
           ("london_enabled", "broker_execution_enabled", "discord_enabled")):
        raise ValueError("This entry point supports offline NY research only")
    if profile == "working":
        target, begin = settings["target_r"], settings["entry_start"]
    elif profile == "historical_candidate":
        target = settings["challenger"]["target_r"]
        begin = settings["challenger"]["entry_start"]
    else:
        raise ValueError("Unknown research profile")

    zone = env.get("NEW_YORK_TIMEZONE", settings["timezone"])
    if zone != "America/New_York":
        raise ValueError("NEW_YORK_TIMEZONE must be America/New_York")
    start = _clock(env.get("NEW_YORK_START", begin), "NEW_YORK_START")
    end = _clock(env.get("NEW_YORK_END", settings["entry_end_exclusive"]),
                 "NEW_YORK_END")
    # An explicit session end also changes the mandatory flat deadline.
    close = _clock(env.get("NEW_YORK_END", settings["force_flat"]),
                   "NEW_YORK_END")
    window = Window("ny", zone, start // 60, start % 60, 0,
                    end - start, close - start)
    risk = (float(env["SIZING_RISK_PERCENT"]) / 100
            if "SIZING_RISK_PERCENT" in env else settings["risk_fraction"])
    costs = Config(
        initial=float(env.get("INITIAL_EQUITY_USD", settings["initial_usd"])),
        risk=risk,
        target=float(env.get("TP_RR", target)),
        minimum_room_r=settings["minimum_room_r"],
        commission=settings["commission_roundtrip_per_lot"],
    )
    data_dir = Path(env.get("DATA_DIR", str(root / "data")))
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    return Runtime(costs, window, data_dir)
