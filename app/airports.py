"""Base de aeroportos com voo regular (app/data/airports.csv, gerada por scripts/build_airports.py).

Serve a busca do formulário de rotas ("guarulhos", "foz do iguacu", "lima peru", "GRU") e os nomes
de cidade e aeroporto mostrados nas telas e mensagens.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "airports.csv"

# código de cidade (IATA metropolitano) -> (cidade, aeroportos), na ordem em que aparecem
METROS: dict[str, tuple[str, tuple[str, ...]]] = {
    "SAO": ("São Paulo", ("GRU", "CGH", "VCP")),
    "RIO": ("Rio de Janeiro", ("GIG", "SDU")),
    "BHZ": ("Belo Horizonte", ("CNF", "PLU")),
    "BUE": ("Buenos Aires", ("EZE", "AEP")),
    "NYC": ("Nova York", ("JFK", "EWR", "LGA")),
    "LON": ("Londres", ("LHR", "LGW", "STN", "LTN", "LCY")),
    "PAR": ("Paris", ("CDG", "ORY")),
    "TYO": ("Tóquio", ("HND", "NRT")),
}


def norm(s: str) -> str:
    """Minúsculas, sem acento e só letras/números: 'Foz do Iguaçu' -> 'foz do iguacu'."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


@dataclass(frozen=True)
class Airport:
    code: str
    city: str
    name: str
    region: str   # UF no Brasil, país fora
    country: str  # ISO de 2 letras
    size: int     # 0 grande, 1 médio, 2 pequeno

    @property
    def text(self) -> str:
        """'Foz do Iguaçu, Cataratas (IGU)', 'Natal (NAT)': o que fica escrito no campo."""
        place = self.city if self.name == self.city else f"{self.city}, {self.name}"
        return f"{place} ({self.code})"


@dataclass(frozen=True)
class _Entry:
    airport: Airport
    city: str
    name: str
    words: frozenset[str]
    main_words: frozenset[str]


@lru_cache(maxsize=1)
def _load() -> dict[str, _Entry]:
    out = {}
    with DATA.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            a = Airport(r["code"], r["city"], r["name"], r["region"], r["country"], int(r["size"]))
            main = norm(f"{a.city} {a.name}")
            out[a.code] = _Entry(a, norm(a.city), norm(a.name), frozenset(norm(f"{main} {a.region} {r['aliases']}").split()),
                                 frozenset(main.split()))
    return out


def get(code: str) -> Airport | None:
    e = _load().get((code or "").strip().upper())
    return e.airport if e else None


def _score(e: _Entry, q: str, tokens: list[str], code: str) -> int | None:
    if e.airport.code == code:
        return 0
    if e.city.startswith(q):
        return 1
    if e.name.startswith(q):
        return 2
    if all(any(w.startswith(t) for w in e.main_words) for t in tokens):
        return 3
    if all(any(w.startswith(t) for w in e.words) for t in tokens):
        return 4
    return None


def _option(codes: list[str], label: str, detail: str, items: list[Airport]) -> dict:
    return {"codes": codes, "label": label, "detail": detail,
            "items": [{"code": a.code, "text": a.text} for a in items]}


def search(query: str, limit: int = 8, groups: bool = True) -> list[dict]:
    """Aeroportos que combinam com o texto, os mais prováveis primeiro (Brasil antes de fora).

    Com `groups`, cidades com vários aeroportos (São Paulo, Rio…) ganham a opção "todos os aeroportos".
    """
    q = norm(query)
    if len(q) < 2:
        return []
    tokens, code = q.split(), q.upper().replace(" ", "")
    entries = _load()
    found = []
    for e in entries.values():
        s = _score(e, q, tokens, code)
        if s is not None:
            a = e.airport
            found.append(((s, a.country != "BR", a.size, e.city, a.code), _option(
                [a.code], f"{a.city} · {a.code}", a.region if a.name == a.city else f"{a.name}, {a.region}", [a])))
    if groups:
        for metro, (city, members) in METROS.items():
            items = [entries[c] for c in members if c in entries]
            ranks = [_score(e, q, tokens, code) for e in items]
            if norm(city).startswith(q) or code == metro:
                s = 1
            elif len(items) > 1 and all(r is not None for r in ranks):
                s = min(ranks)
            else:
                continue
            first = items[0].airport
            # -1 coloca o grupo antes dos aeroportos dele, que empatam no resto
            found.append(((s, first.country != "BR", -1, norm(city), metro), _option(
                [e.airport.code for e in items], f"{city}, todos os aeroportos",
                ", ".join(e.airport.code for e in items), [e.airport for e in items])))
    found.sort(key=lambda t: t[0])
    return [opt for _, opt in found[:limit]]
