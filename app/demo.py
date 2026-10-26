"""Popula um banco com 30 dias de histórico fictício para ver o painel funcionando.

    DATABASE_URL=sqlite:///./demo.db python -m app.main demo

Não use no banco de produção: as rotas e preços gerados são inventados.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy import delete

from .db import Alert, PriceSnapshot, Route, SearchRun, SessionLocal, init_db, utcnow

AIRLINES = ["LATAM", "GOL", "Azul"]


def _route(s, **kw) -> Route:
    s.execute(delete(PriceSnapshot).where(PriceSnapshot.watch_id == kw["id"]))
    s.execute(delete(Alert).where(Alert.watch_id == kw["id"]))
    s.execute(delete(SearchRun).where(SearchRun.watch_id == kw["id"]))
    r = s.get(Route, kw["id"]) or Route(id=kw["id"])
    for k, v in kw.items():
        setattr(r, k, v)
    s.add(r)
    return r


def _run(s, watch_id: str, at, best: float | None, kind: str = "fine", status: str = "ok",
         queries: int = 5, error: str | None = None) -> None:
    s.add(SearchRun(watch_id=watch_id, kind=kind, trigger="schedule", status=status, queries=queries,
                    failures=queries if status == "failed" else 0, results=0 if status == "failed" else queries * 3,
                    best_price=best, error=error, started_at=at, finished_at=at + timedelta(minutes=2)))


def _fine_history(s, r: Route, trend_from: float, trend_to: float, days: int, runs_per_day: int,
                  dests: list[str], rng: random.Random, best_dates: tuple[str, str]) -> None:
    now = utcnow()
    for d in range(days, -1, -1):
        for run in range(runs_per_day):
            at = now - timedelta(days=d, hours=(runs_per_day - 1 - run) * 6)
            if at > now:
                continue
            base = trend_to + (trend_from - trend_to) * (d / days) + rng.uniform(-45, 45)
            _run(s, r.id, at, trend_to if d == 0 else round(base, 2), queries=r.candidates)
            if run == 0:
                _run(s, r.id, at - timedelta(hours=2), None, kind="coarse", queries=15)
            for c in range(r.candidates):
                out = date.fromisoformat(r.depart_from) + timedelta(days=rng.randrange(0, 55))
                ret = out + timedelta(days=r.duration_days)
                if d == 0 and c == 0:
                    out, ret = date.fromisoformat(best_dates[0]), date.fromisoformat(best_dates[1])
                for k, airline in enumerate(AIRLINES):
                    price = base + c * 38 + k * 70 + rng.uniform(0, 25)
                    if d == 0 and c == 0 and k == 0:
                        price = trend_to
                    s.add(PriceSnapshot(
                        watch_id=r.id, source="fine", origin=r.origin, destination=dests[(c + k) % len(dests)] if not (d == 0 and c == 0) else dests[0],
                        outbound_date=out.isoformat(), return_date=ret.isoformat(), price=round(price, 2),
                        currency="BRL", airlines=airline, stops=0 if k != 1 else 1,
                        url="https://www.google.com/travel/flights", observed_at=at,
                    ))


def _coarse_today(s, r: Route, dests: list[str], rng: random.Random, cheap: dict[int, float]) -> None:
    at = utcnow() - timedelta(hours=2)
    d0, d1 = date.fromisoformat(r.depart_from), date.fromisoformat(r.depart_to)
    for dest in dests:
        d = d0
        while d <= d1:
            weekend = d.weekday() in (4, 6)
            p = 1060 + (260 if weekend else 0) + rng.uniform(0, 300) + (40 if dest != dests[0] else 0)
            key = d.toordinal()
            if key in cheap and dest == dests[0]:
                p = cheap[key]
            s.add(PriceSnapshot(
                watch_id=r.id, source="coarse", origin=r.origin, destination=dest,
                outbound_date=d.isoformat(), return_date=(d + timedelta(days=r.duration_days)).isoformat(),
                price=round(p, 2), currency="BRL", observed_at=at,
            ))
            d += timedelta(days=1)


def run() -> None:
    init_db()
    rng = random.Random(7)
    with SessionLocal() as s:
        sao = _route(s, id="nat-sao-mar-abr-2027", origin="NAT", destinations="GRU,CGH,VCP",
                     depart_from="2027-03-01", depart_to="2027-04-30", duration_days=7, duration_tolerance=2,
                     adults=1, max_stops=1, max_price=900, drop_pct=20, min_samples=20, candidates=5,
                     currency="BRL", active=True, notify_email=True, notify_whatsapp=True, paused_at=None,
                     created_at=utcnow() - timedelta(days=31))
        lis = _route(s, id="nat-lis-demo", origin="NAT", destinations="LIS", depart_from="2027-05-01",
                     depart_to="2027-06-15", duration_days=14, duration_tolerance=3, adults=1, max_stops=1,
                     max_price=3800, drop_pct=20, min_samples=20, candidates=4, currency="BRL", active=True,
                     notify_email=True, notify_whatsapp=False, paused_at=None, created_at=utcnow() - timedelta(days=2))
        poa = _route(s, id="nat-poa-demo", origin="NAT", destinations="POA", depart_from="2027-07-01",
                     depart_to="2027-07-31", duration_days=5, duration_tolerance=1, adults=1, max_stops=1,
                     max_price=1100, drop_pct=20, min_samples=20, candidates=3, currency="BRL", active=False,
                     notify_email=True, notify_whatsapp=True, paused_at=utcnow() - timedelta(days=6),
                     created_at=utcnow() - timedelta(days=20))
        s.flush()

        _fine_history(s, sao, 1250, 812, 30, 3, ["GRU", "CGH", "VCP"], rng, ("2027-03-17", "2027-03-24"))
        cheap = {date(2027, 3, 17).toordinal(): 812, date(2027, 3, 16).toordinal(): 860,
                 date(2027, 4, 22).toordinal(): 885, date(2027, 4, 1).toordinal(): 1720}
        _coarse_today(s, sao, ["GRU", "CGH", "VCP"], rng, cheap)
        now = utcnow()
        s.add(Alert(watch_id=sao.id, offer_key="NAT|GRU|2027-03-17|2027-03-24", price=870,
                    reason="abaixo do teto de R$ 900 e 26% abaixo do normal de R$ 1.180",
                    channels="email,whatsapp", sent_at=now - timedelta(hours=18)))
        s.add(Alert(watch_id=sao.id, offer_key="NAT|GRU|2027-03-17|2027-03-24", price=812,
                    reason="abaixo do teto de R$ 900, 31% abaixo do normal de R$ 1.180 e caiu de R$ 870",
                    channels="email,whatsapp", sent_at=now - timedelta(hours=6)))

        # Lisboa: 2 dias de coleta, ainda formando o preço normal (24 buscas)
        _run(s, lis.id, now - timedelta(minutes=20), None, status="failed", queries=4,
             error="RuntimeError: Google Flights returned HTTP 429. The request may have been blocked or rate-limited.")
        for i in range(1, 7):
            at = now - timedelta(hours=i * 8)
            _run(s, lis.id, at, 4390 + i * 55, queries=4)
            for c in range(4):
                out = date(2027, 5, 12) + timedelta(days=c * 3)
                s.add(PriceSnapshot(watch_id=lis.id, source="fine", origin="NAT", destination="LIS",
                                    outbound_date=out.isoformat(), return_date=(out + timedelta(days=14)).isoformat(),
                                    price=4390 + i * 55 + c * 90, currency="BRL", airlines="TAP", stops=1,
                                    url="https://www.google.com/travel/flights", observed_at=at))

        # Porto Alegre: pausada, poucas buscas antigas
        for i in range(5):
            at = now - timedelta(days=10 - i)
            _run(s, poa.id, at, 1610 - i * 20, queries=3)
            s.add(PriceSnapshot(watch_id=poa.id, source="fine", origin="NAT", destination="POA",
                                outbound_date="2027-07-10", return_date="2027-07-15", price=1610 - i * 20,
                                currency="BRL", airlines="GOL", stops=1,
                                url="https://www.google.com/travel/flights", observed_at=at))
        s.commit()
    _bonus_demo()
    print("Demo pronta: 3 rotas, 30 dias de histórico para NAT SAO e campanhas de bônus Azul.")


def _bonus_demo() -> None:
    """Campanhas passadas (histórico) + leitura dos feeds simulados (campanhas ativas)."""
    from .bonus.service import run_bonus
    from .db import BonusCampaign, BonusPost, BonusSettings
    from .notifiers import ConsoleNotifier

    with SessionLocal() as s:
        for m in (BonusPost, BonusCampaign, BonusSettings):
            s.execute(delete(m))
        s.execute(delete(SearchRun).where(SearchRun.watch_id.like("bonus:%")))
        now = utcnow()
        for program, pct, days_ago, dur in [("Esfera", 120, 130, 4), ("Itaú", 120, 70, 5), ("Livelo", 90, 80, 10),
                                            ("Esfera", 85, 95, 6), ("C6 Bank", 110, 160, 7), ("Livelo", 70, 30, 5)]:
            first = now - timedelta(days=days_ago)
            c = BonusCampaign(program=program, bonus_pct=pct, ends_on=(first + timedelta(days=dur)).date().isoformat(),
                              first_seen_at=first, last_seen_at=first + timedelta(days=1),
                              alerted_pct=pct if pct >= 80 else None, alerted_at=first if pct >= 80 else None)
            s.add(c)
            s.flush()
            s.add(BonusPost(source="Passageiro de Primeira", url=f"https://passageirodeprimeira.com/?demo={c.id}",
                            title=f"Azul Fidelidade oferece até {pct}% de bônus nas transferências de pontos {program}",
                            published_at=first, program=program, bonus_pct=pct, ends_on=c.ends_on, campaign_id=c.id))
        s.commit()
    run_bonus([ConsoleNotifier()], trigger="schedule")


if __name__ == "__main__":
    run()
