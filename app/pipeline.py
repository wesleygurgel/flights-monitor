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
from .config import Watch, local_today
from .db import Alert, PriceSnapshot, Route, SearchRun, SessionLocal, as_utc, utcnow
from .notifiers import format_expired_message, format_health_message, format_message, notify_all
from .rules import evaluate, join_reasons, offer_key

log = logging.getLogger(__name__)

FAIL_STREAK_ALERT = 2  # avisa quando a rota acumula 2 rodadas seguidas sem resultado
STALE_RUNNING = timedelta(minutes=30)  # rodada "em andamento" mais velha que isso é considerada morta
COARSE_FRESH = timedelta(hours=12)  # varredura mais nova que isso é reaproveitada (busca manual, reinício)


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


def pick_candidates(found: list[tuple[str, CoarsePrice]], n: int) -> list[tuple[str, CoarsePrice]]:
    """As N datas de ida mais baratas, uma opção (destino x duração) por data.

    Sem isso, durações vizinhas da mesma data ocupam todas as vagas e a busca detalhada
    olha uma data só em vez de explorar o calendário.
    """
    best: dict[str, tuple[str, CoarsePrice]] = {}
    for dest, c in sorted(found, key=lambda t: t[1].price):
        best.setdefault(c.outbound_date, (dest, c))
    return sorted(best.values(), key=lambda t: t[1].price)[:n]


def run_coarse(watch: Watch, collector: Collector, trigger: str = "schedule") -> list[tuple[str, CoarsePrice]]:
    """Varre o calendário para cada destino x duração. Retorna as N datas de ida mais baratas."""
    found: list[tuple[str, CoarsePrice]] = []
    today = local_today().isoformat()
    with tracked_run(watch, "coarse", trigger) as stats, SessionLocal() as s:
        for dest in watch.destinations:
            for dur in watch.durations:
                try:
                    entries = collector.coarse(watch, dest, dur)
                except Exception as e:
                    stats.fail(e)
                    log.warning("coarse %s %s dur=%s falhou: %s", watch.id, dest, dur, e)
                    continue
                entries = [e for e in entries if e.outbound_date >= today]  # data que já passou não serve
                stats.ok([e.price for e in entries])
                for e in entries:
                    found.append((dest, e))
                    s.add(PriceSnapshot(
                        watch_id=watch.id, source="coarse", origin=watch.origin, destination=dest,
                        outbound_date=e.outbound_date, return_date=e.return_date,
                        price=e.price, currency=watch.currency,
                    ))
        s.commit()
    return pick_candidates(found, watch.candidates)


def channels_for(watch: Watch, notifiers: list) -> list:
    """Respeita os canais marcados na rota (o console de --dry-run passa sempre)."""
    wanted = {"console": True, "email": watch.notify_email, "whatsapp": watch.notify_whatsapp}
    return [n for n in notifiers if wanted.get(n.name, False)]


def run_fine(watch: Watch, collector: Collector, candidates: list[tuple[str, CoarsePrice]], notifiers: list,
             trigger: str = "schedule") -> int:
    """Busca detalhada nas candidatas, grava e avalia regras. As ofertas que viram alerta saem
    numa mensagem só ao fim da rodada. Retorna nº de ofertas alertadas."""
    notifiers = channels_for(watch, notifiers)
    deals: list[tuple[str, object, list[str]]] = []
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
                deals.append((dest, best, v.reasons))
        if deals and not notifiers:
            log.info("%s: %d ofertas baratas, mas a rota está sem canais de aviso", watch.id, len(deals))
            return 0
        if deals:
            subject, body = format_message(watch, deals)
            delivered = notify_all(notifiers, subject, body)  # vazio = ninguém entregou: a próxima rodada tenta de novo
            for dest, best, reasons in deals:
                s.add(Alert(watch_id=watch.id, offer_key=offer_key(watch, dest, best), price=best.price,
                            reason=join_reasons(reasons), channels=",".join(delivered)))
            s.commit()
    return len(deals)


