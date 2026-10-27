"""One-off fact check: for each airline's main-weekend daytime flights, open Google Flights' booking step
(the "Book with <airline>" screen) and record the airline-direct price and fare name.

About 10 SerpApi calls. Writes data/factcheck.json and prints a summary.
    python factcheck.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

import main
from fetch import SERPAPI_URL

ROOT = Path(__file__).parent


def call(key: str, params: dict) -> dict:
    r = requests.get(SERPAPI_URL, params=dict(params, api_key=key), timeout=60)
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data


def itineraries(data: dict) -> list[dict]:
    return (data.get("best_flights") or []) + (data.get("other_flights") or [])


def find(data: dict, flight_number: str) -> dict | None:
    return next((it for it in itineraries(data) if it["flights"][0]["flight_number"] == flight_number), None)


def booking(key: str, base: dict, token: str) -> list[dict]:
    data = call(key, dict(base, booking_token=token))
    out = []
    for opt in data.get("booking_options", []):
        for kind in ("together", "departing", "returning"):
            o = opt.get(kind)
            if o:
                out.append({"ticket": kind, "book_with": o.get("book_with"), "price": o.get("price"),
                            "fare": o.get("option_title"), "airline_direct": o.get("airline"),
                            "extensions": o.get("extensions")})
    return out


def main_() -> None:
    main.load_dotenv()
    key = os.environ["SERPAPI_KEY"].strip()
    cfg = main.load_config()
    trip = cfg["trips"][0]
    common = {"engine": "google_flights", "currency": cfg["currency"], "gl": cfg["gl"], "hl": cfg["hl"],
              "adults": 1, "travel_class": 1, "stops": 1}
    report = {}

    for p in [p for p in main.pairs(cfg) if p["trip"]["id"] == trip["id"] and p["option"] == "day"]:
        a = p["airline"]
        try:
            if p["source"] == "return":
                spec = main.return_spec(cfg, p)
                base = dict(common, departure_id=spec.origin, arrival_id=spec.destination, type=1,
                            outbound_date=spec.outbound_date, return_date=spec.return_date,
                            include_airlines=a, outbound_times=spec.outbound_times, return_times=spec.return_times)
                first = call(key, base)
                out = find(first, p["out_flight"])
                rets = call(key, dict(base, departure_token=out["departure_token"]))
                back = find(rets, p["back_flight"])
                report[a] = {"pair": f"{p['out_flight']} + {p['back_flight']}", "tracker_price": out.get("price"),
                             "return_step_price": back.get("price") if back else None,
                             "booking": booking(key, base, back["booking_token"]) if back else "return flight not listed"}
            else:
                legs = []
                for flight, (o, d, date) in ((p["out_flight"], (cfg["origin"], cfg["destination"], trip["out"])),
                                             (p["back_flight"], (cfg["destination"], cfg["origin"], trip["back"]))):
                    base = dict(common, departure_id=o, arrival_id=d, type=2, outbound_date=date)
                    it = find(call(key, base), flight)
                    legs.append({"flight": flight, "search_price": it.get("price") if it else None,
                                 "booking": booking(key, base, it["booking_token"]) if it else "not listed"})
                report[a] = {"pair": f"{p['out_flight']} + {p['back_flight']}", "legs": legs,
                             "tracker_price": sum(l["search_price"] or 0 for l in legs)}
        except Exception as e:   # one airline failing shouldn't lose the others
            report[a] = {"error": f"{type(e).__name__}: {e}"}

    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "factcheck.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main_()
