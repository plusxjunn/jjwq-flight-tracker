"""Telegram delivery (guest channel + admin chat) and plain-English message templates."""
from __future__ import annotations

from datetime import datetime
import re
from html import escape as h, unescape

import requests

from rules import VERDICT_TEXT

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ---- delivery -------------------------------------------------------------

class Notifier:
    def guest(self, text: str) -> None: ...
    def admin(self, text: str) -> None: ...
    def guest_pinned(self, text: str) -> None: ...


class TelegramNotifier(Notifier):
    def __init__(self, token: str, channel_id: str, admin_chat_id: str):
        self.token = token.strip()
        self.channel_id = channel_id.strip()
        self.admin_chat_id = admin_chat_id.strip()

    def _call(self, method: str, **payload) -> dict:
        try:
            r = requests.post(TELEGRAM_API.format(token=self.token, method=method), json=payload, timeout=30)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise RuntimeError(f"Telegram {method} failed: {str(e).replace(self.token, '***')}") from None
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]

    def _send(self, chat_id: str, text: str) -> dict:
        # Messages are Telegram HTML (see `link`); dynamic text is escaped with h().
        return self._call("sendMessage", chat_id=chat_id, text=text[:4096], parse_mode="HTML",
                          link_preview_options={"is_disabled": True})

    def guest(self, text):
        self._send(self.channel_id, text)

    def admin(self, text):
        self._send(self.admin_chat_id, "🔧 Flight tracker\n" + h(text))

    def guest_pinned(self, text):
        msg = self._send(self.channel_id, text)
        self._call("pinChatMessage", chat_id=self.channel_id, message_id=msg["message_id"],
                   disable_notification=True)


class PrintNotifier(Notifier):
    """--dry-run: print instead of sending (HTML shown as plain text)."""

    def _print(self, who: str, text: str):
        text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', r"\2 ↗ \1", text)
        text = unescape(re.sub(r"</?b>", "", text))
        print(f"\n----- {who} -----\n{text}\n")

    def guest(self, text):
        self._print("GUEST CHANNEL", text)

    def admin(self, text):
        self._print("ADMIN CHAT", "🔧 Flight tracker\n" + h(text))

    def guest_pinned(self, text):
        self._print("GUEST CHANNEL (pinned)", text)


# ---- formatting helpers ---------------------------------------------------

def link(url: str | None, label: str = "Google Flights") -> str:
    return f'<a href="{h(url)}">{h(label)}</a>' if url else ""


def money(x: float | None) -> str:
    return "—" if x is None else f"S${x:,.0f}"


def day_mon(d: datetime | str) -> str:
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    return f"{d.day} {d:%b}"


def hhmm(ts: str) -> str:
    return ts[-5:] if ts else ""


def bag_note(cfg: dict, codes: set[str] | None = None) -> str | None:
    budget = [c for c in cfg.get("budget_airlines", []) if codes is None or c in codes]
    if not budget:
        return None
    names = h(" & ".join(cfg["short_names"].get(c, c) for c in budget))
    return (f"🧳 {names}'s cheapest fare has no checked bag or seat choice. "
            f"Budget ~S${cfg.get('bag_fee_sgd', 40):.0f} each way for a 20kg bag (check at booking).")


def route_title(spec) -> str:
    return "SIN⇄Bali" if spec.type == "round_trip" else ("SIN→Bali" if spec.direction == "out" else "Bali→SIN")


# ---- guest messages -------------------------------------------------------

def deal_block(spec, airline_short: str, price: float, lowest: tuple[float, str], reasons, url) -> str:
    low_price, low_day = lowest
    lines = [
        f"✈️ Good time to book: {h(airline_short)} {route_title(spec)}",
        f"{h(spec.label)}: {money(price)} per person",
        f"Lowest we've seen: {money(low_price)} (on {day_mon(low_day)})",
        "Why: " + h("; ".join(r.text for r in reasons)),
    ]
    if url:
        lines.append(f"Book: {link(url)}")
    return "\n".join(lines)


def deal_line(spec, airline_short: str, price: float, reasons, url) -> str:
    return (f"• {h(airline_short)} · {h(spec.label)}: <b>{money(price)}</b>\n"
            f"   {h(reasons[0].text)} · {link(url, 'Book')}")