def last_candidates(watch: Watch, limit: int) -> list[tuple[str, CoarsePrice]]:
    """Recupera as candidatas da última varredura grossa gravada (para o job fino rodar sozinho).

    Ignora datas de ida que já passaram e varreduras de antes da última mudança da rota.
    """
    base = [PriceSnapshot.watch_id == watch.id, PriceSnapshot.source == "coarse"]
    if watch.prices_since:
        base.append(PriceSnapshot.observed_at >= as_utc(watch.prices_since))
    with SessionLocal() as s:
        latest = s.execute(
            select(PriceSnapshot.observed_at).where(*base).order_by(PriceSnapshot.observed_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return []
        rows = s.execute(
            select(PriceSnapshot).where(
                *base, PriceSnapshot.observed_at >= latest - timedelta(hours=1),  # mesma rodada de varredura
                PriceSnapshot.outbound_date >= local_today().isoformat(),
            )
        ).scalars().all()
    return pick_candidates([(r.destination, CoarsePrice(r.outbound_date, r.return_date, r.price)) for r in rows],
                           limit)


ROUTE_HINT = ("Enquanto isso, nenhum alerta de preço sai para essa rota. Se o motivo for HTTP 429, o Google "
              "bloqueou o IP temporariamente: aumente REQUEST_DELAY ou reduza as datas checadas.")


def health_event(key: str) -> tuple[str | None, SearchRun | None]:
    """Detecta a virada: N rodadas seguidas ruins ("down") ou a primeira boa depois delas ("up")."""
    with SessionLocal() as s:
        runs = s.execute(
            select(SearchRun).where(SearchRun.watch_id == key, SearchRun.status != "running")
            .order_by(SearchRun.started_at.desc(), SearchRun.id.desc()).limit(FAIL_STREAK_ALERT + 1)
        ).scalars().all()
    if len(runs) < FAIL_STREAK_ALERT:
        return None, None
    streak = runs[:FAIL_STREAK_ALERT]
    before = runs[FAIL_STREAK_ALERT] if len(runs) > FAIL_STREAK_ALERT else None
    if all(r.is_bad for r in streak) and (before is None or not before.is_bad):
        return "down", runs[0]
    if not runs[0].is_bad and len(runs) > FAIL_STREAK_ALERT and all(r.is_bad for r in runs[1:]):
        return "up", runs[0]
    return None, None


def check_health(watch: Watch, notifiers: list) -> str | None:
    """Avisa quando a coleta da rota quebra e quando volta. Só na virada, para não repetir."""
    from .web.fmt import route_code

    event, run = health_event(watch.id)
    if event:
        subject, body = format_health_message(route_code(watch.origin, watch.destinations), event, run,
                                              ROUTE_HINT if event == "down" else "")
        notify_all(channels_for(watch, notifiers), subject, body)
    return event


def expire_route(watch: Watch, notifiers: list) -> None:
    """O período de ida acabou: pausa a rota e avisa (uma vez, porque depois ela fica pausada)."""
    from .web.fmt import route_code

    with SessionLocal() as s:
        r = s.get(Route, watch.id)
        if r is None or not r.active:
            return
        r.active, r.paused_at = False, utcnow()
        s.commit()
    log.info("%s: período de ida terminou em %s; rota pausada", watch.id, watch.depart_to)
    subject, body = format_expired_message(route_code(watch.origin, watch.destinations), watch.depart_to)
    notify_all(channels_for(watch, notifiers), subject, body)


def run_watch(watch: Watch, collector: Collector, notifiers: list, trigger: str = "schedule",
              coarse: bool = True, fine: bool = True) -> dict:
    """Ciclo completo de uma rota. Usado pelo agendador, pela CLI e pelo botão "Buscar agora"."""
    out = {"candidates": 0, "alerts": 0}
    if watch.search_window() is None:
        expire_route(watch, notifiers)
        return {**out, "expired": True}
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


def recent_run(watch_id: str, kind: str, fresh: timedelta = COARSE_FRESH, since=None) -> bool:
    """Houve rodada desse tipo que funcionou dentro do prazo (e depois da última mudança da rota)?"""
    start = utcnow() - fresh
    if since and as_utc(since) > start:
        start = as_utc(since)
    with SessionLocal() as s:
        return s.execute(
            select(SearchRun.id).where(SearchRun.watch_id == watch_id, SearchRun.kind == kind,
                                       SearchRun.status.in_(("ok", "partial")), SearchRun.started_at >= start)
            .limit(1)
        ).first() is not None


def startup_cycle(watches: list[Watch], collector: Collector, notifiers: list) -> list[str]:
    """Ciclo ao subir o contêiner: só nas rotas sem varredura recente, para um reinício não repetir buscas."""
    ran: list[str] = []
    for w in watches:
        if recent_run(w.id, "coarse", since=w.prices_since):
            log.info("%s: varredura recente, ciclo inicial pulado", w.id)
            continue
        try:
            run_watch(w, collector, notifiers, trigger="schedule")
        except Exception:  # uma rota com problema não impede as outras
            log.exception("%s: ciclo inicial interrompido", w.id)
        ran.append(w.id)
    return ran


def is_running(watch_id: str) -> bool:
    with SessionLocal() as s:
        run = s.execute(
            select(SearchRun).where(SearchRun.watch_id == watch_id, SearchRun.status == "running")
            .order_by(SearchRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()
    if not run:
        return False
    return utcnow() - as_utc(run.started_at) < STALE_RUNNING
