"""Coletores de preço. Interface única para que o Google Flights seja trocável (Skyscanner, cias, mock)."""
from __future__ import annotations

import logging
import random
import threading
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


# O gráfico de preços do Google cobre 60 dias, de (ida de referência - 7) em diante
GRAPH_DAYS = 60
GRAPH_LEAD = 7
# payload[5][0] da busca: 1 = "Os preços estão baixos", 2 = típicos, 3 = altos
PRICE_LEVELS = {1: "low", 2: "typical", 3: "high"}
# depois de um captcha, insistir só prolonga o bloqueio: o calendário fica parado esse tempo
BLOCK_COOLDOWN = 30 * 60
_blocked_until = 0.0
_browser_lock = threading.Lock()  # o Chromium só abre o perfil uma vez por vez


class Blocked(RuntimeError):
    """O Google pediu captcha ou bloqueou o IP."""


@dataclass
class SearchFlight:
    price: float
    airlines: str
    stops: int


def parse_search_payload(payload: list) -> tuple[list[SearchFlight], str | None]:
    """Lê os voos da página de busca: "Principais voos" (payload[2]) e "Outros voos" (payload[3]).
    A biblioteca só lê payload[3], e é em payload[2] que costuma estar o mais barato."""
    flights: list[SearchFlight] = []
    for idx in (2, 3):
        block = payload[idx] if len(payload) > idx and payload[idx] else None
        for k in (block[0] if block and block[0] else []):
            try:
                price = k[1][0][1]
                info = k[0]
            except (IndexError, TypeError):
                continue
            if not price:
                continue
            flights.append(SearchFlight(float(price), ", ".join(info[1])[:128], max(len(info[2]) - 1, 0)))
    level = None
    insight = payload[5] if len(payload) > 5 else None
    if isinstance(insight, list) and insight:
        level = PRICE_LEVELS.get(insight[0])
    return flights, level


def calendar_refs(window: tuple[str, str], duration: int) -> list[tuple[str, str]]:
    """Datas de referência (ida, volta) para que as páginas do gráfico cubram a janela inteira."""
    start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
    refs = []
    d = start
    while d <= end:
        out = d + timedelta(days=GRAPH_LEAD)
        refs.append((out.isoformat(), (out + timedelta(days=duration)).isoformat()))
        d += timedelta(days=GRAPH_DAYS)
    return refs


def parse_calendar(bodies: list[str], window: tuple[str, str]) -> list[CoarsePrice]:
    """Junta as respostas do GetCalendarGraph, só com as idas dentro da janela, uma por data."""
    seen: dict[str, CoarsePrice] = {}
    for body in bodies:
        for e in _calendar_entries(body):
            if e.price and window[0] <= e.outbound_date <= window[1] and e.outbound_date not in seen:
                seen[e.outbound_date] = CoarsePrice(e.outbound_date, e.return_date, float(e.price))
    return sorted(seen.values(), key=lambda c: c.outbound_date)


def _calendar_entries(body: str) -> list:
    """Parser da biblioteca, sem deixar uma resposta estranha derrubar a varredura."""
    from fast_flights.calendar import _parse_response

    try:
        return _parse_response(body, "")
    except (IndexError, TypeError, KeyError, ValueError):
        return []


def calendar_complete(bodies: list[str]) -> bool:
    """O gráfico faz mais de uma requisição; a que interessa traz a lista de datas, não 1 só."""
    return any(len(_calendar_entries(b)) > 1 for b in bodies)


def _query(watch: Watch, destination: str, outbound: str, ret: str):
    from fast_flights import FlightQuery, Passengers, create_query

    return create_query(
        flights=[
            FlightQuery(date=outbound, from_airport=watch.origin, to_airport=destination, max_stops=watch.max_stops),
            FlightQuery(date=ret, from_airport=destination, to_airport=watch.origin, max_stops=watch.max_stops),
        ],
        trip="round-trip",
        passengers=Passengers(adults=watch.adults),
        currency=watch.currency,
        language="pt-BR",
    )


