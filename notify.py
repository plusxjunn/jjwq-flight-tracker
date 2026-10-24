"""Telegram delivery (guest channel + admin chat) and the guest message templates.

Guest messages are Telegram HTML: <b>, <i> and <a> only. Anything dynamic goes through h().
"""
from __future__ import annotations

import re
from datetime import date, datetime
from html import escape as h, unescape
from urllib.parse import quote

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ---- delivery -------------------------------------------------------------

class Notifier:
    def guest(self, text: str) -> None: ...
    def admin(self, text: str) -> None: ...
    def guest_pinned(self, text: str) -> None: ...
    def guest_photo(self, path, caption: str) -> None: ...


class TelegramNotifier(Notifier):
    def __init__(self, token: str, channel_id: str, admin_chat_id: str):
        self.token = token.strip()
        self.channel_id = channel_id.strip()
        self.admin_chat_id = admin_chat_id.strip()

    def _call(self, method: str, files=None, **payload) -> dict:
        url = TELEGRAM_API.format(token=self.token, method=method)
        try:
            r = (requests.post(url, data=payload, files=files, timeout=60) if files
                 else requests.post(url, json=payload, timeout=30))
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise RuntimeError(f"Telegram {method} failed: {str(e).replace(self.token, '***')}") from None
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]

    def send(self, chat_id: str, html: str) -> dict:
        return self._call("sendMessage", chat_id=chat_id, text=html[:4096], parse_mode="HTML",
                          link_preview_options={"is_disabled": True})

    def send_photo(self, chat_id: str, path, caption: str) -> None:
        with open(path, "rb") as f:
            self._call("sendPhoto", files={"photo": f}, chat_id=chat_id, caption=caption[:1024], parse_mode="HTML")

    def guest(self, text):
        self.send(self.channel_id, text)

    def admin(self, text):
        self.send(self.admin_chat_id, "🔧 Flight tracker\n" + h(text))

    def guest_photo(self, path, caption):
        self.send_photo(self.channel_id, path, caption)

    def guest_pinned(self, text):
        msg = self.send(self.channel_id, text)
        self._call("unpinAllChatMessages", chat_id=self.channel_id)   # replace, don't stack, the pinned post
        self._call("pinChatMessage", chat_id=self.channel_id, message_id=msg["message_id"],
                   disable_notification=True)


class PreviewNotifier(Notifier):
    """--preview: everything meant for guests goes to the admin chat instead."""

    HEADER = "👀 <b>PREVIEW</b> <i>(not sent to guests)</i>\n\n"

    def __init__(self, tg: TelegramNotifier):
        self.tg = tg

    def guest(self, text):
        self.tg.send(self.tg.admin_chat_id, self.HEADER + text)

    def admin(self, text):
        self.tg.admin(text)

    def guest_photo(self, path, caption):
        self.tg.send_photo(self.tg.admin_chat_id, path, self.HEADER + caption)

    def guest_pinned(self, text):
        self.guest(text)


class PrintNotifier(Notifier):
    """--dry-run: print instead of sending (HTML shown as plain text)."""

    def _print(self, who: str, text: str):
        text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', r"\2 ↗", text)
        text = unescape(re.sub(r"</?[bi]>", "", text))
        print(f"\n----- {who} -----\n{text}\n")

    def guest(self, text):
        self._print("GUEST CHANNEL", text)

    def admin(self, text):
        self._print("ADMIN CHAT", "🔧 Flight tracker\n" + h(text))

    def guest_pinned(self, text):
        self._print("GUEST CHANNEL (pinned)", text)

    def guest_photo(self, path, caption):
        self._print("GUEST CHANNEL (photo)", f"[picture: {path}]\n{caption}")


# ---- formatting helpers ---------------------------------------------------

def link(url: str | None, label: str = "Google Flights") -> str:
    return f'<a href="{h(url)}">{h(label)}</a>' if url else h(label)


def money(x: float | None) -> str:
    return "S$ –" if x is None else f"S${x:,.0f}"


def t12(hhmm: str) -> str:
    """'13:05' -> '1:05pm', '00:05' -> '12:05am', '12:00' -> '12:00pm'."""
    hr, mi = int(hhmm[:2]), hhmm[3:5]
    return f"{(hr % 12) or 12}:{mi}{'am' if hr < 12 else 'pm'}"


def dlabel(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a} {d.day}"


def trip_dates(t: dict, month: bool = False) -> str:
    s = f"{dlabel(t['out'])} → {dlabel(t['back'])}"
    return s + " Sep" if month else s


