"""Alert and verdict rules. Pure functions: no I/O, easy to unit-test."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median


@dataclass
class Point:
    ts: datetime
    price: float


@dataclass
class Insights:
    price_level: str | None = None
    typical_low: float | None = None
    typical_high: float | None = None


@dataclass
class Reason:
    kind: str     # google_low | history_low | sharp_drop | target
    text: str


DEFAULTS = {
    "target_price": None,
    "cooldown_hours": 72,
    "cooldown_extra_drop": 0.05,
    "history_min_days": 14,
    "history_window_days": 30,
    "history_below_median": 0.10,
    "sharp_drop": 0.12,
    "climbing_above_median": 0.15,
    "climbing_days": 3,
    "hard_deadline": date(2027, 5, 31),
}

VERDICT_TEXT = {
    "BOOK NOW": "Prices are genuinely low. Good time to book.",
    "GOOD PRICE": "Fair price. Fine to book if your plans are set.",
    "WAIT": "Higher than usual. No rush; we'll post when it drops.",
    "RISING": "Prices are climbing. Book soon.",
}


def _cfg(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULTS})
    if isinstance(out["hard_deadline"], str):
        out["hard_deadline"] = date.fromisoformat(out["hard_deadline"])
    return out


def pct(a: float, b: float) -> int:
    """How far a is below b, in whole percent."""
    return round((b - a) / b * 100) if b else 0


def daily_min(points: list[Point]) -> dict[date, float]:
    out: dict[date, float] = {}
    for p in points:
        d = p.ts.date()
        out[d] = min(p.price, out.get(d, p.price))
    return out


def window_prices(points: list[Point], end: date, days: int) -> list[float]:
    """Daily minimums for the `days` days before `end` (end excluded)."""
    start = end - timedelta(days=days)
    return [v for d, v in daily_min(points).items() if start <= d < end]


def has_history(points: list[Point], today: date, min_days: int) -> bool:
    return bool(points) and min(p.ts.date() for p in points) <= today - timedelta(days=min_days)


def median_30(points: list[Point], today: date, cfg: dict | None = None) -> float | None:
    """30-day median of daily minimums before today; None until enough history."""
    c = _cfg(cfg)
    if not has_history(points, today, c["history_min_days"]):
        return None
    w = window_prices(points, today, c["history_window_days"])
    return median(w) if w else None


def evaluate_deal(price: float, now: datetime, history: list[Point], insights: Insights,
                  cfg: dict | None = None) -> list[Reason]:
    """Which alert rules fire for this price. `history` = earlier quotes only (not this one)."""
    c = _cfg(cfg)
    today = now.date()
    reasons: list[Reason] = []

    # 1. Google says low
    if insights.price_level == "low" and insights.typical_low and price < insights.typical_low:
        reasons.append(Reason("google_low",
                              f"Google rates it low, {pct(price, insights.typical_low)}% below the usual "
                              f"S${insights.typical_low:.0f}–{insights.typical_high:.0f}"))

    # 2. Own-history low
    if has_history(history, today, c["history_min_days"]):
        w = window_prices(history, today, c["history_window_days"])
        if w:
            med = median(w)
            if price <= min(w) and price <= med * (1 - c["history_below_median"]):
                reasons.append(Reason("history_low", f"lowest in 30 days, {pct(price, med)}% below usual"))

    # 3. Sharp drop vs previous run
    if history:
        prev, prev_day = history[-1].price, history[-1].ts.date()
        if price <= prev * (1 - c["sharp_drop"]):
            gap = (today - prev_day).days
            when = "yesterday" if gap == 1 else (f"{prev_day:%a}" if gap < 7 else f"{prev_day.day} {prev_day:%b}")
            reasons.append(Reason("sharp_drop", f"down {pct(price, prev)}% since {when} (was S${prev:.0f})"))

    # 4. Target hit
    target = c["target_price"]
    if target is not None and price <= target:
        reasons.append(Reason("target", f"at or below the S${target:.0f} target"))

    return reasons


def cooldown_allows(now: datetime, price: float, last_alert_ts: datetime | None,
                    last_alert_price: float | None, cfg: dict | None = None) -> bool:
    """72h cooldown per trip+airline, unless the price has dropped a further 5%."""
    c = _cfg(cfg)
    if last_alert_ts is None:
        return True
    if now - last_alert_ts >= timedelta(hours=c["cooldown_hours"]):
        return True
    return last_alert_price is not None and price <= last_alert_price * (1 - c["cooldown_extra_drop"])


def climbing(points: list[Point], today: date, cfg: dict | None = None) -> tuple[bool, int]:
    """True when the price has been ≥15% above its 30-day median for the last 3 checks,
    the latest of which is today. Checks need not be on consecutive days (SQ/Garuda are checked
    every few days). `points` includes today's quote. Returns (is_climbing, today's % above median)."""
    c = _cfg(cfg)
    daily = daily_min(points)
    checks = sorted(daily)[-c["climbing_days"]:]
    if len(checks) < c["climbing_days"] or checks[-1] != today:
        return False, 0
    above_pct = 0
    for d in checks:
        prior = [p for p in points if p.ts.date() < d]
        med = median_30(prior, d, c)
        if med is None or daily[d] < med * (1 + c["climbing_above_median"]):
            return False, 0
        above_pct = round((daily[d] - med) / med * 100)
    return True, above_pct


def verdict(price: float, now: datetime, history: list[Point], insights: Insights,
            reasons: list[Reason], is_climbing: bool, cfg: dict | None = None) -> str:
    """One-word verdict for the main (core) trip: BOOK NOW / GOOD PRICE / WAIT / RISING."""
    c = _cfg(cfg)
    today = now.date()
    med = median_30(history, today, c)

    if today > c["hard_deadline"]:
        # Peak weekend: from here prices rarely fall. Book unless it's currently above normal.
        if med is None or price <= med:
            return "BOOK NOW"
        return "RISING" if is_climbing else "WAIT"

    if is_climbing:
        return "RISING"
    if reasons:
        return "BOOK NOW"
    if (med is not None and price <= med) or insights.price_level == "low":
        return "GOOD PRICE"
    return "WAIT"
