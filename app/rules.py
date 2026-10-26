"""Regras que decidem se uma oferta merece alerta."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from .collectors import Offer
from .config import Watch
from .db import as_utc, baseline, last_alert, utcnow

REALERT_AFTER = timedelta(hours=24)  # não repete o mesmo alerta antes disso...
REALERT_DROP_PCT = 5  # ...a menos que o preço caia mais que isso


def money(v: float, currency: str = "BRL") -> str:
    n = f"{round(v):,}".replace(",", ".")
    return f"R$ {n}" if currency == "BRL" else f"{currency} {n}"


def join_reasons(reasons: list[str]) -> str:
    return reasons[0] if len(reasons) == 1 else ", ".join(reasons[:-1]) + " e " + reasons[-1]


@dataclass
class Verdict:
    alert: bool
    reasons: list[str]


def offer_key(watch: Watch, destination: str, o: Offer) -> str:
    return f"{watch.origin}|{destination}|{o.outbound_date}|{o.return_date}"


def evaluate(s: Session, watch: Watch, destination: str, o: Offer) -> Verdict:
    """Fase 1 (histórico curto): vale o teto fixo `max_price`.
    Fase 2 (>= min_samples amostras): a queda relativa à mediana vira o critério principal;
    o teto fixo continua valendo como gatilho absoluto (preço abaixo dele é sempre bom)."""
    reasons: list[str] = []

    if watch.max_price is not None and o.price <= watch.max_price:
        reasons.append(f"abaixo do teto de {money(watch.max_price, watch.currency)}")

    med, n = baseline(s, watch.id, 30)
    if watch.drop_pct and med and n >= watch.min_samples:
        if o.price <= med * (1 - watch.drop_pct / 100):
            pct = (1 - o.price / med) * 100
            reasons.append(f"{pct:.0f}% abaixo do normal de {money(med, watch.currency)}")

    if o.price_level == "low":
        reasons.append("Google indica preço baixo")

    if not reasons:
        return Verdict(False, [])

    # dedup: mesma oferta alertada recentemente?
    prev = last_alert(s, watch.id, offer_key(watch, destination, o))
    if prev and utcnow() - as_utc(prev.sent_at) < REALERT_AFTER:
        if o.price > prev.price * (1 - REALERT_DROP_PCT / 100):
            return Verdict(False, ["já alertado nas últimas 24h"])
        reasons.append(f"caiu de {money(prev.price, watch.currency)}")

    return Verdict(True, reasons)