def trip_title(t: dict) -> str:
    """'Main weekend · Fri 3 → Sun 5 Sep' or 'Fri 3 → Mon 6 Sep'."""
    return (f"{h(t['label'])} · " if t.get("label") else "") + trip_dates(t, month=True)


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def flights_url(cfg: dict, trip: dict, airline: str) -> str:
    """Google Flights search for the exact dates on one airline (for pairs priced from one-way fares)."""
    q = (f"Flights from {cfg['origin']} to {cfg['destination']} on {trip['out']} through {trip['back']} "
         f"nonstop on {cfg['airlines'][airline]}")
    return f"https://www.google.com/travel/flights?q={quote(q)}&curr={cfg['currency']}&hl={cfg['hl']}&gl={cfg['gl']}"


def bag_line(cfg: dict, airline: str, italic: bool = False) -> str:
    if airline in cfg.get("budget_airlines", []):
        text = f"Cabin bag only. About S${2 * cfg['bag_fee_sgd']:.0f} more for a 20kg bag both ways."
        if italic:
            text = f"Cabin bag only; ~S${2 * cfg['bag_fee_sgd']:.0f} more for a 20kg bag"
    else:
        text = "Checked bag included"
    return f"🧳 <i>{text}</i>" if italic else f"🧳 {text}"


def flight_lines(q, with_dates: dict | None = None) -> list[str]:
    """SIN → DPS / DPS → SIN lines for a pair (q has out_dep, out_arr, back_dep, back_arr)."""
    if with_dates:
        return [f"SIN → DPS  <b>{dlabel(with_dates['out'])}</b> · {t12(q['out_dep'])} → {t12(q['out_arr'])}",
                f"DPS → SIN  <b>{dlabel(with_dates['back'])}</b> · {t12(q['back_dep'])} → {t12(q['back_arr'])}"]
    return [f"SIN → DPS  {t12(q['out_dep'])} → {t12(q['out_arr'])}",
            f"DPS → SIN  {t12(q['back_dep'])} → {t12(q['back_arr'])}"]


def flight_note(cfg: dict, q, option: str) -> str:
    bits = [f"{q['out_flight']} / {q['back_flight']}"]
    if option == "after_work":
        bits.append("after-work flight")
    if q["airline"] not in cfg.get("budget_airlines", []):
        bits.append("checked bag included")
    return "<i>" + h(" · ".join(bits)) + "</i>"


# ---- guest messages -------------------------------------------------------

def deal_block(cfg: dict, b: dict) -> str:
    """One airline + flight option. b: airline, option, flights (a pair quote), items [{trip, price, reasons, url}]."""
    q, airline = b["flights"], b["airline"]
    L = [f"<b>✈️ Good time to book: {h(cfg['airlines'][airline])}</b>"]
    if len(b["items"]) == 1:
        it = b["items"][0]
        L += [trip_title(it["trip"]), "",
              f"<b>{money(it['price'])}</b> per person, return",
              f"📉 {h(cap(it['reasons'][0].text))}", ""]
        L += flight_lines(q, it["trip"])
        L.append(flight_note(cfg, q, b["option"]))
    else:
        L += flight_lines(q)
        L += [flight_note(cfg, q, b["option"]), ""]
        for it in b["items"]:
            L.append(f"<b>{trip_dates(it['trip'])}</b>  <b>{money(it['price'])}</b> · {link(it['url'], 'Book')}")
            L.append(f"📉 {h(cap(it['reasons'][0].text))}")
    if airline in cfg.get("budget_airlines", []):
        L += ["", bag_line(cfg, airline)]
    if airline == "GA":
        L += ["", f"⚠️ {h(cfg['garuda_warning'])}"]
    if len(b["items"]) == 1:
        L += ["", f"👉 <b>Book now:</b> {link(b['items'][0]['url'])}"]
    else:
        L += ["", "👉 <b>Good time to book.</b> Tap <b>Book</b> next to your dates."]
    return "\n".join(L)


def deals_message(blocks: list[dict], cfg: dict) -> str:
    return "\n\n".join(deal_block(cfg, b) for b in blocks)


def rising_message(cfg: dict, q: dict, trip: dict, above_pct: int) -> str:
    L = [f"<b>📈 Prices rising: {h(cfg['airlines'][q['airline']])}</b>", trip_title(trip), "",
         f"<b>{money(q['price'])}</b> per person, return",
         f"{above_pct}% above usual for the last {cfg['rules']['climbing_days']} checks", ""]
    L += flight_lines(q, trip)
    L.append(flight_note(cfg, q, q["option"]))
    L += ["", f"👉 <b>Book soon if your dates are fixed:</b> {link(q['url'])}"]
    return "\n".join(L)


VERDICT_LABEL = {
    "BOOK NOW": "🔥 <b>Book now</b>",
    "GOOD PRICE": "✅ <b>Good price</b>",
    "WAIT": "⏳ <b>Wait</b>",
    "RISING": "📈 <b>Rising</b>",
    "TRACKING": "👀 <b>Still tracking</b>",
}


