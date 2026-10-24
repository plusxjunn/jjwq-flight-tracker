"""Flight data providers.

Everything above this module talks to `FlightProvider` only, so SerpApi can be
swapped for another source by writing one class.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import requests

SERPAPI_URL = "https://serpapi.com/search.json"


@dataclass
class SearchSpec:
    id: str
    type: str                 # round_trip | one_way
    origin: str
    destination: str
    outbound_date: str
    return_date: str | None = None
    label: str = ""
    direction: str = "out"    # for one-way: out (SIN→DPS) | back (DPS→SIN)
    group: str | None = None
    include_airlines: str | None = None   # e.g. "SQ": every flight in the itinerary on that airline
    outbound_times: str | None = None     # SerpApi hour window, e.g. "9,9" = departs 9:00-9:59
    return_times: str | None = None


@dataclass
class Flight:
    airline: str              # IATA code of operating carrier, e.g. "TR"
    airline_name: str
    flight_numbers: str       # e.g. "TR 282"
    depart: str               # "2027-09-03 15:55" local
    arrive: str
    price: float              # per person; round-trip searches = combined price
    stops: int = 0


@dataclass
class SearchResult:
    spec: SearchSpec
    flights: list[Flight]
    lowest_price: float | None = None
    price_level: str | None = None
    typical_low: float | None = None
    typical_high: float | None = None
    url: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


class ProviderError(Exception):
    pass


class FlightProvider:
    """Interface: one call to `search` = one paid API call."""

    name = "base"

    def search(self, spec: SearchSpec) -> SearchResult:
        raise NotImplementedError


def _code(flight_number: str) -> str:
    return (flight_number or "").split(" ")[0].strip().upper()


def parse_google_flights(spec: SearchSpec, data: dict) -> SearchResult:
    """Parse a SerpApi google_flights response into nonstop flights."""
    if data.get("error"):
        raise ProviderError(data["error"])
    flights = []
    for group in ("best_flights", "other_flights"):
        for it in data.get(group, []) or []:
            legs = it.get("flights") or []
            price = it.get("price")
            if not legs or price is None:
                continue
            flights.append(Flight(
                airline=_code(legs[0].get("flight_number", "")),
                airline_name=legs[0].get("airline", ""),
                flight_numbers=" + ".join(l.get("flight_number", "") for l in legs),
                depart=legs[0]["departure_airport"]["time"],
                arrive=legs[-1]["arrival_airport"]["time"],
                price=float(price),
                stops=len(legs) - 1,
            ))
    pi = data.get("price_insights") or {}
    rng = pi.get("typical_price_range") or [None, None]
    return SearchResult(
        spec=spec,
        flights=flights,
        lowest_price=pi.get("lowest_price"),
        price_level=pi.get("price_level"),
        typical_low=rng[0],
        typical_high=rng[1],
        url=(data.get("search_metadata") or {}).get("google_flights_url"),
        raw=data,
    )


class SerpApiProvider(FlightProvider):
    name = "serpapi"

    def __init__(self, api_key: str, currency="SGD", gl="sg", hl="en", timeout=60, save_dir=None):
        self.api_key = api_key.strip()
        self.save_dir = Path(save_dir) if save_dir else None   # keep raw responses (e.g. as dry-run samples)
        self.base = {"engine": "google_flights", "currency": currency, "gl": gl,
                     "hl": hl, "adults": 1, "travel_class": 1, "stops": 1}  # stops=1 → nonstop only
        self.timeout = timeout

    def params(self, spec: SearchSpec) -> dict:
        p = dict(self.base, departure_id=spec.origin, arrival_id=spec.destination,
                 outbound_date=spec.outbound_date)
        if spec.type == "round_trip":
            p.update(type=1, return_date=spec.return_date)
        else:
            p.update(type=2)
        for k in ("include_airlines", "outbound_times", "return_times"):
            if getattr(spec, k):
                p[k] = getattr(spec, k)
        return p

    def search(self, spec: SearchSpec) -> SearchResult:
        try:
            r = requests.get(SERPAPI_URL, params=dict(self.params(spec), api_key=self.api_key),
                             timeout=self.timeout)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            # Never let the key leak into logs via the URL in the exception text.
            raise ProviderError(str(e).replace(self.api_key, "***")) from None
        if r.status_code != 200 and not data.get("error"):
            raise ProviderError(f"HTTP {r.status_code}")
        if self.save_dir and not data.get("error"):
            self.save_dir.mkdir(parents=True, exist_ok=True)
            (self.save_dir / f"{spec.id}.json").write_text(json.dumps(data, indent=1).replace(self.api_key, "***"))
        return parse_google_flights(spec, data)


class SampleProvider(FlightProvider):
    """Dry-run provider: replays saved responses, zero API calls.

    Uses samples/<search id>.json when present. Without one, a one-way search reuses any saved
    one-way sample for the same direction, and a return-ticket search is faked from the one-way
    samples (both flights at a 30% return-ticket discount) so messages can still be previewed.
    """
    name = "sample"

    def __init__(self, folder: str | os.PathLike = "samples"):
        self.folder = Path(folder)

    def _load(self, name: str) -> dict | None:
        path = self.folder / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def _one_way(self, direction: str, date: str) -> dict:
        prefix = "ow_out_" if direction == "out" else "ow_back_"
        exact = self._load(prefix + date.replace("-", "")[4:])
        if exact:
            return exact
        any_file = sorted(self.folder.glob(prefix + "*.json"))
        if not any_file:
            raise ProviderError(f"no sample for {prefix}*")
        return json.loads(any_file[0].read_text())

    def search(self, spec: SearchSpec) -> SearchResult:
        data = self._load(spec.id)
        if data is None and spec.type == "one_way":
            data = self._one_way(spec.direction, spec.outbound_date)
        if data is None:
            data = self._fake_return(spec)
        return parse_google_flights(spec, data)

    def _fake_return(self, spec: SearchSpec) -> dict:
        def pick(sample, hours):
            lo = int(hours.split(",")[0]) if hours else 0
            for g in ("best_flights", "other_flights"):
                for it in sample.get(g, []):
                    leg = it["flights"][0]
                    if (_code(leg["flight_number"]) == spec.include_airlines
                            and int(leg["departure_airport"]["time"][-5:-3]) == lo):
                        return it
            return None
        out = pick(self._one_way("out", spec.outbound_date), spec.outbound_times)
        back = pick(self._one_way("back", spec.return_date), spec.return_times)
        if not out:
            return {"best_flights": [], "other_flights": []}
        p_out = out.get("price") or 300
        p_back = (back or {}).get("price") or 200
        return {"best_flights": [dict(out, price=round((p_out + p_back) * 0.7))], "other_flights": [],
                "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights"}}
