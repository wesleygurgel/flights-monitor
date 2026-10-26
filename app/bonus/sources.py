"""Fontes de posts sobre bônus: feeds RSS de blogs de milhas (WordPress publica /feed/).

Para trocar ou adicionar fontes sem mexer no código, defina BONUS_FEEDS no .env:
  BONUS_FEEDS=Passageiro de Primeira|https://passageirodeprimeira.com/feed/,Outro|https://...
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from ..config import env, settings

DEFAULT_FEEDS = [
    ("Passageiro de Primeira", "https://passageirodeprimeira.com/feed/"),
    ("Pontos pra Voar", "https://pontospravoar.com/feed/"),
    ("Melhores Destinos", "https://www.melhoresdestinos.com.br/feed"),
    ("Melhores Cartões", "https://www.melhorescartoes.com.br/feed"),
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 flights-monitor")


@dataclass
class FeedItem:
    title: str
    link: str
    published: datetime | None
    summary: str = ""


class Source(Protocol):
    name: str

    def fetch(self) -> list[FeedItem]: ...


def slug(name: str) -> str:
    from .parser import norm
    return re.sub(r"[^a-z0-9]+", "-", norm(name)).strip("-")


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    raw = "".join(el.itertext()) if len(el) else (el.text or "")
    return html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()


def _date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)  # RSS: "Mon, 26 Oct 2026 14:00:00 +0000"
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str) -> list[FeedItem]:
    """RSS 2.0 ou Atom. Ignora itens sem título ou link."""
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    items: list[FeedItem] = []
    for it in root.iter("item"):
        items.append(FeedItem(_text(it.find("title")), _text(it.find("link")), _date(_text(it.find("pubDate"))),
                              _text(it.find("description"))[:800]))
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter(f"{atom}entry"):
        link = it.find(f"{atom}link")
        items.append(FeedItem(_text(it.find(f"{atom}title")), link.get("href", "") if link is not None else "",
                              _date(_text(it.find(f"{atom}updated")) or _text(it.find(f"{atom}published"))),
                              _text(it.find(f"{atom}summary"))[:800]))
    return [i for i in items if i.title and i.link]


class RssSource:
    def __init__(self, name: str, url: str):
        self.name, self.url = name, url

    def fetch(self) -> list[FeedItem]:
        r = httpx.get(self.url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        return parse_feed(r.text)


class StaticSource:
    """Fonte fixa para testes e demonstração."""

    def __init__(self, name: str, items: list[FeedItem]):
        self.name, self.items = name, items

    def fetch(self) -> list[FeedItem]:
        return list(self.items)


def mock_sources() -> list[Source]:
    now = datetime.now(timezone.utc)
    end = (now + timedelta(days=4)).date()
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
             "outubro", "novembro", "dezembro"]
    fim = f"{end.day} de {meses[end.month - 1]} de {end.year}"
    return [
        StaticSource("Blog simulado A", [
            FeedItem(f"Azul Fidelidade oferece até 100% de bônus nas transferências de pontos da Livelo até {fim}",
                     "https://exemplo.invalid/a/livelo-azul-100", now - timedelta(hours=3)),
            FeedItem("Smiles oferece até 80% de bônus na transferência de pontos Esfera",
                     "https://exemplo.invalid/a/smiles-esfera", now - timedelta(hours=5)),
        ]),
        StaticSource("Blog simulado B", [
            FeedItem(f"Transfira pontos da Livelo para a Azul com até 100% de bônus até {fim}",
                     "https://exemplo.invalid/b/livelo-azul", now - timedelta(hours=2)),
            FeedItem("Azul Fidelidade dá até 60% de bônus nas transferências de pontos do C6 Bank",
                     "https://exemplo.invalid/b/c6-azul-60", now - timedelta(hours=8)),
        ]),
    ]


def get_sources() -> list[Source]:
    if settings.collector == "mock":
        return mock_sources()
    custom = env("BONUS_FEEDS")
    pairs = [tuple(x.split("|", 1)) for x in custom.split(",") if "|" in x] if custom else DEFAULT_FEEDS
    return [RssSource(n.strip(), u.strip()) for n, u in pairs]
