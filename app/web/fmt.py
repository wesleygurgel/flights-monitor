"""Formatação pt-BR e nomes de aeroportos usados nas telas."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..config import settings

TZ = ZoneInfo(settings.tz)

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

# código -> (cidade, nome do aeroporto)
AIRPORTS: dict[str, tuple[str, str]] = {
    "NAT": ("Natal", "Natal"),
    "GRU": ("São Paulo", "Guarulhos"), "CGH": ("São Paulo", "Congonhas"), "VCP": ("São Paulo", "Viracopos"),
    "GIG": ("Rio de Janeiro", "Galeão"), "SDU": ("Rio de Janeiro", "Santos Dumont"),
    "BSB": ("Brasília", "Brasília"), "CNF": ("Belo Horizonte", "Confins"), "PLU": ("Belo Horizonte", "Pampulha"),
    "REC": ("Recife", "Recife"), "FOR": ("Fortaleza", "Fortaleza"), "SSA": ("Salvador", "Salvador"),
    "JPA": ("João Pessoa", "João Pessoa"), "MCZ": ("Maceió", "Maceió"), "AJU": ("Aracaju", "Aracaju"),
    "POA": ("Porto Alegre", "Salgado Filho"), "CWB": ("Curitiba", "Afonso Pena"), "FLN": ("Florianópolis", "Hercílio Luz"),
    "BEL": ("Belém", "Belém"), "MAO": ("Manaus", "Manaus"), "GYN": ("Goiânia", "Goiânia"),
    "CXJ": ("Caxias do Sul", "Caxias do Sul"), "FEN": ("Fernando de Noronha", "Noronha"),
    "LIS": ("Lisboa", "Lisboa"), "OPO": ("Porto", "Porto"), "MAD": ("Madri", "Barajas"),
    "CDG": ("Paris", "Charles de Gaulle"), "MIA": ("Miami", "Miami"), "MCO": ("Orlando", "Orlando"),
    "JFK": ("Nova York", "JFK"), "EZE": ("Buenos Aires", "Ezeiza"), "AEP": ("Buenos Aires", "Aeroparque"),
    "SCL": ("Santiago", "Santiago"), "MVD": ("Montevidéu", "Carrasco"), "LIM": ("Lima", "Lima"),
}

# conjuntos de aeroportos de uma mesma cidade -> código da cidade (IATA metropolitano)
METROS = {"SAO": {"GRU", "CGH", "VCP"}, "RIO": {"GIG", "SDU"}, "BHZ": {"CNF", "PLU"}, "BUE": {"EZE", "AEP"}}


def brl(v: float | None) -> str:
    if v is None:
        return "—"
    return "R$\u00a0" + f"{round(v):,}".replace(",", ".")


def num(v: float | None) -> str:
    return "—" if v is None else f"{round(v):,}".replace(",", ".")


def as_date(d: str | date) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def d_short(d: str | date) -> str:
    d = as_date(d)
    return f"{d.day} {MESES_CURTOS[d.month - 1]}"


def d_long(d: str | date) -> str:
    d = as_date(d)
    return f"{d.day} de {MESES[d.month - 1]}"


def range_short(a: str, b: str) -> str:
    da, db = as_date(a), as_date(b)
    if da.month == db.month:
        return f"{da.day} a {db.day} {MESES_CURTOS[db.month - 1]}"
    return f"{d_short(da)} a {d_short(db)}"


def local(dt: datetime) -> datetime:
    from ..db import as_utc
    return as_utc(dt).astimezone(TZ)


def when(dt: datetime) -> str:
    """'hoje, 8:02' / 'ontem, 20:01' / '24 out, 14:00'."""
    lt = local(dt)
    today = datetime.now(TZ).date()
    hm = f"{lt.hour}:{lt.minute:02d}"
    if lt.date() == today:
        return f"hoje, {hm}"
    if (today - lt.date()).days == 1:
        return f"ontem, {hm}"
    return f"{d_short(lt.date())}, {hm}"


def city(code: str) -> str:
    return AIRPORTS.get(code, (code, code))[0]


def airport(code: str) -> str:
    return AIRPORTS.get(code, (code, code))[1]


def route_code(origin: str, dests: list[str]) -> str:
    s = set(dests)
    for metro, members in METROS.items():
        if len(s) > 1 and s <= members:
            return f"{origin} {metro}"
    return f"{origin} {dests[0]}" if len(dests) == 1 else f"{origin} {'/'.join(dests)}"


def dest_city(dests: list[str]) -> str:
    cities = {city(d) for d in dests}
    return cities.pop() if len(cities) == 1 else ", ".join(city(d) for d in dests)


def dest_airports(dests: list[str]) -> str:
    names = [airport(d) for d in dests]
    if len(names) == 1:
        return city(dests[0]) if names[0] == city(dests[0]) else f"{city(dests[0])}, {names[0]}"
    return ", ".join(names[:-1]) + " ou " + names[-1]


def window(depart_from: str, depart_to: str, days: int, tol: int) -> str:
    a, b = as_date(depart_from), as_date(depart_to)
    if a.year == b.year and a.month == b.month:
        span = f"Ida entre {a.day} e {b.day} {MESES_CURTOS[b.month - 1]} de {b.year}"
    else:
        span = f"Ida entre {d_short(a)} e {d_short(b)} de {b.year}"
    dur = f"{days} dias" if not tol else f"{days - tol} a {days + tol} dias"
    return f"{span}, {dur}"


def stops(n: int | None) -> str:
    if n is None:
        return ""
    return "direto" if n == 0 else ("1 escala" if n == 1 else f"{n} escalas")
