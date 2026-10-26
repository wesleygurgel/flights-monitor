from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .collectors import Offer
from .config import Watch, env

log = logging.getLogger(__name__)


def format_message(watch: Watch, destination: str, o: Offer, reasons: list[str]) -> tuple[str, str]:
    subject = f"✈️ {watch.origin}→{destination} por {watch.currency} {o.price:.0f} ({o.outbound_date} a {o.return_date})"
    stops = "direto" if o.stops == 0 else f"{o.stops} parada(s)"
    body = (
        f"*{watch.origin} → {destination}* — *{watch.currency} {o.price:.0f}*\n"
        f"Ida {o.outbound_date} · Volta {o.return_date}\n"
        f"{o.airlines} · {stops}\n"
        f"Motivo: {'; '.join(reasons)}\n\n"
        f"Comprar: {o.url}"
    )
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


def notify_all(notifiers: list, subject: str, body: str) -> None:
    for n in notifiers:
        try:
            n.send(subject, body)
            log.info("alerta enviado via %s", n.name)
        except Exception as e:  # um canal falhar não derruba os outros
            log.exception("falha ao enviar via %s: %s", n.name, e)
