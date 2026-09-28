"""Recommendations generated from the demand forecast, holiday effects and the live grid.

Runs hourly from the collection scheduler (and on demand from the Recommendations page). Each action has a stable
source_key, so a rerun refreshes the numbers of an action that is still open instead of adding a duplicate; actions a
person already accepted, rejected or completed are left alone. Open actions whose condition has gone away expire.

    peak_management      high / critical day: MW to cut or shift in the high window, with ₹ saved at peak power prices
    power_purchase       high day: firm supply to arrange (with forecast uncertainty); low day / holiday: purchases to trim
    discom_coordination  high day: that reduction split across DISCOMs by their live share of Delhi load
    maintenance          the lowest-demand day of the coming week, for planned shutdowns
    schedule_revision    a DISCOM overdrawing its schedule right now, with the hourly deviation cost
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Recommendation, SldcRealtimeReading, User
from . import app_settings, demand_model

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
SYSTEM_USER = "usr_super_admin"  # generated actions are attributed to the platform admin account
DISCOM_LABEL = {"NDPL": "TPDDL", "BRPL": "BRPL", "BYPL": "BYPL", "NDMC": "NDMC", "MES": "MES", "RAILWAY": "Railways"}


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def _ist_to_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=IST).astimezone(timezone.utc)


def _hours(windows: list[dict]) -> float:
    return sum((w["to"] - w["from"]).total_seconds() / 3600 for w in windows)


def _windows_text(windows: list[dict]) -> str:
    return ", ".join(f"{w['from']:%H:%M}–{w['to']:%H:%M}" for w in windows)


def _inr(value: float) -> str:
    if value >= 1e7:
        return f"₹{value / 1e7:.2f} crore"
    if value >= 1e5:
        return f"₹{value / 1e5:.1f} lakh"
    return f"₹{value:,.0f}"


def _peak_advice(windows: list[dict], peak_at: datetime) -> str:
    start = windows[0]["from"]
    if start.hour >= 19 or start.hour < 6:
        # Evening / night peaks in Delhi are driven by household air-conditioning.
        return ("Ask consumers to hold AC setpoints at 24–26°C, stagger society water pumping and move EV charging past 01:00, "
                f"and call residential demand-response for the {peak_at:%H:00} peak.")
    return (f"Pre-cool offices and malls before {start:%H:%M}, move industrial batches and water pumping outside the window, "
            f"and call commercial demand-response contracts for the {peak_at:%H:00} peak.")


def build(db: Session, now: datetime | None = None) -> list[dict]:
    """The recommendations that should exist right now (not yet saved)."""
    now = now or now_ist()
    settings = app_settings.values(db)
    price = float(settings["recommendations.power_price_inr_mwh"])
    dr_share = float(settings["recommendations.demand_response_pct"]) / 100
    actions: list[dict] = []

    try:
        outlook = demand_model.forecast(db)
        holidays = {row["name"]: row for row in demand_model.insights()["holidays"]}
    except demand_model.ModelNotReady:
        outlook, holidays = None, {}

    captured = db.scalar(select(func.max(SldcRealtimeReading.captured_at)))
    discoms = []
    if captured is not None:
        discoms = [row for row in db.scalars(select(SldcRealtimeReading).where(SldcRealtimeReading.captured_at == captured, SldcRealtimeReading.category == "discom")) if row.actual_mw]
    discom_total = sum(row.actual_mw for row in discoms) or 0

    if outlook:
        thresholds = outlook["thresholds"]
        upcoming = [day for day in outlook["days"] if day["hours_covered"] >= 20 or day["peak_at"] > now]
        near_term = (now + timedelta(days=int(settings["alerts.forecast_days"]))).date()
        for day in upcoming:
            date = datetime.fromisoformat(day["date"]).date()
            label = f"{day['weekday']} {date:%d %b}"
            end_of_day = _ist_to_utc(datetime.combine(date, datetime.min.time()) + timedelta(days=1))

            if day["risk"] in ("high", "critical") and day["high_windows"] and day["peak_at"] > now:
                excess = max(0.0, day["peak_mw"] - thresholds["high_mw"])
                reduction = round(min(excess, dr_share * day["peak_mw"]) if excess else dr_share * day["peak_mw"] * 0.5, 0)
                hours = _hours(day["high_windows"])
                savings = round(reduction * hours * price, 0)
                windows = _windows_text(day["high_windows"])
                priority = "critical" if day["risk"] == "critical" else "high"
                actions.append({
                    "source_key": f"peak_shave:{date}", "category": "peak_management", "priority": priority,
                    "action": f"Cut or shift about {reduction:,.0f} MW during {windows} on {label}. " + _peak_advice(day["high_windows"], day["peak_at"]),
                    "expected_reduction_mw": reduction, "expected_savings": savings, "time_window": f"{label}, {windows}",
                    "confidence": day["probability_high"],
                    "reason": f"{day['reason']} Avoided peak purchase: {reduction:,.0f} MW for {hours:.0f} h at ₹{price:,.0f}/MWh ≈ {_inr(savings)}.",
                    "valid_until": end_of_day,
                })
                if date > near_term:
                    continue  # supply booking and DISCOM targets only for the next few days; further out they'd be noise
                peak_hours = [hour for hour in outlook["hours"] if hour["at"].date() == date]
                upper = max((hour["high_band_mw"] for hour in peak_hours), default=day["peak_mw"])
                actions.append({
                    "source_key": f"supply:{date}", "category": "power_purchase", "priority": priority,
                    "action": (f"Line up firm supply for a {day['peak_mw']:,.0f} MW peak at {day['peak_at']:%H:00} on {label}, with cover up to "
                               f"{upper:,.0f} MW for forecast uncertainty. Book day-ahead exchange blocks for {windows} before gate closure instead of "
                               f"relying on real-time deviation."),
                    "expected_reduction_mw": None, "expected_savings": None, "time_window": f"{label}, {windows}",
                    "confidence": day["probability_high"],
                    "reason": f"Forecast peak {day['peak_mw']:,.0f} MW is above the high mark of {thresholds['high_mw']:,.0f} MW; the 80% range reaches {upper:,.0f} MW.",
                    "valid_until": end_of_day,
                })
                if discom_total and reduction:
                    split = sorted(((row.actual_mw / discom_total, row) for row in discoms if row.entity in ("BRPL", "BYPL", "NDPL", "NDMC")), key=lambda item: -item[0])
                    parts = [f"{DISCOM_LABEL[row.entity]} ~{reduction * share:,.0f} MW" for share, row in split]
                    actions.append({
                        "source_key": f"discom:{date}", "category": "discom_coordination", "priority": "medium",
                        "action": f"Share the {reduction:,.0f} MW peak-reduction target with DISCOMs for {windows} on {label}: " + ", ".join(parts) + ". Ask each to confirm its demand-response and load-shifting plan by the previous evening.",
                        "expected_reduction_mw": reduction, "expected_savings": None, "time_window": f"{label}, {windows}",
                        "confidence": day["probability_high"],
                        "reason": f"Split by each DISCOM's live share of Delhi load at {captured:%H:%M} ({', '.join(f'{DISCOM_LABEL[row.entity]} {share:.0%}' for share, row in split)}).",
                        "valid_until": end_of_day,
                    })

            if day["low_level"] == "low" and day["low_windows"] and day["min_at"] > now:
                windows = _windows_text(day["low_windows"])
                gap = max(0.0, thresholds["low_mw"] - day["min_mw"])
                actions.append({
                    "source_key": f"surplus:{date}", "category": "power_purchase", "priority": "medium",
                    "action": f"Trim day-ahead purchases or surrender surplus for {windows} on {label}: demand is expected to fall to {day['min_mw']:,.0f} MW at {day['min_at']:%H:00}.",
                    "expected_reduction_mw": round(gap, 0) or None, "expected_savings": round(gap * _hours(day["low_windows"]) * price, 0) or None,
                    "time_window": f"{label}, {windows}", "confidence": day["probability_low"], "reason": day["min_reason"], "valid_until": end_of_day,
                })

            if day["holiday"] and date > now.date():
                name = demand_model.holiday_key(day["holiday"])
                effect = holidays.get(name)
                if effect and effect["effect_mw"] <= -100:
                    actions.append({
                        "source_key": f"holiday:{date}", "category": "power_purchase", "priority": "medium",
                        "action": f"{name} on {label}: plan purchases for lower demand. In past years this day ran about {abs(effect['effect_mw']):,.0f} MW ({abs(effect['effect_pct']):.1f}%) below a normal day, and {abs(effect['evening_effect_mw'] or effect['effect_mw']):,.0f} MW lower in the evening.",
                        "expected_reduction_mw": None, "expected_savings": round(abs(effect["effect_mw"]) * 24 * price * 0.25, 0),
                        "time_window": f"{label}, all day", "confidence": 0.8,
                        "reason": f"Measured by the demand model over {effect['occurrences']} past {name} days. Savings assume a quarter of the drop is bought at peak prices.",
                        "valid_until": end_of_day,
                    })

        calm = [day for day in upcoming if day["risk"] in ("normal", "watch") and datetime.fromisoformat(day["date"]).date() > now.date() and day["hours_covered"] >= 20]
        if calm:
            best = min(calm, key=lambda day: day["peak_mw"])
            date = datetime.fromisoformat(best["date"]).date()
            actions.append({
                "source_key": f"maintenance:{date}", "category": "maintenance", "priority": "low",
                "action": f"Schedule planned shutdowns and line maintenance on {best['weekday']} {date:%d %b}, the lightest day this week: forecast peak only {best['peak_mw']:,.0f} MW, dropping to {best['min_mw']:,.0f} MW around {best['min_at']:%H:00}.",
                "expected_reduction_mw": None, "expected_savings": None, "time_window": f"{best['weekday']} {date:%d %b}, best around {best['min_at']:%H:00}",
                "confidence": round(1 - best["probability_high"], 3),
                "reason": f"Lowest forecast peak of the next {len(upcoming)} days and only {best['probability_high']:.0%} chance of high demand. {best['reason']}",
                "valid_until": _ist_to_utc(datetime.combine(date, datetime.min.time()) + timedelta(days=1)),
            })

    limit = float(settings["alerts.discom_overdraw_mw"])
    for row in discoms:
        if (row.deviation_mw or 0) > limit and captured is not None and now - captured < timedelta(minutes=30):
            name = DISCOM_LABEL.get(row.entity, row.entity)
            cost = round(row.deviation_mw * price, 0)
            actions.append({
                "source_key": f"overdraw:{row.entity}:{captured:%Y-%m-%d}", "category": "schedule_revision", "priority": "high",
                "action": f"{name} is drawing {row.deviation_mw:,.0f} MW over its schedule ({row.schedule_mw:,.0f} → {row.actual_mw:,.0f} MW). Ask {name} to request a schedule revision or curtail flexible load now.",
                "expected_reduction_mw": round(row.deviation_mw, 0), "expected_savings": cost, "time_window": f"Now (as of {captured:%H:%M})",
                "confidence": 0.9, "reason": f"Live SLDC drawl at {captured:%H:%M}. Each hour at this deviation costs about {_inr(cost)} at ₹{price:,.0f}/MWh.",
                "valid_until": _ist_to_utc(captured + timedelta(hours=1)),
            })
    return actions


def generate(db: Session, now: datetime | None = None) -> dict:
    if not app_settings.values(db)["recommendations.enabled"]:
        return {"created": 0, "refreshed": 0, "expired": 0, "enabled": False}
    creator = SYSTEM_USER if db.get(User, SYSTEM_USER) else db.scalar(select(User.id).limit(1))
    wanted = {action["source_key"]: action for action in build(db, now)}
    utc_now = datetime.now(timezone.utc)
    created = refreshed = expired = 0
    existing = {item.source_key: item for item in db.scalars(select(Recommendation).where(Recommendation.source_key.is_not(None)))}

    for key, action in wanted.items():
        item = existing.get(key)
        if item is None:
            db.add(Recommendation(created_by=creator, status="open", **action))
            created += 1
        elif item.status == "open":
            for field, value in action.items():
                setattr(item, field, value)
            item.updated_at = utc_now
            refreshed += 1

    # An open generated action whose condition is gone (day passed, risk dropped, overdrawal ended) no longer applies.
    for key, item in existing.items():
        if item.status == "open" and key not in wanted:
            item.status = "expired"
            item.updated_at = utc_now
            expired += 1
    db.commit()
    return {"created": created, "refreshed": refreshed, "expired": expired, "enabled": True}
