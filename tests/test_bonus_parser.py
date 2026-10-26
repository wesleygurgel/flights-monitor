"""Títulos reais de blogs de milhas (2025-2026) usados como casos do parser."""
from datetime import date

import pytest

from app.bonus.parser import GENERIC_BANKS, parse

PUB = date(2026, 8, 1)

CASOS = [
    ("Azul Fidelidade oferece até 90% de bônus nas transferências de pontos da Livelo até 12 de agosto de 2026",
     ["Livelo"], 90, date(2026, 8, 12), False),
    ("Azul Fidelidade oferece até 85% de bônus nas transferências de pontos Esfera até 26 de julho de 2026",
     ["Esfera"], 85, date(2026, 7, 26), False),
    ("Azul Fidelidade oferece até 80% de bônus nas transferências de pontos Esfera até 24 de maio de 2026",
     ["Esfera"], 80, date(2026, 5, 24), False),
    ("Azul Fidelidade oferece até 90% de bônus nas transferências de pontos do Inter, C6 Bank e Coopera",
     ["C6 Bank", "Inter", "Coopera"], 90, None, False),
    ("Transfira pontos Esfera para Azul Fidelidade com até 120% de bônus",
     ["Esfera"], 120, None, False),
    ("Azul Fidelidade e Livelo: Transfira pontos da Livelo para o Azul Fidelidade com até 100% de bônus",
     ["Livelo"], 100, None, False),
    ("Acaba hoje: Azul Fidelidade e Livelo oferecem até 120% de bônus na transferência de pontos",
     ["Livelo"], 120, PUB, True),
    ("Último dia: bônus de 120% na transferência de pontos Esfera para a Azul",
     ["Esfera"], 120, PUB, True),
    ("Azul Fidelidade: até 120% de bônus na transferência de pontos de bancos parceiros",
     [GENERIC_BANKS], 120, None, False),
    ("Oferta: transfira pontos do Itaú para a Azul com até 110% de bônus até 31/08",
     ["Itaú"], 110, date(2026, 8, 31), False),
]


@pytest.mark.parametrize("title,programs,pct,ends,last_day", CASOS)
def test_titulos_reais(title, programs, pct, ends, last_day):
    p = parse(title, published=PUB)
    assert p is not None, title
    assert sorted(p.programs) == sorted(programs)
    assert p.bonus_pct == pct
    assert p.ends_on == ends
    assert p.last_day is last_day


@pytest.mark.parametrize("title", [
    "Passageiro de Primeira celebra 15 anos com campanha de transferência de pontos da Azul para ALL Accor",
    "Smiles oferece até 100% de bônus na transferência de pontos da Livelo",
    "LATAM Pass dá até 25% de bônus nas transferências do Itaú",
    "Cadastre-se no Azul Fidelidade e ganhe até 7.000 pontos grátis",
    "Azul anuncia nova rota internacional para Lisboa",
    "Voos da Azul para o Nordeste a partir de R$ 299",
])
def test_ignora_o_que_nao_e_bonus_para_azul(title):
    assert parse(title, published=PUB) is None


def test_internacional_nao_vira_inter():
    p = parse("Azul Fidelidade dá 100% de bônus na transferência de pontos Livelo para emissões internacionais",
              published=PUB)
    assert p.programs == ["Livelo"]


def test_post_com_outra_companhia_fica_marcado():
    p = parse("Ofertas LATAM e Azul com até 120% de bônus na transferência terminam hoje", published=PUB)
    assert p is not None and p.notes and p.last_day


def test_ano_vira_quando_data_ja_passou():
    p = parse("Azul dá até 100% de bônus nas transferências Livelo até 5 de janeiro", published=date(2026, 12, 20))
    assert p.ends_on == date(2027, 1, 5)


def test_usa_resumo_quando_titulo_nao_tem_programa():
    p = parse("Azul Fidelidade lança promoção com até 100% de bônus na transferência de pontos",
              summary="A campanha vale para pontos da Esfera até 30 de setembro de 2026.", published=PUB)
    assert p.programs == ["Esfera"] and p.ends_on == date(2026, 9, 30)
