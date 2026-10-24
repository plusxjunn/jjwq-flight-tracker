"""Daily run: fetch prices → price each planned flight pair → store → alerts → digest → CSV + report."""
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
    if isinstance(cfg["schedule"]["end_date"], str):
        cfg["schedule"]["end_date"] = date.fromisoformat(cfg["schedule"]["end_date"])
    for t in cfg["trips"]:
        t["out"], t["back"] = str(t["out"]), str(t["back"])
    return cfg


# ---- what to search ---------------------------------------------------------

def pairs(cfg: dict) -> list[dict]:
    """Every planned flight pair: one per trip × airline × option (day / after_work)."""
    out = []
    for airline, opts in cfg["options"].items():
        for option, o in opts.items():
            for t in cfg["trips"]:
                if o.get("trips") and t["id"] not in o["trips"]:
                    continue
                out.append(dict(pair_id=f"{t['id']}:{airline}:{option}", trip=t, airline=airline, option=option,
                                out_flight=o["out"], back_flight=o["back"], source=cfg["pricing"][airline]))
    return out


def mmdd(iso: str) -> str:
    return iso[5:7] + iso[8:10]


def hour_window(hhmm: str) -> str:
    """SerpApi time window covering the hour a flight departs, e.g. '09:15' -> '9,9'."""
    h = int(hhmm[:2])
    return f"{h},{h}"


def one_way_specs(cfg: dict) -> list[SearchSpec]:
    outs = sorted({t["out"] for t in cfg["trips"]})
    backs = sorted({t["back"] for t in cfg["trips"]})
    o, d = cfg["origin"], cfg["destination"]
    return ([SearchSpec(id=f"ow_out_{mmdd(x)}", type="one_way", origin=o, destination=d, outbound_date=x,
                        direction="out") for x in outs] +
            [SearchSpec(id=f"ow_back_{mmdd(x)}", type="one_way", origin=d, destination=o, outbound_date=x,
                        direction="back") for x in backs])


def return_spec(cfg: dict, p: dict) -> SearchSpec:
    return SearchSpec(
        id="rt_" + p["pair_id"].replace(":", "_"), type="round_trip", origin=cfg["origin"],
        destination=cfg["destination"], outbound_date=p["trip"]["out"], return_date=p["trip"]["back"],
        include_airlines=p["airline"], outbound_times=hour_window(cfg["flights"][p["out_flight"]][0]),
        return_times=hour_window(cfg["flights"][p["back_flight"]][0]))


def due_return_specs(cfg: dict, store: Store, today: date, force: bool = False) -> list[SearchSpec]:
    """Return-ticket searches due today. Each repeats every N days on its own phase so they spread
    across the week; one that missed its day (e.g. quota skip) catches up as soon as possible."""
    every = cfg["return_search_every_days"]
    main_id = cfg["trips"][0]["id"]
    due, idx = [], {"main_day": 0, "other": 0}
    for p in pairs(cfg):
        if p["source"] != "return":
            continue
        kind = "main_day" if p["trip"]["id"] == main_id and p["option"] == "day" else "other"
        n, i = every[kind], idx[kind]
        idx[kind] += 1
        spec = return_spec(cfg, p)
        last = store.last_search_day(spec.id)
        if force or last is None or (today - last).days >= n or (
                last < today and (today.toordinal() + i) % n == 0):
            due.append(spec)
    return due


# ---- pricing ------------------------------------------------------------------

def find_flight(result, flight_number: str):
    if result is None:
        return None
    return next((f for f in result.flights if not f.stops and f.flight_numbers == flight_number), None)


def times(cfg: dict, flight_number: str, live) -> tuple[str, str]:
    if live is not None:
        return live.depart[-5:], live.arrive[-5:]
    return tuple(cfg["flights"][flight_number])


