"""Persistent single-position paper account and send reservation, never broker orders."""
import fcntl
import json
import math
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from engine import Config, FIVE, PRAGUE, exit_bar


class StateError(RuntimeError):
    pass


class State:
    def __init__(self, directory: Path, initial: float, dry_run=False):
        directory.mkdir(parents=True, exist_ok=True)
        name = "ny-trading-dry-run" if dry_run else "ny-trading"
        self.path = directory / (name + ".json")
        self._lock = (directory / (name + ".lock")).open("a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            raise StateError("Another NY Trading worker holds this state volume") from None
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
                if (self.data.get("version") != 1 or self.data["initial"] != initial
                        or not all(math.isfinite(self.data[name]) for name in ("initial","balance","day_balance"))
                        or not isinstance(self.data["seen"],dict)
                        or not isinstance(self.data["trades"],list)):
                    raise StateError("Paper state/config mismatch; preserve and inspect the state")
                # Add notification state without changing the account or open trade.
                for field in ("sessions", "notifications"):
                    if not isinstance(self.data.setdefault(field, {}), dict):
                        raise StateError("Invalid paper notification state")
            else:
                self.data = dict(version=1, initial=initial, balance=initial, day=None,
                    day_balance=initial, position=None, seen={}, halted=None, trades=[],
                    sessions={}, notifications={})
                self.save()
        except Exception:
            self.release()
            raise

    def release(self):
        self._lock.close()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(self.data, handle, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def day_at(self, when):
        day = when.astimezone(PRAGUE).date().isoformat()
        if self.data["day"] != day:
            if self.data["position"]:
                self.data["halted"] = "unresolved_position_at_day_reset"
            else:
                self.data["day"] = day
                self.data["day_balance"] = self.data["balance"]

    def _mark(self, equity):
        d = self.data
        if equity < d["day_balance"] - d["initial"] * .05:
            d["halted"] = "paper_daily_loss"
        if equity < d["initial"] * .90:
            d["halted"] = "paper_maximum_loss"

    def _close(self, when, price, reason):
        d = self.data
        p = d["position"]
        c = Config(**p["costs"])
        gross = p["side"] * (price - p["entry"]) * p["units"]
        d["balance"] += gross - c.commission * p["lots"] / 2
        net = gross-c.commission*p["lots"]
        d["trades"].append(dict(signal_id=p["id"], entry_time=p["time"],
            side=p["side"], entry=p["entry"], sl=p["sl"], tp=p["tp"],
            lots=p["lots"], risk_usd=p["risk_usd"], exit_time=when.isoformat(),
            reason=reason, exit=price, pnl=net, r=net/p["risk_usd"], balance=d["balance"]))
        d["position"] = None
        self._mark(d["balance"])

    def advance(self, bars, now):
        """Consume completed M5 only, including missed bars after restart."""
        d = self.data
        if not d["position"]:
            self.day_at(now)
            self.save()
            return
        p = d["position"]
        cursor = datetime.fromisoformat(p["cursor"])
        completed = [b for b in bars if b.time >= cursor and b.time + FIVE <= now]
        if now >= cursor + FIVE and (not completed or completed[0].time != cursor):
            d["halted"] = "missing_paper_execution_bar"
            self.save()
            return
        for b in completed:
            p = d["position"]
            if p is None:
                break
            if b.time != datetime.fromisoformat(p["cursor"]):
                d["halted"] = "missing_paper_execution_bar"
                break
            c = Config(**p["costs"])
            deadline = datetime.fromisoformat(p["deadline"])
            if b.time >= deadline:
                self._close(b.time, b.open-p["side"]*(c.spread/2+c.slippage), "DEADLINE")
                break
            opened = b.open - p["side"]*c.spread/2
            self._mark(d["balance"]+p["side"]*(opened-p["entry"])*p["units"])
            price, reason, adverse = exit_bar(p, b, c)
            worst = adverse-p["side"]*(c.slippage if reason and reason.startswith("SL") else 0)
            self._mark(d["balance"]+p["side"]*(worst-p["entry"])*p["units"])
            if price is not None:
                self._close(b.time+FIVE, price, reason)
            elif b.time+FIVE >= deadline:
                self._close(b.time+FIVE, b.close-p["side"]*(c.spread/2+c.slippage), "DEADLINE")
            elif d["halted"]:
                self._close(b.time+FIVE, worst, "PAPER_FTMO_BREACH")
            else:
                p["cursor"] = (b.time+FIVE).isoformat()
        if not d["position"]:
            self.day_at(now)
        self.save()

    def reserve(self, signal_id, when, deadline, planned, costs):
        if signal_id in self.data["seen"] or self.data["position"] or self.data["halted"]:
            return False
        self.data["seen"][signal_id] = dict(status="reserved", time=when.isoformat())
        self.data["position"] = dict(planned, id=signal_id, time=when.isoformat(),
            cursor=when.isoformat(), deadline=deadline.isoformat(), costs=asdict(costs))
        self.data["balance"] -= costs.commission * planned["lots"] / 2
        self.save()
        return True

    def delivery_status(self, signal_id, status, message_id=None):
        record = self.data["seen"][signal_id]
        record["status"] = status
        if message_id is not None:
            record["message_id"] = message_id
        self.save()

    def track_session(self, key, start, cutoff, deadline):
        if key not in self.data["sessions"]:
            self.data["sessions"][key] = dict(start=start.isoformat(),
                cutoff=cutoff.isoformat(), deadline=deadline.isoformat())
            self.save()

    def queue_notification(self, key, kind, when, content, expires_at=None):
        if key not in self.data["notifications"]:
            self.data["notifications"][key] = dict(kind=kind, time=when.isoformat(),
                content=content, status="pending")
            if expires_at is not None:
                self.data["notifications"][key]["expires_at"] = expires_at.isoformat()
            self.save()

    def notification_status(self, key, status, message_id=None):
        record = self.data["notifications"][key]
        record["status"] = status
        if message_id is not None:
            record["message_id"] = message_id
        self.save()