def _playwright_proxy(url: str | None) -> dict | None:
    """O Playwright quer usuário e senha fora da URL do proxy."""
    if not url:
        return None
    from urllib.parse import unquote, urlsplit

    u = urlsplit(url)
    out = {"server": f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")}
    if u.username:
        out["username"] = unquote(u.username)
        out["password"] = unquote(u.password or "")
    return out


def _browser_calendar(urls: list[str]) -> list[str]:
    """Abre cada busca num Chromium headless, clica em "Gráfico de preços" e devolve as respostas.
    O Google passou a exigir um token anti-robô gerado no navegador (out/2026); sem ele o
    GetCalendarGraph responde erro, por isso não dá para chamar o RPC direto como na busca."""
    import re

    from playwright.sync_api import sync_playwright

    bodies: list[str] = []
    proxy = _playwright_proxy(settings.proxy_url)
    with sync_playwright() as p:
        # perfil persistente: os cookies do Google entre rodadas reduzem o captcha
        browser = p.chromium.launch_persistent_context(
            str(settings.browser_profile), channel="chromium", proxy=proxy, locale="pt-BR",
            args=["--disable-blink-features=AutomationControlled"])
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            for i, url in enumerate(urls):
                if i:
                    _sleep()
                got: list[str] = []

                def grab(resp):
                    if "GetCalendarGraph" in resp.url:
                        try:
                            got.append(resp.text())
                        except Exception:  # resposta de redirecionamento, sem corpo
                            pass

                page.on("response", grab)
                page.goto(url, wait_until="domcontentloaded")
                if "/sorry/" in page.url:
                    raise Blocked("Google pediu captcha (bloqueio temporário do IP)")
                page.get_by_role("button", name=re.compile("Gráfico de preços|Price graph")).click(timeout=30000)
                for _ in range(40):
                    if calendar_complete(got):
                        break
                    page.wait_for_timeout(500)
                page.remove_listener("response", grab)
                if not got:
                    raise RuntimeError("o gráfico de preços não respondeu")
                bodies.extend(got)
        finally:
            browser.close()
    return bodies


class GoogleFlightsCollector:
    name = "google_flights"

    def coarse(self, watch: Watch, destination: str, duration: int) -> list[CoarsePrice]:
        global _blocked_until
        window = watch.search_window()
        if window is None:
            return []
        if time.time() < _blocked_until:
            raise Blocked("calendário em pausa depois de um captcha do Google")
        _sleep()
        urls = [_query(watch, destination, out, ret).url() for out, ret in calendar_refs(window, duration)]
        try:
            with _browser_lock:
                bodies = _browser_calendar(urls)
            return parse_calendar(bodies, window)
        except Blocked:
            _blocked_until = time.time() + BLOCK_COOLDOWN
            raise

    def fine(self, watch: Watch, destination: str, outbound: str, ret: str) -> list[Offer]:
        import json

        from fast_flights.fetcher import fetch_flights_html
        from selectolax.lexbor import LexborHTMLParser

        _sleep()
        q = _query(watch, destination, outbound, ret)
        html = fetch_flights_html(q, proxy=settings.proxy_url)
        script = LexborHTMLParser(html).css_first(r"script.ds\:1")
        if script is None:
            raise RuntimeError("página de busca sem dados de voos (layout mudou ou bloqueio)")
        payload = json.loads(script.text().split("data:", 1)[1].rsplit(",", 1)[0])
        flights, level = parse_search_payload(payload)
        url = q.url()
        return [Offer(outbound, ret, f.price, f.airlines, f.stops, url, level) for f in flights]


class MockCollector:
    """Gera preços plausíveis para testar o pipeline sem bater no Google."""

    name = "mock"

    def __init__(self, seed: int = 42, bargain: bool = True):
        self.rng = random.Random(seed)
        self.bargain = bargain

    def coarse(self, watch: Watch, destination: str, duration: int) -> list[CoarsePrice]:
        out: list[CoarsePrice] = []
        window = watch.search_window()
        if window is None:
            return out
        d, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
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