def price_pairs(cfg: dict, results: dict) -> tuple[list[dict], list[str]]:
    """Turn today's search results into same-airline pair prices. Returns (quotes, warnings)."""
    quotes, warnings = [], []
    for p in pairs(cfg):
        t = p["trip"]
        ow_out = results.get(f"ow_out_{mmdd(t['out'])}")
        ow_back = results.get(f"ow_back_{mmdd(t['back'])}")
        f_out, f_back = find_flight(ow_out, p["out_flight"]), find_flight(ow_back, p["back_flight"])
        url, price = None, None
        if p["source"] == "one_way":
            if ow_out is None or ow_back is None:
                continue                          # search failed; already reported
            if f_out is None or f_back is None:
                missing = p["out_flight"] if f_out is None else p["back_flight"]
                warnings.append(f"{p['pair_id']}: {missing} not on sale/listed for that date")
                continue
            price = f_out.price + f_back.price
            url = notify.flights_url(cfg, t, p["airline"])
        else:
            rt = results.get("rt_" + p["pair_id"].replace(":", "_"))
            if rt is None:
                continue                          # not due today, or failed
            hit = find_flight(rt, p["out_flight"])
            if hit is None:
                warnings.append(f"{p['pair_id']}: {p['out_flight']} + {p['back_flight']} return not found")
                continue
            price, url = hit.price, rt.url
        out_dep, out_arr = times(cfg, p["out_flight"], f_out)
        back_dep, back_arr = times(cfg, p["back_flight"], f_back)
        quotes.append(dict(pair_id=p["pair_id"], trip=t["id"], airline=p["airline"], option=p["option"],
                           price=price, out_flight=p["out_flight"], out_dep=out_dep, out_arr=out_arr,
                           back_flight=p["back_flight"], back_dep=back_dep, back_arr=back_arr,
                           source=p["source"], url=url))
    return quotes, warnings


def points(rows) -> list[rules.Point]:
    return [rules.Point(datetime.fromisoformat(r["ts"]), r["price"]) for r in rows]


def trip_by_id(cfg: dict, trip_id: str) -> dict:
    return next(t for t in cfg["trips"] if t["id"] == trip_id)


# ---- verdicts & digest ------------------------------------------------------------

def airline_verdict(cfg: dict, store: Store, airline: str, now: datetime) -> dict:
    """Verdict for one airline, from its main-weekend daytime pair."""
    rc = cfg["rules"]
    pid = f"{cfg['trips'][0]['id']}:{airline}:day"
    latest = store.latest_pair(pid)
    if latest is None:
        return {"verdict": "TRACKING", "why": "No prices yet"}
    ts = datetime.fromisoformat(latest["ts"])
    price = latest["price"]
    prior = points(store.pair_history(pid, before=ts))
    first = datetime.fromisoformat(store.pair_history(pid)[0]["ts"])

    week = store.latest_pair(pid, at_or_before=now - timedelta(days=6, hours=12))
    if week is None:
        change = f"Tracking since {first.day} {first:%b}"
    else:
        diff = price - week["price"]
        change = ("No change this week" if abs(diff) < 1 else
                  f"{'Down' if diff < 0 else 'Up'} {notify.money(abs(diff))} this week")

    if not rules.has_history(prior, now.date(), rc["history_min_days"]):
        return {"verdict": "TRACKING", "why": change}
    med = rules.median_30(prior, now.date(), rc)
    usual = ""
    if med:
        usual = ("at its usual" if abs(price - med) <= med * 0.02 else
                 "below its usual" if price < med else "above its usual") + f" ~{notify.money(med)}"
    reasons = rules.evaluate_deal(price, now, prior, rules.Insights(), rc)
    up, _ = rules.climbing(points(store.pair_history(pid)), now.date(), rc)
    v = rules.verdict(price, now, prior, rules.Insights(), reasons, up, rc)
    why = reasons[0].text[0].upper() + reasons[0].text[1:] if (v == "BOOK NOW" and reasons) else \
        ", ".join(x for x in (change, usual) if x)
    return {"verdict": v, "why": why}


def build_digest(cfg: dict, store: Store, now: datetime) -> list[dict]:
    blocks = []
    main = cfg["trips"][0]["id"]
    for airline in cfg["airlines"]:
        day = store.latest_pair(f"{main}:{airline}:day")
        b = {"airline": airline, **airline_verdict(cfg, store, airline, now), "flights": day, "trips": [],
             "after_work": None}
        for t in cfg["trips"]:
            q = store.latest_pair(f"{t['id']}:{airline}:day")
            b["trips"].append({"trip": t, "price": q["price"] if q else None,
                               "checked": datetime.fromisoformat(q["ts"]) if q else None})
        aw = store.latest_pair(f"{main}:{airline}:after_work")
        if aw is not None:
            b["after_work"] = aw
        blocks.append(b)
    return blocks


