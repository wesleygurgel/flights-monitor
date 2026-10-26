"""Consultas que montam o que cada tela mostra."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Alert, PriceSnapshot, Route, as_utc, baseline, utcnow
from . import charts, fmt

BATCH_WINDOW = timedelta(hours=1)  # snapshots dentro de 1h da última leitura = mesma rodada


def latest_batch(s: Session, watch_id: str, source: str) -> list[PriceSnapshot]:
    latest = s.execute(
        select(PriceSnapshot.observed_at)
        .where(PriceSnapshot.watch_id == watch_id, PriceSnapshot.source == source)
        .order_by(PriceSnapshot.observed_at.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return []
    return list(s.execute(
        select(PriceSnapshot).where(
            PriceSnapshot.watch_id == watch_id, PriceSnapshot.source == source,
            PriceSnapshot.observed_at >= latest - BATCH_WINDOW,
        ).order_by(PriceSnapshot.price)
    ).scalars())


def daily_min(s: Session, watch_id: str, days: int = 30) -> list[tuple[date, float]]:
    since = utcnow() - timedelta(days=days)
    rows = s.execute(
        select(PriceSnapshot.observed_at, PriceSnapshot.price)
        .where(PriceSnapshot.watch_id == watch_id, PriceSnapshot.source == "fine",
               PriceSnapshot.observed_at >= since)
    ).all()
    best: dict[date, float] = {}
    for at, price in rows:
        d = fmt.local(at).date()
        best[d] = min(price, best.get(d, price))
    return sorted(best.items())


def alerts_for(s: Session, watch_id: str, limit: int = 20) -> list[Alert]:
    return list(s.execute(
        select(Alert).where(Alert.watch_id == watch_id).order_by(Alert.sent_at.desc()).limit(limit)
    ).scalars())


def channels_label(ch: str | None) -> str:
    names = {"email": "E-mail", "whatsapp": "WhatsApp", "console": "Console"}
    parts = [names.get(c, c) for c in (ch or "").split(",") if c]
    return " e ".join(parts) if parts else "Nenhum canal entregou"


def channels_inline(ch: str | None) -> str:
    """Versão para o meio da frase: 'e-mail e WhatsApp'."""
    return channels_label(ch).replace("E-mail", "e-mail").replace("Console", "console")


@dataclass
class RouteView:
    route: Route
    code: str
    dests: str
    window: str
    best: PriceSnapshot | None = None
    ref: float | None = None
    samples: int = 0
    phase: int = 1
    status: str = "monitoring"  # paused | below_ref | below_cap | monitoring | waiting
    pct_vs_ref: int | None = None
    spark: dict | None = None
    last_alert: Alert | None = None
    extra: dict = field(default_factory=dict)

    @property
    def is_bargain(self) -> bool:
        return self.status in ("below_ref", "below_cap")

    @property
    def status_label(self) -> str:
        return {"paused": "Pausada", "below_ref": "Abaixo do normal", "below_cap": "Abaixo do teto",
                "monitoring": "Monitorando", "waiting": "Aguardando busca"}[self.status]

    @property
    def status_sub(self) -> str:
        r = self.route
        if self.status == "paused":
            return f"desde {fmt.d_short(fmt.local(r.paused_at).date())}" if r.paused_at else "sem buscas"
        if self.status == "below_ref":
            return f"{-self.pct_vs_ref}% sob {fmt.brl(self.ref)}"
        if self.status == "below_cap":
            return f"teto {fmt.brl(r.max_price)}"
        if self.status == "waiting":
            return "primeira busca em breve"
        return "usando o normal" if self.phase == 2 else "usando o teto"

    @property
    def offer_line(self) -> str:
        b = self.best
        if not b:
            return "Ainda sem preços"
        parts = [fmt.range_short(b.outbound_date, b.return_date)]
        if b.airlines:
            parts.append(f"{b.airlines.split(',')[0]} {fmt.stops(b.stops)}".strip())
        return ", ".join(parts)

    @property
    def alert_line(self) -> str:
        if self.route.active is False:
            return "Pausada"
        return f"Alerta {fmt.when(self.last_alert.sent_at)}" if self.last_alert else "Nenhum alerta ainda"


def route_view(s: Session, r: Route) -> RouteView:
    v = RouteView(
        route=r, code=fmt.route_code(r.origin, r.dest_list), dests=fmt.dest_airports(r.dest_list),
        window=fmt.window(r.depart_from, r.depart_to, r.duration_days, r.duration_tolerance),
    )
    batch = latest_batch(s, r.id, "fine")
    v.best = batch[0] if batch else None
    v.ref, v.samples = baseline(s, r.id, 30)
    v.phase = 2 if (r.drop_pct and v.ref and v.samples >= r.min_samples) else 1
    alerts = alerts_for(s, r.id, 1)
    v.last_alert = alerts[0] if alerts else None
    series = daily_min(s, r.id)
    v.spark = charts.sparkline([p for _, p in series], r.max_price)

    price = v.best.price if v.best else None
    if price is not None and v.ref:
        v.pct_vs_ref = round((price / v.ref - 1) * 100)
    if not r.active:
        v.status = "paused"
    elif price is None:
        v.status = "waiting"
    elif v.phase == 2 and price <= v.ref * (1 - r.drop_pct / 100):
        v.status = "below_ref"
    elif r.max_price and price <= r.max_price:
        v.status = "below_cap"
    else:
        v.status = "monitoring"
    return v


def all_routes(s: Session) -> list[Route]:
    return list(s.execute(select(Route).order_by(Route.active.desc(), Route.created_at)).scalars())


def hero(views: list[RouteView]) -> dict:
    """A frase de abertura da tela de rotas: a melhor oportunidade agora, ou o que falta."""
    if not views:
        return {"kind": "empty"}
    deals = [v for v in views if v.is_bargain]
    if deals:
        # maior desconto relativo primeiro; sem referência, maior folga sob o teto
        def score(v: RouteView) -> float:
            if v.ref:
                return v.best.price / v.ref
            return v.best.price / (v.route.max_price or v.best.price)
        v = min(deals, key=score)
        b = v.best
        dest = fmt.dest_city([b.destination])
        title = f"{fmt.city(v.route.origin)} para {dest} por {fmt.brl(b.price)}"
        if v.pct_vs_ref is not None and v.pct_vs_ref < 0 and v.phase == 2:
            title += f", {-v.pct_vs_ref}% abaixo do normal para a rota."
        else:
            title += f", abaixo do seu teto de {fmt.brl(v.route.max_price)}."
        detail = f"Ida em {fmt.d_long(b.outbound_date)}, volta em {fmt.d_long(b.return_date)}"
        if b.airlines:
            detail += f", {b.airlines.split(',')[0]} {'sem escalas' if b.stops == 0 else fmt.stops(b.stops)}"
        detail += "."
        if v.last_alert:
            detail += f" Alerta enviado {fmt.when(v.last_alert.sent_at)} por {channels_inline(v.last_alert.channels)}."
        return {"kind": "deal", "title": title, "detail": detail, "url": b.url, "view": v}
    priced = [v for v in views if v.best and v.route.active]
    if priced:
        v = min(priced, key=lambda x: x.best.price)
        return {"kind": "none", "title": "Nenhuma rota abaixo do seu limite agora.",
                "detail": f"O menor preço encontrado foi {fmt.brl(v.best.price)} para {v.code}. "
                          f"Você recebe um alerta assim que algo cair abaixo do limite.", "view": v}
    return {"kind": "waiting", "title": "As buscas ainda não rodaram.",
            "detail": "Os primeiros preços aparecem aqui depois da próxima busca agendada."}


def detail(s: Session, r: Route) -> dict:
    v = route_view(s, r)
    series = daily_min(s, r.id)
    alerts = alerts_for(s, r.id)
    alert_pts = [(fmt.local(a.sent_at).date(), a.price) for a in alerts]
    chart = charts.history_chart(series, v.ref if v.phase == 2 else None, r.max_price, alert_pts)

    # calendário: última varredura grossa, duração alvo, menor preço por data de ida
    coarse = [c for c in latest_batch(s, r.id, "coarse")
              if (date.fromisoformat(c.return_date) - date.fromisoformat(c.outbound_date)).days == r.duration_days]
    by_day: dict[date, float] = {}
    for c in coarse:
        d = date.fromisoformat(c.outbound_date)
        by_day[d] = min(c.price, by_day.get(d, c.price))
    cal = charts.price_calendar(by_day, date.fromisoformat(r.depart_from), date.fromisoformat(r.depart_to), r.max_price)
    coarse_at = fmt.when(coarse[0].observed_at) if coarse else None

    # melhores voos: última busca fina, sem repetir mesma combinação data/destino/cia
    seen, offers = set(), []
    for o in latest_batch(s, r.id, "fine"):
        key = (o.outbound_date, o.return_date, o.destination, o.airlines)
        if key in seen:
            continue
        seen.add(key)
        offers.append(o)
        if len(offers) == 8:
            break

    return {"v": v, "chart": chart, "calendar": cal, "coarse_at": coarse_at,
            "offers": offers, "alerts": alerts, "n_samples": v.samples}


def next_run(cron: str, tz) -> datetime | None:
    try:
        from apscheduler.triggers.cron import CronTrigger
        return CronTrigger.from_crontab(cron, timezone=tz).get_next_fire_time(None, datetime.now(tz))
    except Exception:
        return None


def ensure_aware(dt: datetime | None) -> datetime | None:
    return as_utc(dt) if dt else None
