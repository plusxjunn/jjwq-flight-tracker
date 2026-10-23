"""Daily run: fetch prices → store → evaluate rules → notify → export CSV + report."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

import notify
import report
import rules
from fetch import ProviderError, SampleProvider, SearchSpec, SerpApiProvider
from store import SGT, Store, now_sgt

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "tracker.db"


def load_config(path: str | Path = ROOT / "config.yaml") -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for k in ("twice_daily_from", "end_date"):
        if isinstance(cfg["schedule"][k], str):
            cfg["schedule"][k] = date.fromisoformat(cfg["schedule"][k])
    return cfg


def specs_for(cfg: dict, slot: str | None = None) -> list[SearchSpec]:
    out = []
    for s in cfg["searches"]:
        if slot and slot not in s.get("slots", ["morning"]):
            continue
        back = s.get("direction") == "back"
        out.append(SearchSpec(
            id=s["id"], type=s["type"], label=s["label"], group=s.get("group"),
            direction=s.get("direction", "out"),
            origin=cfg["destination"] if back else cfg["origin"],
            destination=cfg["origin"] if back else cfg["destination"],
            outbound_date=str(s["outbound_date"]),
            return_date=str(s["return_date"]) if s.get("return_date") else None,
        ))
    return out


def in_window(spec: SearchSpec, flight, cfg: dict) -> bool:
    """Afternoon/evening preference: land in Bali in window, or leave Bali in window."""
    tw = cfg["time_windows"]
    if spec.direction == "back":
        lo, hi, t = *tw["back_depart"], flight.depart[-5:]
    else:
        lo, hi, t = *tw["out_arrive"], flight.arrive[-5:]
        if flight.arrive[:10] > flight.depart[:10]:   # lands after midnight: count as evening
            t = "23:59"
    return lo <= t <= hi


def build_quotes(result, cfg: dict) -> list[dict]:
    """Cheapest nonstop per tracked airline, any time (preferred=0) and in the time window (preferred=1)."""
    quotes = []
    for preferred in (0, 1):
        best: dict[str, object] = {}
        for f in result.flights:
            if f.stops or f.airline not in cfg["airlines"]:
                continue
            if preferred and not in_window(result.spec, f, cfg):
                continue
            if f.airline not in best or f.price < best[f.airline].price:
                best[f.airline] = f
        overall = min(best.values(), key=lambda f: f.price, default=None)
        for code, f in best.items():
            quotes.append(dict(airline=code, price=f.price, flights=f.flight_numbers, depart=f.depart,
                               arrive=f.arrive, preferred=preferred, is_overall_min=f is overall))
    return quotes


def points(rows) -> list[rules.Point]:
    return [rules.Point(datetime.fromisoformat(r["ts"]), r["price"]) for r in rows]


def short(cfg, code):
    return cfg["short_names"].get(code, code)


def dshort(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a} {d.day}"


def resolve_slot(arg: str, now: datetime) -> str:
    if arg != "auto":
        return arg
    return "morning" if now.hour < 15 else "evening"


# ---- digest -----------------------------------------------------------------

def build_digest(store: Store, cfg: dict, specs: list[SearchSpec], now: datetime) -> dict:
    rc = cfg["rules"]
    d: dict = {"round_trips": [], "mix": [], "after_work": []}
    for s in specs:
        if s.type == "round_trip":
            q = store.latest_quotes(s.id)
            d["round_trips"].append({"label": s.label, "prices": [(short(cfg, r["airline"]), r["price"]) for r in q]})
    outs = [s for s in specs if s.type == "one_way" and s.direction == "out"]
    backs = [s for s in specs if s.type == "one_way" and s.direction == "back"]
    for o in outs:
        for b in backs:
            qo, qb = store.latest_quotes(o.id), store.latest_quotes(b.id)
            if qo and qb:
                d["mix"].append({"label": f"{dshort(o.outbound_date)} → {dshort(b.outbound_date)}",
                                 "out_airline": short(cfg, qo[0]["airline"]),
                                 "back_airline": short(cfg, qb[0]["airline"]),
                                 "total": qo[0]["price"] + qb[0]["price"]})
    for s in outs + backs:
        q = store.latest_quotes(s.id, preferred=1)
        if q:
            r = q[0]
            d["after_work"].append({"label": f"{dshort(s.outbound_date)} {'out' if s.direction == 'out' else 'back'}",
                                    "airline": short(cfg, r["airline"]), "flight": r["flights"],
                                    "depart": notify.hhmm(r["depart"]), "arrive": notify.hhmm(r["arrive"]),
                                    "price": r["price"]})

    core = store.latest_quotes("core")
    hist = store.quote_history("core", None)
    d["core_price"] = core[0]["price"] if core else None
    d["core_airline"] = short(cfg, core[0]["airline"]) if core else None
    week = store.latest_quotes("core", at_or_before=now - timedelta(days=7))
    d["last_week"] = week[0]["price"] if week else None
    if hist:
        low = min(hist, key=lambda r: r["price"])
        d["lowest_seen"] = (low["price"], low["ts"])
    d["verdict"] = core_verdict(store, cfg, now)
    return d


def core_verdict(store: Store, cfg: dict, now: datetime) -> str:
    core = store.latest_quotes("core")
    if not core:
        return "WAIT"
    price = core[0]["price"]
    s = store.latest_search("core")
    ins = rules.Insights(s["price_level"], s["typical_low"], s["typical_high"])
    prior = points(store.quote_history("core", None, before=datetime.fromisoformat(core[0]["ts"])))
    reasons = [r for r in rules.evaluate_deal(price, now, prior, ins, cfg["rules"]) if r.kind != "sharp_drop"]
    is_climbing, _ = rules.climbing(points(store.quote_history("core", None)), now.date(), cfg["rules"])
    return rules.verdict(price, now, prior, ins, reasons, is_climbing, cfg["rules"])


# ---- main run ---------------------------------------------------------------

def run(cfg: dict, store: Store, provider, notifier, now: datetime, slot: str,
        dry_run: bool, force_digest: bool = False, report_path: Path | None = None) -> str:
    rc = cfg["rules"]
    specs = specs_for(cfg, slot)
    run_id = store.start_run(slot, now)
    limit = cfg["quota"]["monthly_limit"]
    used_before = store.calls_this_month(now)

    if not dry_run and used_before + len(specs) > limit:
        msg = (f"Skipped the {slot} run: {used_before} SerpApi calls used this month, this run needs "
               f"{len(specs)}, limit is {limit}.")
        store.finish_run(run_id, "quota_skip", 0, msg)
        notifier.admin("⚠️ " + msg)
        return "quota_skip"

    calls, errors, results = 0, [], []
    for spec in specs:
        try:
            result = provider.search(spec)
            ok = True
        except ProviderError as e:
            result, ok = None, False
            errors.append(f"{spec.id}: {e}")
        calls += 1
        store.log_call(run_id, spec.id, ok, now)
        if result is None:
            continue
        store.save_search(run_id, now, result, build_quotes(result, cfg))
        results.append(result)

        seen = {f.airline for f in result.flights if not f.stops and f.airline in cfg.get("watch_airlines", {})}
        for code in seen:
            last = store.last_alert("watch", None, code)
            if not last or now - datetime.fromisoformat(last["ts"]) > timedelta(days=7):
                notifier.admin(f"ℹ️ {cfg['watch_airlines'][code]} ({code}) is showing nonstop flights on "
                               f"{spec.id} again. Add it to 'airlines' in config.yaml if you want it tracked.")
                store.save_alert(now, "watch", None, code, None, spec.id)

    # ---- deal alerts (per search, per airline) ----
    deals = []
    for result in results:
        spec = result.spec
        ins = rules.Insights(result.price_level, result.typical_low, result.typical_high)
        for q in [q for q in build_quotes(result, cfg) if q["preferred"] == 0]:
            hist = points(store.quote_history(spec.id, q["airline"], before=now))
            reasons = rules.evaluate_deal(q["price"], now, hist, ins, rc)
            if not reasons:
                continue
            last = store.last_alert("deal", spec.id, q["airline"])
            if not rules.cooldown_allows(now, q["price"], datetime.fromisoformat(last["ts"]) if last else None,
                                         last["price"] if last else None, rc):
                continue
            low = min(store.quote_history(spec.id, q["airline"]), key=lambda r: r["price"])
            deals.append(dict(spec=spec, airline=q["airline"], short=short(cfg, q["airline"]), price=q["price"],
                              lowest=(low["price"], low["ts"]), reasons=reasons, url=result.url))
            store.save_alert(now, "deal", spec.id, q["airline"], q["price"], "; ".join(r.kind for r in reasons))
    if deals:
        notifier.guest(notify.deals_message(deals, cfg))

    # ---- prices climbing (core) ----
    core = next((r for r in results if r.spec.id == "core"), None)
    if core:
        is_up, above = rules.climbing(points(store.quote_history("core", None)), now.date(), rc)
        last = store.last_alert("climbing", "core", None)
        if is_up and (not last or now - datetime.fromisoformat(last["ts"]) >= timedelta(hours=rc["cooldown_hours"])):
            price = store.latest_quotes("core")[0]["price"]
            notifier.guest(notify.climbing_message(core.spec, price, above, rc["climbing_days"], core.url))
            store.save_alert(now, "climbing", "core", None, price, f"{above}% above median")

    # ---- weekly digest ----
    if force_digest or (slot == "morning" and now.weekday() == cfg["schedule"]["digest_weekday"]):
        # Chart picture first; a failure here must never block the digest itself.
        try:
            png = Path(tempfile.mkdtemp(prefix="flight-chart-")) / "weekly-chart.png"
            if report.chart_png(store, cfg, png):
                notifier.guest_photo(png, notify.chart_caption(cfg))
        except Exception as e:
            notifier.admin(f"⚠️ Weekly chart picture failed, digest sent without it: {type(e).__name__}: {e}")
        notifier.guest(notify.digest_message(build_digest(store, cfg, specs_for(cfg), now), cfg))

    # ---- bookkeeping ----
    used_after = used_before + calls
    warn = cfg["quota"]["warn_at"]
    if used_before < warn <= used_after:
        notifier.admin(f"⚠️ {used_after}/{limit} SerpApi calls used this month.")
    status = "ok" if not errors else ("api_error" if not results else "partial")
    if errors:
        notifier.admin(f"❌ {len(errors)} of {len(specs)} searches failed ({slot} run):\n" + "\n".join(errors))
    store.finish_run(run_id, status, calls, "; ".join(errors))
    if report_path:
        report.build(store, cfg, report_path, now)
    return status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="use saved sample, no API calls, print messages")
    ap.add_argument("--slot", choices=["auto", "morning", "evening"], default="auto")
    ap.add_argument("--digest", action="store_true", help="also send the weekly digest now")
    ap.add_argument("--welcome", action="store_true", help="post + pin the welcome message, then exit")
    ap.add_argument("--test-admin", action="store_true", help="send a test message to the admin chat, then exit")
    ap.add_argument("--skip-if-done", action="store_true",
                    help="exit without API calls if this slot already ran today (for backup schedules)")
    ap.add_argument("--now", help="override current time (ISO, SGT) for testing")
    args = ap.parse_args(argv)

    cfg = load_config()
    now = datetime.fromisoformat(args.now).replace(tzinfo=SGT) if args.now else now_sgt()

    if args.dry_run:
        notifier = notify.PrintNotifier()
    else:
        notifier = notify.TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL_ID"],
                                           os.environ["TELEGRAM_ADMIN_CHAT_ID"])

    if args.test_admin:
        notifier.admin(f"✅ Test message. Bot can reach the admin chat. ({now:%d %b %Y %H:%M} SGT)")
        return 0
    if args.welcome:
        notifier.guest_pinned(notify.welcome_message(cfg))
        return 0

    if now.date() > cfg["schedule"]["end_date"]:
        print(f"Past end date {cfg['schedule']['end_date']}; nothing to do.")
        return 0
    slot = resolve_slot(args.slot, now)
    if slot == "evening" and now.date() < cfg["schedule"]["twice_daily_from"]:
        print(f"Evening runs start {cfg['schedule']['twice_daily_from']}; nothing to do.")
        return 0

    if args.dry_run:
        tmp = Path(tempfile.mkdtemp(prefix="flight-dry-"))
        db = tmp / "tracker.db"
        if DB_PATH.exists():
            shutil.copy(DB_PATH, db)
        store, provider, report_path = Store(db), SampleProvider(ROOT / "samples"), tmp / "report.html"
    else:
        store = Store(DB_PATH)
        provider = SerpApiProvider(os.environ["SERPAPI_KEY"], cfg["currency"], cfg["gl"], cfg["hl"])
        report_path = ROOT / "docs" / "report.html"

    if args.skip_if_done and store.slot_done(slot, now.date()):
        print(f"The {slot} run already happened today; nothing to do.")
        store.close()
        return 0

    try:
        status = run(cfg, store, provider, notifier, now, slot, args.dry_run, args.digest, report_path)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        store.db.execute("UPDATE runs SET status='error', finished_at=?, detail=? WHERE status='running'",
                         (now_sgt().isoformat(), tb[-2000:]))
        store.db.commit()
        try:
            notifier.admin(f"💥 Run crashed: {type(e).__name__}: {e}")
        finally:
            store.export_csv(DB_PATH.parent if not args.dry_run else store.path.parent)
            store.close()
        return 1

    store.export_csv(DB_PATH.parent if not args.dry_run else store.path.parent)
    store.close()
    print(f"Run finished: {status}" + (f" (dry run, files in {store.path.parent})" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