def deals_message(alerts: list[dict], cfg: dict) -> str:
    """alerts: dicts with spec, airline, short, price, lowest, reasons, url (in config order)."""
    if len(alerts) <= 2:
        body = "\n\n".join(deal_block(a["spec"], a["short"], a["price"], a["lowest"], a["reasons"], a["url"])
                           for a in alerts)
    else:
        body = (f"✈️ Good time to book: {len(alerts)} low fares today\n"
                "Per person, nonstop, cheapest fare type.\n\n"
                + "\n".join(deal_line(a["spec"], a["short"], a["price"], a["reasons"], a["url"]) for a in alerts))
    parts = [body]
    note = bag_note(cfg, {a["airline"] for a in alerts})
    if note:
        parts.append(note)
    parts.append("👉 Good time to book.")
    return "\n\n".join(parts)


def climbing_message(spec, price: float, above_pct: int, days: int, url: str | None) -> str:
    lines = [
        f"📈 Prices climbing: {route_title(spec)} {h(spec.label)}",
        f"Cheapest now: {money(price)} per person, {above_pct}% above usual for {days} days in a row.",
    ]
    if url:
        lines.append(f"Book: {link(url)}")
    lines.append("👉 If you haven't booked yet, book soon.")
    return "\n".join(lines)


def welcome_message(cfg: dict) -> str:
    names = h(", ".join(cfg["airlines"].values()))
    budget = h(" & ".join(cfg["short_names"][c] for c in cfg.get("budget_airlines", [])))
    return (
        "💍 Welcome! This channel tracks flight prices Singapore ⇄ Bali for our wedding on Sat 4 Sep 2027.\n"
        "📅 Main trip Fri 3 → Sun 5 Sep (also Thu 2 → Mon 6, and one-way Wed 1 / Fri 3 out, Sun 5 / Mon 6 back). "
        "Prices are per person, economy, nonstop, cheapest fare type.\n"
        f"✈️ Airlines: {names}.\n"
        "🚦 BOOK NOW = genuinely low · GOOD PRICE = fair, fine to book · WAIT = higher than usual · "
        "RISING = going up, book soon.\n"
        f"🧳 {budget}'s cheapest fare excludes checked bags and seat choice (~S${cfg.get('bag_fee_sgd', 40):.0f} "
        "each way for a bag). We post only when it's worth your attention, plus a summary every Sunday.\n"
        f"📊 {link(cfg.get('report_url'), 'Price chart')}"
    )


def digest_message(d: dict, cfg: dict) -> str:
    """d is built by main.build_digest()."""
    L = ["✈️ <b>Weekly fare check: SIN⇄Bali wedding trip</b>",
         "Per person, nonstop, cheapest fare type."]
    for trip in d["round_trips"]:
        L.append("")
        L.append(f"<b>{h(trip['label'])}</b> (return ticket)")
        if trip["prices"]:
            L.append(" · ".join(f"{h(a)} {money(p)}" for a, p in trip["prices"]))
        else:
            L.append("No nonstop fares found this week.")
    if d.get("mix"):
        L.append("")
        L.append("<b>Mix &amp; match</b> (two one-way tickets)")
        for m in d["mix"]:
            L.append(f"{h(m['label'])}: {h(m['out_airline'])} + {h(m['back_airline'])} = {money(m['total'])}")
    if d.get("after_work"):
        L.append("")
        L.append("<b>Afternoon/evening flights</b>")
        for a in d["after_work"]:
            L.append(f"{h(a['label'])}: {h(a['airline'])} {h(a['flight'])} {a['depart']}→{a['arrive']} {money(a['price'])}")
    L.append("")
    line = f"Cheapest Fri→Sun: {money(d['core_price'])}"
    if d.get("core_airline"):
        line += f" ({h(d['core_airline'])})"
    if d.get("last_week") is not None:
        diff = d["core_price"] - d["last_week"]
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "=")
        line += f", last week {money(d['last_week'])} ({arrow}{abs(diff):.0f})"
    L.append(line)
    if d.get("lowest_seen"):
        L.append(f"Lowest seen so far: {money(d['lowest_seen'][0])} on {day_mon(d['lowest_seen'][1])}")
    L.append("")
    L.append(f"Verdict: <b>{d['verdict']}</b>. {VERDICT_TEXT[d['verdict']]}")
    note = bag_note(cfg)
    if note:
        L.append(note)
    if cfg.get("report_url"):
        L.append(f"📊 {link(cfg['report_url'], 'Price chart')}")
    L.append("👉 " + {"BOOK NOW": "Good time to book.", "GOOD PRICE": "Good time to book.",
                      "RISING": "Book soon if you haven't.", "WAIT": "No action needed."}[d["verdict"]])
    return "\n".join(L)
