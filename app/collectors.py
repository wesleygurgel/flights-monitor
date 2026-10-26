"""Coletores de preço. Interface única para que o Google Flights seja trocável (Skyscanner, cias, mock)."""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from .config import Watch, settings

log = logging.getLogger(__name__)


@dataclass
class CoarsePrice:
    outbound_date: str
    return_date: str
    price: float


@dataclass
class Offer:
    outbound_date: str
    return_date: str
    price: float
    airlines: str
    stops: int
    url: str
    price_level: str | None = None


class Collector(Protocol):
    name: str

    def coarse(self, watch: Watch, destination: str, duration: int) -> list[CoarsePrice]: ...
    def fine(self, watch: Watch, destination: str, outbound: str, ret: str) -> list[Offer]: ...


def _sleep() -> None:
    d = settings.request_delay
    if d > 0:
        time.sleep(d + random.uniform(0, d / 2))


class GoogleFlightsCollector:
    name = "google_flights"

    def coarse(self, watch: Watch, destination: str, duration: int) -> list[CoarsePrice]:
        from fast_flights import get_calendar_graph

        _sleep()
        cal = get_calendar_graph(
            from_airport=watch.origin,
            to_airport=destination,
            date_range=(watch.depart_from, watch.depart_to),
            trip_duration=duration,
            adults=watch.adults,
            max_stops=watch.max_stops,
            currency=watch.currency,
            language="pt-BR",
        )
        return [CoarsePrice(e.outbound_date, e.return_date, float(e.price)) for e in cal.entries if e.price]

    def fine(self, watch: Watch, destination: str, outbound: str, ret: str) -> list[Offer]:
        from fast_flights import FlightQuery, Passengers, create_query, get_flights

        _sleep()
        q = create_query(
            flights=[
                FlightQuery(date=outbound, from_airport=watch.origin, to_airport=destination, max_stops=watch.max_stops),
                FlightQuery(date=ret, from_airport=destination, to_airport=watch.origin, max_stops=watch.max_stops),
            ],
            trip="round-trip",
            passengers=Passengers(adults=watch.adults),
            currency=watch.currency,
            language="pt-BR",
        )
        result = get_flights(q)
        flights = getattr(result, "flights", result)
        level = getattr(result, "current_price", None)
        url = q.url()
        offers: list[Offer] = []
        for f in flights:
            if not f.price:
                continue
            offers.append(
                Offer(
                    outbound_date=outbound,
                    return_date=ret,
                    price=float(f.price),
                    airlines=", ".join(f.airlines)[:128],
                    stops=max(len(f.flights) - 1, 0),
                    url=url,
                    price_level=level,
                )
            )
        return offers


class MockCollector:
    """Gera preços plausíveis para testar o pipeline sem bater no Google."""

    name = "mock"

    def __init__(self, seed: int = 42, bargain: bool = True):
        self.rng = random.Random(seed)
        self.bargain = bargain

    def coarse(self, watch: Watch, destination: str, duration: int) -> list[CoarsePrice]:
        out: list[CoarsePrice] = []
        d = date.fromisoformat(watch.depart_from)
        end = date.fromisoformat(watch.depart_to)
        while d <= end:
            base = 1300 + 400 * (d.weekday() in (4, 6))
            price = base + self.rng.uniform(-250, 350)
            if self.bargain and d.day == 17:  # uma "promoção" fixa para o teste
                price = 780
            out.append(CoarsePrice(d.isoformat(), (d + timedelta(days=duration)).isoformat(), round(price, 2)))
            d += timedelta(days=1)
        return out

    def fine(self, watch: Watch, destination: str, outbound: str, ret: str) -> list[Offer]:
        base = 780 if outbound.endswith("-17") else 1200 + self.rng.uniform(0, 400)
        url = f"https://www.google.com/travel/flights?q=Flights%20{watch.origin}%20to%20{destination}%20{outbound}"
        return [
            Offer(outbound, ret, round(base, 2), "LATAM", 0, url, "low" if base < 900 else "typical"),
            Offer(outbound, ret, round(base * 1.15, 2), "GOL", 1, url),
        ]


def get_collector(name: str = "google_flights") -> Collector:
    return {"google_flights": GoogleFlightsCollector(), "mock": MockCollector()}[name]