# ---- main run -------------------------------------------------------------------

def run(cfg: dict, store: Store, provider, notifier, now: datetime, dry_run: bool,
        force_digest: bool = False, report_path: Path | None = None, preview: bool = False) -> str:
    rc = cfg["rules"]
    run_id = store.start_run("morning", now)
    specs = one_way_specs(cfg) + due_return_specs(cfg, store, now.date(), force=preview)
    limit = cfg["quota"]["monthly_limit"]
    used_before = store.calls_this_month(now)

    if not dry_run and used_before + len(specs) > limit:
        msg = (f"Skipped today's run: {used_before} SerpApi calls used this month, this run needs "
               f"{len(specs)}, limit is {limit}.")
        store.finish_run(run_id, "quota_skip", 0, msg)
        notifier.admin("⚠️ " + msg)
        return "quota_skip"

    calls, errors, results = 0, [], {}
    for spec in specs:
        try:
            result = provider.search(spec)
        except ProviderError as e:
            result = None
            errors.append(f"{spec.id}: {e}")
        calls += 1
        store.log_call(run_id, spec.id, result is not None, now)
        if result is None:
            continue
        store.record_search(run_id, now, result)
        results[spec.id] = result

        seen = {f.airline for f in result.flights if not f.stops and f.airline in cfg.get("watch_airlines", {})}
        for code in seen:
            last = store.last_alert("watch", None, code)
            if not last or now - datetime.fromisoformat(last["ts"]) > timedelta(days=7):
                notifier.admin(f"ℹ️ {cfg['watch_airlines'][code]} ({code}) is showing nonstop flights on "
                               f"{spec.id} again. Add it to 'airlines' in config.yaml if you want it tracked.")
                store.save_alert(now, "watch", None, code, None, spec.id)

    quotes, warnings = price_pairs(cfg, results)
    for q in quotes:
        store.save_pair(now, q)

    # ---- deal alerts: per pair, grouped into one block per airline + option ----
    main = cfg["trips"][0]["id"]
    blocks: dict[tuple, dict] = {}
    for q in quotes:
        hist = points(store.pair_history(q["pair_id"], before=now))
        pair_rc = dict(rc, target_price=rc.get("target_price") if q["trip"] == main else None)
        reasons = rules.evaluate_deal(q["price"], now, hist, rules.Insights(), pair_rc)
        if preview and not reasons and q["trip"] == main and q["option"] == "day":
            reasons = [rules.Reason("preview", "example only: real alerts need a 30-day low or a 12%+ drop")]
        if not reasons:
            continue
        last = store.last_alert("deal", q["pair_id"], q["airline"])
        if not preview and not rules.cooldown_allows(
                now, q["price"], datetime.fromisoformat(last["ts"]) if last else None,
                last["price"] if last else None, rc):
            continue
        key = (q["airline"], q["option"])
        blocks.setdefault(key, {"airline": q["airline"], "option": q["option"], "flights": q, "items": []})
        blocks[key]["items"].append({"trip": trip_by_id(cfg, q["trip"]), "price": q["price"],
                                     "reasons": reasons, "url": q["url"]})
        if not preview:
            store.save_alert(now, "deal", q["pair_id"], q["airline"], q["price"],
                             "; ".join(r.kind for r in reasons))
    if blocks:
        notifier.guest(notify.deals_message(list(blocks.values()), cfg))

    # ---- prices rising: main-weekend daytime pair, per airline ----
    for q in quotes:
        if q["trip"] != main or q["option"] != "day":
            continue
        up, above = rules.climbing(points(store.pair_history(q["pair_id"])), now.date(), rc)
        last = store.last_alert("climbing", q["pair_id"], q["airline"])
        if up and (not last or now - datetime.fromisoformat(last["ts"]) >= timedelta(hours=rc["cooldown_hours"])):
            notifier.guest(notify.rising_message(cfg, q, trip_by_id(cfg, main), above))
            if not preview:
                store.save_alert(now, "climbing", q["pair_id"], q["airline"], q["price"], f"{above}% above usual")

    # ---- weekly digest (chart picture first) ----
    if force_digest or preview or now.weekday() == cfg["schedule"]["digest_weekday"]:
        try:
            png = Path(tempfile.mkdtemp(prefix="flight-chart-")) / "weekly-chart.png"
            if report.chart_png(store, cfg, png):
                notifier.guest_photo(png, notify.chart_caption(cfg))
        except Exception as e:
            notifier.admin(f"⚠️ Weekly chart picture failed, digest sent without it: {type(e).__name__}: {e}")
        notifier.guest(notify.digest_message(build_digest(cfg, store, now), cfg, now))

    # ---- bookkeeping ----
    used_after = used_before + calls
    warn = cfg["quota"]["warn_at"]
    if used_before < warn <= used_after:
        notifier.admin(f"⚠️ {used_after}/{limit} SerpApi calls used this month.")
    status = "ok" if not errors else ("api_error" if not results else "partial")
    if errors:
        notifier.admin(f"❌ {len(errors)} of {len(specs)} searches failed:\n" + "\n".join(errors))
    if warnings:
        notifier.admin("⚠️ Some planned flights weren't found (schedule change or not on sale yet):\n"
                       + "\n".join(warnings))
    store.finish_run(run_id, status, calls, "; ".join(errors + warnings))
    if report_path:
        report.build(store, cfg, report_path, now)
    return status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="use saved samples, no API calls, print messages")
    ap.add_argument("--preview", action="store_true",
                    help="real searches (all of them), but every guest message goes to the admin chat")
    ap.add_argument("--digest", action="store_true", help="also send the weekly digest now")
    ap.add_argument("--welcome", action="store_true", help="post + pin the welcome message, then exit")
    ap.add_argument("--announce", metavar="FILE", help="post this file's text (Telegram HTML) to guests, then exit")
    ap.add_argument("--test-admin", action="store_true", help="send a test message to the admin chat, then exit")
    ap.add_argument("--skip-if-done", action="store_true",
                    help="exit without API calls if today's run already happened (scheduled runs)")
    ap.add_argument("--slot", default="morning", help=argparse.SUPPRESS)   # kept so old commands still work
    ap.add_argument("--now", help="override current time (ISO, SGT) for testing")
    args = ap.parse_args(argv)

    cfg = load_config()
    now = datetime.fromisoformat(args.now).replace(tzinfo=SGT) if args.now else now_sgt()

    if args.dry_run:
        notifier = notify.PrintNotifier()
    else:
        notifier = notify.TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHANNEL_ID"],
                                           os.environ["TELEGRAM_ADMIN_CHAT_ID"])
        if args.preview:
            notifier = notify.PreviewNotifier(notifier)

    if args.test_admin:
        notifier.admin(f"✅ Test message. Bot can reach the admin chat. ({now:%d %b %Y %H:%M} SGT)")
        return 0
    if args.welcome:
        notifier.guest_pinned(notify.welcome_message(cfg))
        return 0
    if args.announce:
        notifier.guest(Path(args.announce).read_text().strip())
        return 0

    if now.date() > cfg["schedule"]["end_date"]:
        print(f"Past end date {cfg['schedule']['end_date']}; nothing to do.")
        return 0

    if args.dry_run:
        tmp = Path(tempfile.mkdtemp(prefix="flight-dry-"))
        db = tmp / "tracker.db"
        if DB_PATH.exists():
            shutil.copy(DB_PATH, db)
        store, provider, report_path = Store(db), SampleProvider(ROOT / "samples"), tmp / "report.html"
    else:
        store = Store(DB_PATH)
        provider = SerpApiProvider(os.environ["SERPAPI_KEY"], cfg["currency"], cfg["gl"], cfg["hl"],
                                   save_dir=ROOT / "samples" if args.preview else None)
        report_path = ROOT / "docs" / "report.html"

    if args.skip_if_done and store.slot_done("morning", now.date()):
        print("Today's run already happened; nothing to do.")
        store.close()
        return 0

    try:
        status = run(cfg, store, provider, notifier, now, args.dry_run, args.digest, report_path, args.preview)
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
