"""NY-only Twelve Data -> Discord paper signal worker. No broker connection."""
import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from discover import candidates
from engine import UTC, plan_entry
from live_clients import TwelveData, Discord, FeedError, DeliveryError, webhook_url
from live_state import State, StateError
from runtime_config import load_runtime
from notifications import message, on_clock, flush

LOG = logging.getLogger("nytrading")
MAX_SIGNAL_AGE = 90


def floor_time(now, minutes):
    return now.replace(minute=now.minute//minutes*minutes, second=0, microsecond=0)


def active(now, window):
    start, end, _ = window.times(now)
    return now.astimezone(ZoneInfo(window.zone)).weekday() < 5 and start <= now < end


def latest_signal(now, bars, window):
    """Reject old/unclosed signals; a current M5 open is an indicative reference."""
    when = floor_time(now, 15)
    if not active(now, window) or not 0 <= (now-when).total_seconds() <= MAX_SIGNAL_AGE:
        return None
    m15 = [b for b in bars["15min"] if b.time.weekday()<5 and b.time+timedelta(minutes=15)<=now]
    h1 = [b for b in bars["1h"] if b.time.weekday()<5 and b.time+timedelta(hours=1)<=now]
    if (not m15 or not h1 or m15[-1].time != when-timedelta(minutes=15)
            or h1[-1].time != floor_time(now,60)-timedelta(hours=1)):
        raise FeedError("Closed M15/H1 candles are delayed; no signal sent")
    if len(m15)<101 or len(h1)<21:
        raise FeedError("Insufficient M15/H1 warmup history; no signal sent")
    execution = next((b for b in bars["5min"] if b.time == when), None)
    if execution is None:
        raise FeedError("Current M5 reference is unavailable; no signal sent")
    setup = candidates(m15, h1)["band_reversion"].get(when)
    return (when, setup, execution.open) if setup is not None else None


class Feed:
    """Refresh M5 per five-minute boundary, M15/H1 only when they close."""
    def __init__(self, client):
        self.client = client
        self.cache = {}
        self.keys = {}

    def snapshot(self, now, indicators=True):
        intervals = (("5min",5,300), ("15min",15,1000), ("1h",60,500)) if indicators else (("5min",5,300),)
        for name, minutes, count in intervals:
            key = floor_time(now, minutes)
            if self.keys.get(name) == key:
                continue
            rows = self.client.bars(name, count)
            self.cache[name] = rows
            expected = key if name == "5min" else key-timedelta(minutes=minutes)
            # Missing/delayed boundary bars retry on the next poll, not a false entry.
            if any(b.time == expected for b in rows):
                self.keys[name] = key
        return self.cache


def cycle(now, bars, runtime, state, discord=None, dry_run=False, refresh_now=None):
    on_clock(now, runtime.window, state)
    flush(state, now, discord, dry_run)
    if state.data["position"] or active(now,runtime.window):
        if not any(b.time == floor_time(now,5) for b in bars.get("5min",[])):
            raise FeedError("M5 feed is delayed; paper exits/entries wait for fresh data")
    state.advance(bars.get("5min", []), now)
    flush(state, now, discord, dry_run)
    if refresh_now is not None:
        # Result delivery can take time; recheck freshness before an actionable entry.
        now = refresh_now()
    if state.data["position"] or state.data["halted"] or not active(now,runtime.window):
        return None
    result = latest_signal(now,bars,runtime.window)
    if result is None:
        return None
    when, setup, reference = result
    signal_id = "XAUUSD:band_reversion:"+when.isoformat()
    if signal_id in state.data["seen"]:
        return None
    balance = state.data["balance"]
    planned, rejection = plan_entry(setup, reference, balance,state.data["day_balance"],runtime.costs)
    if rejection:
        LOG.info("SIGNAL_REJECTED %s %s", signal_id, rejection)
        return None
    _, _, deadline = runtime.window.times(when)
    if not state.reserve(signal_id,when,deadline,planned,runtime.costs):
        return None
    content = message(when,planned,runtime.costs,balance,deadline,runtime.window)
    if dry_run:
        state.delivery_status(signal_id,"dry_run")
        LOG.info("DRY_RUN_SIGNAL %s", content)
    else:
        # Save before POST: ambiguous network failures/restarts never blindly resend.
        state.delivery_status(signal_id,"delivery_unconfirmed")
        try:
            receipt = discord.send(content)
        except DeliveryError as exc:
            LOG.error("SIGNAL_DELIVERY_UNCONFIRMED %s %s; inspect Discord before manual retry",signal_id,exc)
            return content
        state.delivery_status(signal_id,"sent",receipt)
        LOG.info("SIGNAL_SENT %s",signal_id)
    return content


def log_handlers():
    """Keep INFO on stdout and actual warnings/errors on stderr."""
    stdout = logging.StreamHandler(sys.stdout)
    stdout.addFilter(lambda record: record.levelno < logging.WARNING)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)
    return [stdout, stderr]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run",action="store_true",help="Fetch data but never send Discord; separate state")
    parser.add_argument("--once",action="store_true",help="One poll and exit")
    parser.add_argument("--check-config",action="store_true",help="Validate environment without HTTP calls")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",
                        handlers=log_handlers())
    configuration_step = "NY times, TP and risk"
    try:
        runtime = load_runtime(Path(__file__).resolve().parent)
        dry_value = os.getenv("SCANNER_DRY_RUN","false").lower()
        if dry_value not in ("true","false"):
            raise ValueError("SCANNER_DRY_RUN must be true or false")
        dry_run = args.dry_run or dry_value == "true"
        configuration_step = "TWELVE_DATA_API_KEY in Railway Variables"
        key = os.getenv("TWELVE_DATA_API_KEY","").strip()
        if not key or key.upper() in {"YOUR_API_KEY","TVUJ_API_KLIC"}:
            raise ValueError("Set TWELVE_DATA_API_KEY in Railway Variables")
        webhook = os.getenv("DISCORD_WEBHOOK_URL","").strip()
        if not dry_run:
            configuration_step = "DISCORD_WEBHOOK_URL in Railway Variables"
            webhook_url(webhook)
        configuration_step = "Railway Volume mounted at DATA_DIR (/data)"
        if os.getenv("RAILWAY_ENVIRONMENT_ID") or os.getenv("RAILWAY_PROJECT_ID"):
            mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH","")
            if not mount or not runtime.data_dir.resolve().is_relative_to(Path(mount).resolve()):
                raise ValueError("Attach a Railway Volume at DATA_DIR (/data); state must persist")
        if args.check_config:
            print(json.dumps(dict(status="configuration_valid",session="NY",
                start=f"{runtime.window.open_hour:02}:{runtime.window.open_minute:02}",
                timezone=runtime.window.zone,target_r=runtime.costs.target,risk_fraction=runtime.costs.risk,
                mode="dry_run" if dry_run else "paper_signals")))
            return 0
        configuration_step = "persistent paper state in DATA_DIR; preserve existing files"
        state = State(runtime.data_dir,runtime.costs.initial,dry_run)
    except (ValueError,StateError,OSError,KeyError):
        # Avoid logging arbitrary configuration values or exception URLs.
        LOG.error("CONFIGURATION_ERROR: verify %s",configuration_step)
        return 2
    feed = Feed(TwelveData(key))
    discord = None if dry_run else Discord(webhook)
    stop = threading.Event()
    for event in (signal.SIGTERM,signal.SIGINT):
        signal.signal(event,lambda *_:stop.set())
    LOG.info("NY_TRADING_READY mode=%s zone=%s TP=%s risk=%s", "dry_run" if dry_run else "paper_signals",
             runtime.window.zone,runtime.costs.target,runtime.costs.risk)
    last_status = None
    try:
        while not stop.is_set():
            now = datetime.now(UTC)
            on_clock(now, runtime.window, state)
            flush(state, now, discord, dry_run)
            entered = active(now,runtime.window)
            status = "paper_halted" if state.data["halted"] else "active" if entered else "waiting_for_NY"
            if status != last_status:
                LOG.info("STATUS %s%s",status,
                         " reason="+state.data["halted"] if state.data["halted"] else "")
                last_status = status
            if (entered or state.data["position"]) and not state.data["halted"]:
                try:
                    fresh_signal = entered and (now-floor_time(now,15)).total_seconds()<=MAX_SIGNAL_AGE
                    bars = feed.snapshot(now,indicators=fresh_signal)
                    cycle(datetime.now(UTC),bars,runtime,state,discord,dry_run,
                          refresh_now=lambda: datetime.now(UTC))
                except FeedError as exc:
                    LOG.warning("FEED_UNAVAILABLE %s",exc)
                    if stop.wait(60):
                        break
                except (ValueError,StateError,OSError,KeyError,TypeError):
                    LOG.error("STATE_OR_DATA_ERROR; worker stopped, preserve /data for inspection")
                    return 2
            if args.once:
                break
            stop.wait(20)
    finally:
        state.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
