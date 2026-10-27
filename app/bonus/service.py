"""Monitor de bônus de transferência para a Azul: lê as fontes, agrupa em campanhas e avisa."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import BonusCampaign, BonusPost, BonusSettings, SessionLocal, as_utc, utcnow
from ..notifiers import format_health_message, notify_all
from ..pipeline import health_event, tracked_run
from ..web import fmt
from .parser import parse
from .sources import Source, get_sources, slug

log = logging.getLogger(__name__)

SAME_CAMPAIGN_DAYS = 21  # posts do mesmo programa nesse intervalo, sem data conflitante, são a mesma campanha
OPEN_WITHOUT_END_DAYS = 7  # campanha sem data final conhecida conta como ativa por 7 dias após o último post
FEED_HINT = "Os bônus continuam sendo lidos das outras fontes. Se persistir, o endereço do feed pode ter mudado (BONUS_FEEDS)."


def today() -> date:
    return datetime.now(fmt.TZ).date()


def get_settings(s: Session) -> BonusSettings:
    cfg = s.get(BonusSettings, 1)
    if cfg is None:
        cfg = BonusSettings(id=1, active=True, programs="", min_pct=80, notify_email=True, notify_whatsapp=True)
        s.add(cfg)
        s.commit()
    return cfg


def is_active(c: BonusCampaign, ref: date | None = None) -> bool:
    ref = ref or today()
    if c.ends_on:
        return date.fromisoformat(c.ends_on) >= ref
    return (ref - fmt.local(c.last_seen_at).date()).days <= OPEN_WITHOUT_END_DAYS


def _find_campaign(s: Session, program: str, ends: date | None, seen: datetime) -> BonusCampaign | None:
    """Campanha do mesmo programa vista até 21 dias antes ou depois da data do post."""
    window = timedelta(days=SAME_CAMPAIGN_DAYS)
    cands = s.execute(
        select(BonusCampaign).where(BonusCampaign.program == program, BonusCampaign.last_seen_at >= seen - window,
                                    BonusCampaign.first_seen_at <= seen + window)
        .order_by(BonusCampaign.last_seen_at.desc())
    ).scalars().all()
    for c in cands:
        if ends and c.ends_on and abs((date.fromisoformat(c.ends_on) - ends).days) > 2:
            continue  # datas finais diferentes: outra campanha
        if c.ends_on and date.fromisoformat(c.ends_on) < fmt.local(seen).date() - timedelta(days=1):
            continue  # já tinha acabado quando o post saiu
        return c
    return None


def ingest(s: Session, source: Source, items) -> tuple[int, int]:
    """Grava posts novos reconhecidos e atualiza campanhas. Retorna (itens lidos, posts novos de bônus).

    A campanha é datada pelo post, não pela leitura: na primeira leitura o feed traz posts de
    semanas atrás, e eles não podem parecer campanhas de agora.
    """
    new = 0
    now = utcnow()
    for it in items:
        seen = min(as_utc(it.published), now) if it.published else now
        pub_local = fmt.local(seen).date()
        p = parse(it.title, it.summary, pub_local)
        if not p:
            continue
        for program in p.programs or [None]:
            exists = s.execute(select(BonusPost.id).where(BonusPost.url == it.link,
                                                          BonusPost.program.is_(None) if program is None
                                                          else BonusPost.program == program)).first()
            if exists:
                continue
            post = BonusPost(source=source.name, url=it.link[:512], title=it.title, published_at=it.published,
                             program=program, bonus_pct=p.bonus_pct,
                             ends_on=p.ends_on.isoformat() if p.ends_on else None, last_day=p.last_day)
            s.add(post)
            new += 1
            if program is None:
                continue
            c = _find_campaign(s, program, p.ends_on, seen)
            if c is None:
                c = BonusCampaign(program=program, bonus_pct=p.bonus_pct,
                                  ends_on=p.ends_on.isoformat() if p.ends_on else None,
                                  first_seen_at=seen, last_seen_at=seen)
                s.add(c)
                s.flush()
            else:
                if p.bonus_pct and (c.bonus_pct or 0) < p.bonus_pct:
                    c.bonus_pct = p.bonus_pct
                if p.ends_on and not c.ends_on:
                    c.ends_on = p.ends_on.isoformat()
                c.first_seen_at = min(as_utc(c.first_seen_at), seen)
                c.last_seen_at = max(as_utc(c.last_seen_at), seen)
            if p.notes and not c.note:
                c.note = p.notes[0]
            post.campaign_id = c.id
    s.commit()
    return len(items), new


def campaign_links(s: Session, c: BonusCampaign) -> list[BonusPost]:
    return list(s.execute(select(BonusPost).where(BonusPost.campaign_id == c.id)
                          .order_by(BonusPost.published_at.desc())).scalars())


def _message(s: Session, c: BonusCampaign, kind: str) -> tuple[str, str]:
    pct = f"até {c.bonus_pct}%"
    if c.ends_on:
        end = date.fromisoformat(c.ends_on)
        left = (end - today()).days
        prazo = "termina hoje" if left <= 0 else ("termina amanhã" if left == 1 else f"vai até {fmt.d_long(end)}")
    else:
        prazo = "prazo não informado nos posts"
    links = campaign_links(s, c)[:3]
    if kind == "reminder":
        subject = f"Último dia: {c.program} para Azul com {pct} de bônus"
        head = f"*Último dia para transferir {c.program} para a Azul com {pct} de bônus.*"
    elif kind == "increase":
        subject = f"Bônus subiu: {c.program} para Azul agora com {pct}"
        head = f"*O bônus de {c.program} para a Azul subiu para {pct}* ({prazo})."
    else:
        subject = f"{c.program} para Azul com {pct} de bônus"
        head = f"*Promoção de transferência: {c.program} para a Azul com {pct} de bônus* ({prazo})."
    body = head + "\n"
    if c.note:
        body += f"Atenção: {c.note}.\n"
    if links:
        body += "\nDetalhes e regras:\n" + "\n".join(f"{p.source}: {p.url}" for p in links)
    body += "\n\nO percentual máximo costuma exigir clube ou categoria; confira as faixas antes de transferir."
    return subject, body


def _channels(cfg: BonusSettings, notifiers: list) -> list:
    wanted = {"console": True, "email": cfg.notify_email, "whatsapp": cfg.notify_whatsapp}
    return [n for n in notifiers if wanted.get(n.name, False)]


def evaluate_alerts(s: Session, cfg: BonusSettings, notifiers: list) -> list[tuple[str, int]]:
    """Dispara alertas de campanha nova, de aumento e o lembrete do último dia. Retorna [(tipo, id)]."""
    sent = []
    chans = _channels(cfg, notifiers)
    open_ = s.execute(select(BonusCampaign).where(
        or_(BonusCampaign.ends_on.is_(None), BonusCampaign.ends_on >= today().isoformat()))).scalars().all()
    for c in open_:
        if not is_active(c) or not cfg.wants(c.program) or not c.bonus_pct or c.bonus_pct < cfg.min_pct:
            continue
        kind = None
        if c.alerted_pct is None:
            kind = "new"
        elif c.bonus_pct > c.alerted_pct:
            kind = "increase"
        elif (c.ends_on == today().isoformat() and not c.transferred and c.reminder_at is None):
            kind = "reminder"
        if not kind:
            continue
        subject, body = _message(s, c, kind)
        if not notify_all(chans, subject, body):
            continue  # nenhum canal entregou (ou todos desligados): tenta de novo na próxima leitura
        if kind == "reminder":
            c.reminder_at = utcnow()
        else:
            c.alerted_pct, c.alerted_at = c.bonus_pct, utcnow()
            if c.ends_on == today().isoformat():
                c.reminder_at = utcnow()  # o próprio alerta já diz que é o último dia
        sent.append((kind, c.id))
    s.commit()
    return sent


def run_bonus(notifiers: list, sources: list[Source] | None = None, trigger: str = "schedule") -> dict:
    sources = sources if sources is not None else get_sources()
    with SessionLocal() as s:
        cfg = get_settings(s)
        if not cfg.active:
            log.info("monitor de bônus desligado")
            return {"skipped": True}
    out = {"items": 0, "new_posts": 0, "alerts": []}
    for src in sources:
        key = f"bonus:{slug(src.name)}"
        try:
            with tracked_run(SimpleNamespace(id=key), "feed", trigger) as stats:
                try:
                    items = src.fetch()
                except Exception as e:
                    stats.fail(e)
                    log.warning("feed %s falhou: %s", src.name, e)
                    items = None
                if items is not None:
                    stats.ok([])
                    stats.results = len(items)
                    with SessionLocal() as s:
                        n, new = ingest(s, src, items)
                    out["items"] += n
                    out["new_posts"] += new
        except Exception:
            log.exception("feed %s: erro ao processar", src.name)
        event, run = health_event(key)
        if event:
            with SessionLocal() as s:
                chans = _channels(get_settings(s), notifiers)
            subject, body = format_health_message(f"bônus Azul ({src.name})", event, run,
                                                  FEED_HINT if event == "down" else "")
            notify_all(chans, subject, body)
    with SessionLocal() as s:
        out["alerts"] = evaluate_alerts(s, get_settings(s), notifiers)
    log.info("bônus: %d itens lidos, %d posts novos, %d alertas", out["items"], out["new_posts"], len(out["alerts"]))
    return out


def feed_keys() -> list[tuple[str, str]]:
    return [(src.name, f"bonus:{slug(src.name)}") for src in get_sources()]


def campaigns_for_panel(s: Session) -> dict:
    cfg = get_settings(s)
    rows = s.execute(select(BonusCampaign).order_by(BonusCampaign.last_seen_at.desc())).scalars().all()
    active = [c for c in rows if is_active(c)]
    active.sort(key=lambda c: (not (cfg.wants(c.program) and (c.bonus_pct or 0) >= cfg.min_pct),
                               -(c.bonus_pct or 0)))
    six_months = today() - timedelta(days=183)
    history: dict[str, dict] = {}
    for c in rows:
        if fmt.local(c.first_seen_at).date() < six_months:
            continue
        h = history.setdefault(c.program, {"program": c.program, "count": 0, "best": None, "best_at": None, "last": None})
        h["count"] += 1
        if c.bonus_pct and (h["best"] is None or c.bonus_pct > h["best"]):
            h["best"], h["best_at"] = c.bonus_pct, fmt.local(c.first_seen_at).date()
        seen = fmt.local(c.first_seen_at).date()
        if h["last"] is None or seen > h["last"]:
            h["last"] = seen
    return {"cfg": cfg, "active": active, "history": sorted(history.values(), key=lambda h: -(h["best"] or 0)),
            "links": {c.id: campaign_links(s, c) for c in active}}


def days_left(c: BonusCampaign) -> int | None:
    return (date.fromisoformat(c.ends_on) - today()).days if c.ends_on else None


__all__ = ["run_bonus", "campaigns_for_panel", "get_settings", "is_active", "days_left", "as_utc"]
