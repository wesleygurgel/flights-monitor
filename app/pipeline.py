"""Orquestra: varredura grossa -> candidatas -> busca fina -> regras -> alerta.

Cada rodada é registrada em `search_runs` (quantas consultas, quantas falharam, melhor preço),
o que alimenta o preço normal da rota, o status no painel e o aviso de coleta quebrada.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select

from .collectors import Collector, CoarsePrice
from .config import Watch
from .db import Alert, PriceSnapshot, SearchRun, SessionLocal, utcnow
from .notifiers import format_health_message, format_message, notify_all
from .rules import evaluate, join_reasons, offer_key

log = logging.getLogger(__name__)

FAIL_STREAK_ALERT = 2  # avisa quando a rota acumula 2 rodadas seguidas sem resultado
STALE_RUNNING = timedelta(minutes=30)  # rodada "em andamento" mais velha que isso é considerada morta


class RunStats:
    def __init__(self) -> None:
        self.queries = 0
        self.failures = 0
        self.results = 0
        self.best: float | None = None
        self.last_error: str | None = None

    def ok(self, prices: list[float]) -> None:
        self.queries += 1
        self.results += len(prices)
        if prices:
            m = min(prices)
            self.best = m if self.best is None else min(self.best, m)

    def fail(self, e: Exception) -> None:
        self.queries += 1
        self.failures += 1
        self.last_error = f"{type(e).__name__}: {e}"[:500]

    @property
    def status(self) -> str:
        if self.queries and self.failures == self.queries:
            return "failed"
        if self.results == 0:
            return "empty"
        return "partial" if self.failures else "ok"


@contextmanager
def tracked_run(watch: Watch, kind: str, trigger: str):
    """Abre o registro da rodada, entrega o contador e fecha com status ao final."""
    with SessionLocal() as s:
        run = SearchRun(watch_id=watch.id, kind=kind, trigger=trigger, status="running")
        s.add(run)
        s.commit()
        run_id = run.id
    stats = RunStats()
    try:
        yield stats
    except Exception as e:  # erro inesperado fora das consultas
        stats.fail(e)
        raise
    finally:
        with SessionLocal() as s:
            run = s.get(SearchRun, run_id)
            run.queries, run.failures, run.results = stats.queries, stats.failures, stats.results
            run.best_price = stats.best if kind == "fine" else None
            run.status = stats.status
            run.error = stats.last_error
            run.finished_at = utcnow()
            s.commit()
        log.info("%s %s: %s (%d consultas, %d falhas, %d resultados)", kind, watch.id, stats.status,
                 stats.queries, stats.failures, stats.results)


def run_coarse(watch: Watch, collector: Collector, trigger: str = "schedule") -> list[tuple[str, CoarsePrice]]:
    """Varre o calendário para cada destino x duração. Retorna as N (destino, data) mais baratas."""
    found: list[tuple[str, CoarsePrice]] = []
    with tracked_run(watch, "coarse", trigger) as stats, SessionLocal() as s:
        for dest in watch.destinations:
            for dur in watch.durations:
                try:
                    entries = collector.coarse(watch, dest, dur)
                except Exception as e:
                    stats.fail(e)
                    log.warning("coarse %s %s dur=%s falhou: %s", watch.id, dest, dur, e)
                    continue
                stats.ok([e.price for e in entries])
                for e in entries:
                    found.append((dest, e))
                    s.add(PriceSnapshot(
                        watch_id=watch.id, source="coarse", origin=watch.origin, destination=dest,
                        outbound_date=e.outbound_date, return_date=e.return_date,
                        price=e.price, currency=watch.currency,
                    ))
        s.commit()
    found.sort(key=lambda t: t[1].price)
    return found[: watch.candidates]


def channels_for(watch: Watch, notifiers: list) -> list:
    """Respeita os canais marcados na rota (o console de --dry-run passa sempre)."""
    wanted = {"console": True, "email": watch.notify_email, "whatsapp": watch.notify_whatsapp}
    return [n for n in notifiers if wanted.get(n.name, False)]


def run_fine(watch: Watch, collector: Collector, candidates: list[tuple[str, CoarsePrice]], notifiers: list,
             trigger: str = "schedule") -> int:
    """Busca detalhada nas candidatas, grava, avalia regras e dispara alertas. Retorna nº de alertas."""
    sent = 0
    notifiers = channels_for(watch, notifiers)
    with tracked_run(watch, "fine", trigger) as stats, SessionLocal() as s:
        for dest, c in candidates:
            try:
                offers = collector.fine(watch, dest, c.outbound_date, c.return_date)
            except Exception as e:
                stats.fail(e)
                log.warning("fine %s %s %s falhou: %s", watch.id, dest, c.outbound_date, e)
                continue
            stats.ok([o.price for o in offers])
            for o in offers:
                s.add(PriceSnapshot(
                    watch_id=watch.id, source="fine", origin=watch.origin, destination=dest,
                    outbound_date=o.outbound_date, return_date=o.return_date, price=o.price,
                    currency=watch.currency, airlines=o.airlines, stops=o.stops,
                    price_level=o.price_level, url=o.url,
                ))
            s.commit()
            if not offers:
                continue
            best = min(offers, key=lambda x: x.price)
            v = evaluate(s, watch, dest, best)
            log.info("fine %s %s %s→%s best=%.0f alert=%s %s", watch.id, dest, best.outbound_date,
                     best.return_date, best.price, v.alert, v.reasons)
            if v.alert:
                subject, body = format_message(watch, dest, best, v.reasons)
                delivered = notify_all(notifiers, subject, body)
                s.add(Alert(watch_id=watch.id, offer_key=offer_key(watch, dest, best), price=best.price,
                            reason=join_reasons(v.reasons), channels=",".join(delivered)))
                s.commit()
                sent += 1
    return sent


def last_candidates(watch: Watch, limit: int) -> list[tuple[str, CoarsePrice]]:
    """Recupera as candidatas da última varredura grossa gravada (para o job fino rodar sozinho)."""
    with SessionLocal() as s:
        latest = s.execute(
            select(PriceSnapshot.observed_at).where(
                PriceSnapshot.watch_id == watch.id, PriceSnapshot.source == "coarse"
            ).order_by(PriceSnapshot.observed_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return []
        since = latest - timedelta(hours=1)  # mesma rodada de varredura
        rows = s.execute(
            select(PriceSnapshot).where(
                PriceSnapshot.watch_id == watch.id, PriceSnapshot.source == "coarse",
                PriceSnapshot.observed_at >= since,
            ).order_by(PriceSnapshot.price).limit(limit)
        ).scalars().all()
    return [(r.destination, CoarsePrice(r.outbound_date, r.return_date, r.price)) for r in rows]


def check_health(watch: Watch, notifiers: list) -> str | None:
    """Avisa quando a coleta da rota quebra (N rodadas seguidas sem resultado) e quando volta.

    Só manda no momento da virada, para não repetir o aviso a cada rodada.
    Retorna "down", "up" ou None (para testes e log).
    """
    with SessionLocal() as s:
        runs = s.execute(
            select(SearchRun).where(SearchRun.watch_id == watch.id, SearchRun.status != "running")
            .order_by(SearchRun.started_at.desc(), SearchRun.id.desc()).limit(FAIL_STREAK_ALERT + 1)
        ).scalars().all()
    if len(runs) < FAIL_STREAK_ALERT:
        return None
    streak = runs[:FAIL_STREAK_ALERT]
    before = runs[FAIL_STREAK_ALERT] if len(runs) > FAIL_STREAK_ALERT else None
    event = None
    if all(r.is_bad for r in streak) and (before is None or not before.is_bad):
        event = "down"
    elif not runs[0].is_bad and len(runs) > FAIL_STREAK_ALERT and all(r.is_bad for r in runs[1:]):
        event = "up"
    if event:
        subject, body = format_health_message(watch, event, runs[0])
        notify_all(channels_for(watch, notifiers), subject, body)
    return event


def run_watch(watch: Watch, collector: Collector, notifiers: list, trigger: str = "schedule",
              coarse: bool = True, fine: bool = True) -> dict:
    """Ciclo completo de uma rota. Usado pelo agendador, pela CLI e pelo botão "Buscar agora"."""
    out = {"candidates": 0, "alerts": 0}
    if coarse:
        cands = run_coarse(watch, collector, trigger)
    else:
        cands = last_candidates(watch, watch.candidates)
    out["candidates"] = len(cands)
    if fine and cands:
        out["alerts"] = run_fine(watch, collector, cands, notifiers, trigger)
    elif fine:
        log.warning("%s: sem candidatas da varredura grossa; busca fina pulada", watch.id)
    check_health(watch, notifiers)
    return out


def is_running(watch_id: str) -> bool:
    with SessionLocal() as s:
        run = s.execute(
            select(SearchRun).where(SearchRun.watch_id == watch_id, SearchRun.status == "running")
            .order_by(SearchRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()
    if not run:
        return False
    from .db import as_utc
    return utcnow() - as_utc(run.started_at) < STALE_RUNNING