def closing_line(blocks: list[dict], cfg: dict) -> str:
    name = lambda b: cfg["airlines"][b["airline"]]
    good = [name(b) for b in blocks if b["verdict"] in ("BOOK NOW", "GOOD PRICE")]
    rising = [name(b) for b in blocks if b["verdict"] == "RISING"]
    wait = [name(b) for b in blocks if b["verdict"] == "WAIT"]
    def join(xs, word="or"):
        return f" {word} ".join(xs) if len(xs) <= 2 else ", ".join(xs[:-1]) + f" {word} " + xs[-1]
    if rising:
        head = f"👉 <b>Book soon if your dates are fixed:</b> {h(join(rising))} prices are rising."
    elif good:
        head = f"👉 <b>Good time to book {h(join(good))}.</b>"
    elif wait:
        return "👉 <b>No action needed this week.</b> Prices are above usual; we'll post when they drop."
    else:
        return "👉 <b>No action needed yet.</b> We're still learning what's usual; we'll post when prices drop."
    if wait:
        one = len(wait) == 1
        head += (f" {h(join(wait, 'and'))} {'is' if one else 'are'} above usual; "
                 f"we'll post when {'it drops' if one else 'they drop'}.")
    return head


def digest_message(blocks: list[dict], cfg: dict, now: datetime) -> str:
    """blocks come from main.build_digest()."""
    L = ["<b>✈️ Weekly fares for our wedding</b>", "<i>Direct flights · per person · return</i>"]
    for b in blocks:
        a = b["airline"]
        L += ["", f"<b>{h(cfg['airlines'][a])}</b> · {VERDICT_LABEL[b['verdict']]}", f"<i>{h(b['why'])}</i>"]
        if b["flights"] is not None:
            L += flight_lines(b["flights"])
        for t in b["trips"]:
            when = ""
            if t["checked"] is not None and t["checked"].date() != now.date():
                when = f" <i>(checked {t['checked']:%a})</i>"
            price = f"<b>{money(t['price'])}</b>" if t["price"] is not None else "<i>no price yet</i>"
            L.append(f"{trip_dates(t['trip'])}   {price}{when}")
        aw = b["after_work"]
        if aw is not None:
            L.append(f"After work: SIN → DPS {t12(aw['out_dep'])}, {trip_dates(cfg['trips'][0])}  "
                     f"<b>{money(aw['price'])}</b>")
        L.append(bag_line(cfg, a, italic=True))
        if a == "GA":
            L.append(f"⚠️ <i>{h(cfg['garuda_warning'])}</i>")
    L += ["", closing_line(blocks, cfg)]
    if cfg.get("report_url"):
        L.append(f"📊 {link(cfg['report_url'], 'Price chart')}")
    return "\n".join(L)


def chart_caption(cfg: dict) -> str:
    t = cfg["trips"][0]
    text = (f"📊 {h(t.get('label', ''))} ({trip_dates(t)}): return price per airline, daytime flights, "
            "same airline both ways. ▲ = alert sent.")
    if cfg.get("report_url"):
        text += f" {link(cfg['report_url'], 'Full report')}"
    return text


def welcome_message(cfg: dict) -> str:
    names = h(", ".join(cfg["airlines"].values()))
    budget = " & ".join(cfg["short_names"][c] for c in cfg.get("budget_airlines", []))
    trips = cfg["trips"]
    others = [trip_dates(t) for t in trips[1:]]
    other_lines = [" · ".join(others[i:i + 2]) for i in range(0, len(others), 2)]
    return "\n".join([
        "💍 <b>Welcome to JJ &amp; WQ's Bali Tracker!</b>",
        "We're watching flight prices for our wedding at <b>Glamp Nusa, Nusa Dua</b> on <b>Sat 4 Sep 2027</b>, "
        "and we'll post here when it's a good time to book.",
        "",
        f"✈️ <b>{names}</b>",
        "Direct flights, same airline both ways. Prices are per person, return.",
        "SIN = Singapore Changi · DPS = Bali (Denpasar)",
        "",
        "🏡 Check-in 3pm Fri 3 · check-out 12pm Sun 5",
        "The flights we track land in Bali from midday and mostly leave Bali in the evening "
        "(Garuda's only flight back is at 2pm). Glamp Nusa is about 25 min from the airport.",
        "",
        "📅 <b>Dates we track</b>",
        f"{trip_dates(trips[0])} ({h(trips[0].get('label', '').lower())})",
        *other_lines,
        "",
        "🚦 <b>What our labels mean</b>",
        "🔥 Book now: lowest in 30 days, or a sudden drop",
        "✅ Good price: at or below usual",
        "⏳ Wait: above usual, we'll post when it drops",
        "📈 Rising: going up, book soon if your dates are fixed",
        "👀 Still tracking: under 2 weeks of prices so far",
        "",
        f"🧳 {h(budget)}'s fare is cabin bag only (about S${2 * cfg['bag_fee_sgd']:.0f} more for a 20kg bag "
        "both ways). SQ and Garuda include a checked bag.",
        "",
        f"📊 {link(cfg.get('report_url'), 'Price chart')} · Summary every Sunday",
    ])
