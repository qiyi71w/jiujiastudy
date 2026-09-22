"""Persistent local-day scheduling for an account's daily report."""
import datetime as dt
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc


def settings(config):
    service = config.get("service") or {}
    clock = service.get("daily_time", "09:00")
    zone = service.get("daily_timezone", "UTC")
    if not isinstance(clock, str) or len(clock) != 5 or clock[2] != ":":
        raise ValueError("时间需为 HH:MM")
    try:
        hour, minute = int(clock[:2]), int(clock[3:])
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
        tz = ZoneInfo(zone)
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError):
        raise ValueError("需要有效的 HH:MM 和 IANA 时区") from None
    return clock, zone, tz


def planned(day, clock, tz, delay):
    """Resolve a local wall time: first fold, or first valid minute after a gap."""
    hour, minute = map(int, clock.split(":"))
    wall = dt.datetime.combine(day, dt.time(hour, minute))
    for offset in range(181):
        candidate = wall + dt.timedelta(minutes=offset)
        local = candidate.replace(tzinfo=tz, fold=0)
        utc = local.astimezone(UTC)
        if utc.astimezone(tz).replace(tzinfo=None) == candidate:
            return (utc + dt.timedelta(seconds=delay)).isoformat()
    raise ValueError("无法计算当天计划时间")


def ensure_plan(saved, config, now):
    """Only create today's plan; yesterday is kept solely if its due time is today."""
    clock, zone, tz = settings(config)
    today = now.astimezone(tz).date()
    plans = saved.setdefault("daily_plans", {})
    key = today.isoformat()
    # A timezone edit cannot turn an already formed report into another scan today.
    already_scanned = any(
        p.get("state") == "formed" and p.get("formed_at") and
        dt.datetime.fromisoformat(p["formed_at"]).astimezone(tz).date() == today
        for p in plans.values())
    if key not in plans and not already_scanned:
        delay = secrets.randbelow(901)
        plans[key] = {"due": planned(today, clock, tz, delay), "delay": delay,
                      "timezone": zone, "time": clock, "state": "pending", "delivery": {}}
    plan = plans.get(key)
    if plan and plan["state"] == "pending" and (plan["timezone"], plan["time"]) != (zone, clock):
        plan.update(due=planned(today, clock, tz, plan["delay"]), timezone=zone, time=clock)
    for day, pending in plans.items():
        if day > key and pending["state"] == "pending" and (pending["timezone"], pending["time"]) != (zone, clock):
            pending.update(due=planned(dt.date.fromisoformat(day), clock, tz, pending["delay"]),
                           timezone=zone, time=clock)
        if day < key and dt.datetime.fromisoformat(pending["due"]).astimezone(tz).date() < today:
            if pending["state"] in ("pending", "collecting"):
                pending["state"] = "cancelled"
            if pending["state"] == "formed":
                receipt = pending["delivery"].get("telegram", {})
                if receipt.get("state") == "pending":
                    receipt["state"] = "expired"
                pending.pop("text", None)
        elif day < (today - dt.timedelta(days=2)).isoformat() and pending["state"] == "formed":
            pending.pop("text", None)
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    due_yesterday = plans.get(yesterday, {}).get("due")
    eligible = [key] if key in plans else []
    if due_yesterday and dt.datetime.fromisoformat(due_yesterday).astimezone(tz).date() == today:
        eligible.insert(0, yesterday)
    return saved, eligible
