"""Discord presentation and durable paper-result/session notices. No broker orders."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from live_clients import DeliveryError

LOG = logging.getLogger("nytrading")
PRAGUE = ZoneInfo("Europe/Prague")
NEW_YORK = ZoneInfo("America/New_York")


def message(when, planned, costs, balance, deadline, window):
    """Match Gold Lab's presentation while describing this strategy accurately."""
    side = "🟢 BUY" if planned["side"] == 1 else "🔴 SELL"
    distance = abs(planned["entry"] - planned["sl"])
    price_risk = distance * planned["units"]
    start, _, _ = window.times(when)
    return (f"**{side} · XAUUSD**\n"
        f"New York · {when.astimezone(PRAGUE):%H:%M} Prague\n\n"
        f"**Entry**  {planned['entry']:.3f}\n"
        f"**Stop loss**  {planned['sl']:.3f}\n"
        f"**Take profit · 100% · {costs.target:g}R**  {planned['tp']:.3f}\n"
        "**1H filter**  LOW DIRECTIONALITY · band reversion\n\n"
        f"**Risk budget**  ${balance * costs.risk:,.2f} · {costs.risk * 100:g}% of ${balance:,.2f}\n"
        f"**Volume (total)**  {planned['lots']:.2f} lots\n"
        f"**SL distance**  {distance:.3f} USD/oz\n"
        f"**Price risk at SL**  ${price_risk:,.2f}\n"
        f"**Planned risk incl. costs**  ${planned['risk_usd']:,.2f}\n"
        f"_Reference sizing: {costs.contract:g} oz/lot. Check MT5 specifications; "
        "recalculate if your entry or SL differs. Equity is the virtual paper account._\n\n"
        f"Close at session end {deadline.astimezone(PRAGUE):%H:%M} · Prague time\n"
        f"Session {start.astimezone(PRAGUE):%H:%M}–{deadline.astimezone(PRAGUE):%H:%M} · Prague time\n"
        f"Date {when.astimezone(PRAGUE):%Y-%m-%d} · Europe/Prague\n"
        "_Paper signal · reference prices_")


def result_message(trade):
    reason = trade["reason"]
    if reason.startswith("TP"):
        title = "✅ TAKE PROFIT HIT"
    elif reason.startswith("SL"):
        title = "🔴 STOP LOSS HIT"
    elif reason == "DEADLINE":
        title = "⏱ SESSION CLOSED"
    else:
        title = "⚠️ PAPER RISK LIMIT · POSITION CLOSED"
    side = "BUY" if trade["side"] == 1 else "SELL"
    when = datetime.fromisoformat(trade["exit_time"])
    detail = ""
    if reason == "SL_BOTH":
        detail = "\n_Both TP and SL touched in M5; conservative SL-first result._"
    elif reason.endswith("GAP"):
        detail = "\n_Gap: reference execution follows the conservative model._"
    return (f"**{title} · XAUUSD**\n"
        f"New York · {side} · {when.astimezone(PRAGUE):%H:%M} Prague\n\n"
        f"**Entry**  {trade['entry']:.3f}\n"
        f"**Reference exit**  {trade['exit']:.3f}\n"
        f"**Volume (total)**  {trade['lots']:.2f} lots\n"
        f"**Total paper result:** {trade['r']:+.2f}R · ${trade['pnl']:+,.2f}{detail}\n\n"
        "_Paper result · costs included · Prague time · not a broker fill_")


def session_message(deadline):
    return (f"**🏁 Ending the session · New York · {deadline.astimezone(PRAGUE):%H:%M} Prague**\n"
        "No more entries. Close any remaining position at session end.\n"
        f"Date {deadline.astimezone(PRAGUE):%Y-%m-%d} · Europe/Prague\n"
        "_Paper alerts · broker positions require manual closure_")


def on_clock(now, window, state):
    """Session-end reminders do not depend on a successful market-data request."""
    local = now.astimezone(NEW_YORK)
    if local.weekday() >= 5:
        return
    start, cutoff, deadline = window.times(now)
    key = "NY:" + local.date().isoformat()
    if start <= now < deadline:
        state.track_session(key, start, cutoff, deadline)
    # Upgrade a pre-notification state with an open position after a restart.
    position = state.data["position"]
    if position and datetime.fromisoformat(position["time"]).astimezone(NEW_YORK).date() == local.date():
        held_deadline = datetime.fromisoformat(position["deadline"])
        state.track_session(key, start, min(cutoff, held_deadline), held_deadline)
    session = state.data["sessions"].get(key)
    if not session:
        return  # No historical session spam when starting outside trading hours.
    deadline = datetime.fromisoformat(session["deadline"])
    cutoff = datetime.fromisoformat(session["cutoff"])
    if cutoff < deadline and now >= cutoff:
        state.queue_notification(key + ":cutoff", "session", cutoff,
            f"**🔒 No more entries · New York · {cutoff.astimezone(PRAGUE):%H:%M} Prague**\n"
            f"Open trades remain active until TP/SL or session end at {deadline.astimezone(PRAGUE):%H:%M} Prague.")
    if now >= deadline:
        state.queue_notification(key + ":end", "session", deadline, session_message(deadline))


def flush(state, now, discord=None, dry_run=False):
    """Save before POST; ambiguous deliveries are never automatically repeated."""
    for trade in state.data["trades"]:
        if "r" not in trade:  # Legacy summaries lack the details needed for a result alert.
            continue
        state.queue_notification(trade["signal_id"] + ":result", "result",
            datetime.fromisoformat(trade["exit_time"]), result_message(trade))
    for key, record in state.data["notifications"].items():
        if record["status"] != "pending":
            continue
        scheduled = datetime.fromisoformat(record["time"])
        if record["kind"] == "session" and scheduled.astimezone(NEW_YORK).date() != now.astimezone(NEW_YORK).date():
            state.notification_status(key, "expired")
            continue
        content = record["content"]
        if (now - scheduled).total_seconds() > 90:
            content = "**Delayed paper notice**\n" + content
        if dry_run:
            state.notification_status(key, "dry_run")
            LOG.info("DRY_RUN_NOTICE %s", content)
            continue
        state.notification_status(key, "delivery_unconfirmed")
        try:
            receipt = discord.send(content)
        except DeliveryError as exc:
            LOG.error("NOTICE_DELIVERY_UNCONFIRMED %s %s; inspect Discord before manual retry", key, exc)
            continue
        state.notification_status(key, "sent", receipt)
        LOG.info("NOTICE_SENT %s", key)
