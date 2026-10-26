"""Orquestra: varredura grossa -> candidatas -> busca fina -> regras -> alerta."""
from __future__ import annotations

import logging

from sqlalchemy import select

from .collectors import Collector, CoarsePrice
from .config import Watch
from .db import Alert, PriceSnapshot, SessionLocal
from .notifiers import format_message, notify_all
from .rules import evaluate, offer_key

log = logging.getLogger(__name__)


def run_coarse(watch: Watch, collector: Collector) -> list[tuple[str, CoarsePrice]]:
    """Varre o calendário para cada destino x duração. Retorna as N (destino, data) mais baratas."""
    found: list[tuple[str, CoarsePrice]] = []
    with SessionLocal() as s:
        for dest in watch.destinations:
            for dur in watch.durations:
                try:
                    entries = collector.coarse(watch, dest, dur)
                except Exception as e:
                    log.exception("coarse %s %s dur=%s falhou: %s", watch.id, dest, dur, e)
                    continue
                for e in entries:
                    found.append((dest, e))
                    s.add(PriceSnapshot(
                        watch_id=watch.id, source="coarse", origin=watch.origin, destination=dest,
                        outbound_date=e.outbound_date, return_date=e.return_date,
                        price=e.price, currency=watch.currency,
                    ))
                log.info("coarse %s %s dur=%s: %d datas, min=%s", watch.id, dest, dur, len(entries),
                         min((x.price for x in entries), default=None))
        s.commit()
    found.sort(key=lambda t: t[1].price)
    return found[: watch.candidates]


def run_fine(watch: Watch, collector: Collector, candidates: list[tuple[str, CoarsePrice]], notifiers: list) -> int:
    """Busca detalhada nas candidatas, grava, avalia regras e dispara alertas. Retorna nº de alertas."""
    sent = 0
    with SessionLocal() as s:
        for dest, c in candidates:
            try:
                offers = collector.fine(watch, dest, c.outbound_date, c.return_date)
            except Exception as e:
                log.exception("fine %s %s %s falhou: %s", watch.id, dest, c.outbound_date, e)
                continue
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
                notify_all(notifiers, subject, body)
                s.add(Alert(watch_id=watch.id, offer_key=offer_key(watch, dest, best), price=best.price,
                            reason="; ".join(v.reasons)))
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
        from datetime import timedelta
        since = latest - timedelta(hours=1)  # mesma rodada de varredura
        rows = s.execute(
            select(PriceSnapshot).where(
                PriceSnapshot.watch_id == watch.id, PriceSnapshot.source == "coarse",
                PriceSnapshot.observed_at >= since,
            ).order_by(PriceSnapshot.price).limit(limit)
        ).scalars().all()
    return [(r.destination, CoarsePrice(r.outbound_date, r.return_date, r.price)) for r in rows]
