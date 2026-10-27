"""Lê títulos de posts de blogs de milhas e extrai campanhas de bônus de transferência PARA a Azul.

Exemplo real:
  "Azul Fidelidade oferece até 90% de bônus nas transferências de pontos da Livelo até 12 de agosto de 2026"
  -> programas ["Livelo"], 90%, termina em 2026-08-12
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}

# nome exibido -> padrões (sobre o texto já sem acento e em minúsculas)
PROGRAMS: dict[str, list[str]] = {
    "Livelo": [r"\blivelo\b"],
    "Esfera": [r"\besfera\b"],
    "Itaú": [r"\bitau\b", r"\biupp\b", r"\bpersonnalite\b"],
    "C6 Bank": [r"\bc6\b", r"\batomos\b"],
    "BTG Pactual": [r"\bbtg\b"],
    "Inter": [r"\binter\b(?! ?nacional)(?!-)", r"\bbanco inter\b", r"\binter loop\b"],
    "Coopera": [r"\bcoopera\b"],
    "Banco do Brasil": [r"\bbanco do brasil\b"],
    "Bradesco": [r"\bbradesco\b"],
    "Santander": [r"\bsantander\b"],
    "Caixa": [r"\bcaixa\b"],
    "Nubank": [r"\bnubank\b"],
    "XP": [r"\bxp\b"],
    "Sicredi": [r"\bsicredi\b"],
    "Sicoob": [r"\bsicoob\b"],
    "Safra": [r"\bsafra\b"],
    "Porto Bank": [r"\bporto (bank|seguro)\b"],
    "Mercado Pago": [r"\bmercado pago\b"],
}
GENERIC_BANKS = "Bancos parceiros"
ALL_PROGRAMS = list(PROGRAMS) + [GENERIC_BANKS]

LAST_DAY = re.compile(r"\b(ultimo dia|ultimas horas|acaba hoje|termina hoje|terminam hoje|encerra hoje|so hoje)\b")
PCT = re.compile(r"(\d{2,3})\s*(?:%|por ?cento|porcento)")
END_TEXT = re.compile(r"\bate (?:o dia )?(\d{1,2})(?:o)? de (" + "|".join(MESES) + r")(?: de (\d{4}))?")
END_NUM = re.compile(r"\bate (?:o dia )?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")
# Azul como ORIGEM dos pontos ("pontos da Azul para ALL Accor") não interessa
AZUL_ORIGIN = re.compile(r"\b(?:da|do|de) (?:azul|tudoazul)(?: fidelidade)? (?:para|pro|ao|a) ")
OTHER_DEST = re.compile(r"\b(smiles|latam pass|latam|tap|all accor|accor|iberia|avios|aeroplan|flying blue)\b")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t)


@dataclass
class ParsedBonus:
    programs: list[str]
    bonus_pct: int | None
    ends_on: date | None
    last_day: bool = False
    notes: list[str] = field(default_factory=list)


def _programs(t: str) -> list[str]:
    found = [name for name, pats in PROGRAMS.items() if any(re.search(p, t) for p in pats)]
    if not found and re.search(r"\bbancos?\b", t):
        found = [GENERIC_BANKS]
    return found


def _end_date(t: str, published: date | None) -> date | None:
    ref = published or date.today()
    m = END_TEXT.search(t)
    if m:
        day, month, year = int(m.group(1)), MESES[m.group(2)], m.group(3)
    else:
        m = END_NUM.search(t)
        if not m:
            return None
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if year and len(year) == 2:
            year = "20" + year
    y = int(year) if year else ref.year
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    if not year and (ref - d).days > 60:  # "até 5 de janeiro" publicado em dezembro
        d = date(y + 1, month, day)
    return d


def parse(title: str, summary: str = "", published: date | None = None) -> ParsedBonus | None:
    """Devolve a campanha se o post for sobre bônus de transferência para a Azul; senão None."""
    t = norm(title)
    if not re.search(r"\b(azul|tudoazul)\b", t):
        return None
    if "bonus" not in t or not re.search(r"\btransf", t):
        return None
    if AZUL_ORIGIN.search(t):
        return None

    notes: list[str] = []
    # post sobre várias companhias ("LATAM Pass e Azul com até 120%"): o % pode ser da outra
    if OTHER_DEST.search(t):
        notes.append("post cita outra companhia; o percentual pode não ser da Azul")

    pcts = [int(p) for p in PCT.findall(t) if 10 <= int(p) <= 400]
    s = norm(summary)[:600]
    if not pcts and s:
        pcts = [int(p) for p in PCT.findall(s) if 10 <= int(p) <= 400]

    programs = _programs(t) or (_programs(s) if s else [])
    last_day = bool(LAST_DAY.search(t))
    ends = _end_date(t, published) or (_end_date(s, published) if s else None)
    if last_day and not ends:
        ends = published

    if not pcts and not programs:
        return None
    return ParsedBonus(programs=programs, bonus_pct=max(pcts) if pcts else None, ends_on=ends,
                       last_day=last_day, notes=notes)
