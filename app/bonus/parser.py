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
# "até quarta-feira (28)", "até 23h59 de sexta-feira (30)": só o dia, o mês vem da data do post
END_WEEKDAY = re.compile(r"\bate (?:as )?(?:\d{1,2}h\d{0,2} (?:de |do dia )?)?"
                         r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)? \((\d{1,2})\)")
# frase que tira programas da campanha ("Livelo e Esfera não estão presentes na campanha")
NEGATED = re.compile(r"\bnao (?:estao|esta|participa\w*|fazem? parte|entram?|inclu\w*|contempla\w*|valem?)\b"
                     r"|\bfic(?:a|am|ou|aram) de fora\b|\bfora d[ae] (?:campanha|promocao|oferta)\b")
EXCEPT = re.compile(r"\b(?:exceto|com excecao d[eoa]s?)\b")
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
    """Programas citados, sem os que o texto exclui (frase negativa inteira ou o que vem depois de "exceto")."""
    kept = []
    for sentence in re.split(r"[.;!?](?:\s|$)", t):
        if NEGATED.search(sentence):
            continue
        m = EXCEPT.search(sentence)
        kept.append(sentence[:m.start()] if m else sentence)
    text = " | ".join(kept)
    found = [name for name, pats in PROGRAMS.items() if any(re.search(p, text) for p in pats)]
    if not found and re.search(r"\bbancos?\b|\bcartoes de credito\b", text):
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
            m = END_WEEKDAY.search(t)
            if not m:
                return None
            day = int(m.group(1))
            month, year = ref.month, ref.year
            if day < ref.day:  # "até sexta-feira (2)" publicado no fim do mês
                month, year = (1, year + 1) if month == 12 else (month + 1, year)
            try:
                return date(year, month, day)
            except ValueError:
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
