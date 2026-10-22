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

    def __init__(self, api_key: str, currency="SGD", gl="sg", hl="en", timeout=60):
        self.api_key = api_key.strip()
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
        return parse_google_flights(spec, data)


class SampleProvider(FlightProvider):
    """Dry-run provider: replays saved responses, zero API calls.

    Uses samples/<search id>.json when present, else samples/core_rt.json.
    """
    name = "sample"

    def __init__(self, folder: str | os.PathLike = "samples"):
        self.folder = Path(folder)

    def search(self, spec: SearchSpec) -> SearchResult:
        path = self.folder / f"{spec.id}.json"
        if not path.exists():
            path = self.folder / "core_rt.json"
        return parse_google_flights(spec, json.loads(path.read_text()))
