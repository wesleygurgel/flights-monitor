from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .collectors import Offer
from .config import Watch, env

log = logging.getLogger(__name__)


def format_message(watch: Watch, destination: str, o: Offer, reasons: list[str]) -> tuple[str, str]:
    from .rules import join_reasons, money
    from .web.fmt import city, d_long

    price = money(o.price, watch.currency)
    subject = f"{watch.origin} para {destination} por {price}, ida {d_long(o.outbound_date)}"
    stops = "sem escalas" if o.stops == 0 else ("1 escala" if o.stops == 1 else f"{o.stops} escalas")
    body = (
        f"*{city(watch.origin)} para {city(destination)} ({destination}) por {price}*\n"
        f"Ida {d_long(o.outbound_date)}, volta {d_long(o.return_date)}\n"
        f"{o.airlines.split(',')[0]}, {stops}\n"
        f"Por que avisei: {join_reasons(reasons)}.\n\n"
        f"Comprar: {o.url}"
    )
    return subject, body


def format_health_message(watch: Watch, event: str, run) -> tuple[str, str]:
    """Aviso de coleta quebrada ("down") ou recuperada ("up")."""
    from .web.fmt import route_code, when

    code = route_code(watch.origin, watch.destinations)
    if event == "down":
        subject = f"Buscas de {code} pararam de trazer preços"
        why = run.error or "o Google não devolveu nenhum resultado"
        body = (
            f"*As últimas buscas de {code} não trouxeram preços.*\n"
            f"Última tentativa: {when(run.started_at)}. Motivo: {why}.\n\n"
            "Enquanto isso, nenhum alerta de preço sai para essa rota. Se o motivo for HTTP 429, "
            "o Google bloqueou o IP temporariamente: aumente REQUEST_DELAY ou reduza as datas checadas."
        )
    else:
        subject = f"Buscas de {code} voltaram a funcionar"
        body = f"*As buscas de {code} voltaram a trazer preços* ({when(run.started_at)}). Os alertas seguem normalmente."
    return subject, body


class EmailNotifier:
    name = "email"

    def enabled(self) -> bool:
        return bool(env("SMTP_HOST") and env("EMAIL_TO"))

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = env("SMTP_USER")
        msg["To"] = env("EMAIL_TO")
        msg.set_content(body.replace("*", ""))
        with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", "587"))) as smtp:
            smtp.starttls()
            smtp.login(env("SMTP_USER"), env("SMTP_PASS"))
            smtp.send_message(msg)


class WhatsAppNotifier:
    """Evolution API: POST /message/sendText/{instance}."""

    name = "whatsapp"

    def enabled(self) -> bool:
        return bool(env("EVOLUTION_URL") and env("EVOLUTION_APIKEY") and env("WHATSAPP_TO"))

    def send(self, subject: str, body: str) -> None:
        url = f"{env('EVOLUTION_URL').rstrip('/')}/message/sendText/{env('EVOLUTION_INSTANCE', 'monitor')}"
        r = httpx.post(
            url,
            headers={"apikey": env("EVOLUTION_APIKEY")},
            json={"number": env("WHATSAPP_TO"), "text": body},
            timeout=20,
        )
        r.raise_for_status()


class ConsoleNotifier:
    name = "console"

    def enabled(self) -> bool:
        return True

    def send(self, subject: str, body: str) -> None:
        print(f"\n=== ALERTA: {subject}\n{body}\n")


def active_notifiers(dry_run: bool = False) -> list:
    if dry_run:
        return [ConsoleNotifier()]
    ns = [n for n in (EmailNotifier(), WhatsAppNotifier()) if n.enabled()]
    return ns or [ConsoleNotifier()]


def notify_all(notifiers: list, subject: str, body: str) -> list[str]:
    """Envia por todos os canais; retorna os nomes dos que funcionaram."""
    ok: list[str] = []
    for n in notifiers:
        try:
            n.send(subject, body)
            ok.append(n.name)
            log.info("alerta enviado via %s", n.name)
        except Exception as e:  # um canal falhar não derruba os outros
            log.exception("falha ao enviar via %s: %s", n.name, e)
    return ok
