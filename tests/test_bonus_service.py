from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.bonus import service
from app.bonus.sources import FeedItem, StaticSource, parse_feed
from app.db import BonusCampaign, BonusPost, BonusSettings, SearchRun, SessionLocal, init_db


class Spy:
    name = "console"

    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))


@pytest.fixture(autouse=True)
def limpo():
    init_db()
    with SessionLocal() as s:
        for m in (BonusPost, BonusCampaign, BonusSettings):
            s.execute(delete(m))
        s.execute(delete(SearchRun).where(SearchRun.watch_id.like("bonus:%")))
        s.commit()
    yield


def fonte(*titulos, nome="Blog"):
    now = datetime.now(timezone.utc)
    return StaticSource(nome, [FeedItem(t, f"https://exemplo.invalid/{nome}/{i}-{abs(hash(t))}", now) for i, t in enumerate(titulos)])


def data_pt(d):
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
             "outubro", "novembro", "dezembro"]
    return f"{d.day} de {meses[d.month - 1]} de {d.year}"


def test_campanha_nova_aumento_e_sem_repeticao():
    spy = Spy()
    fim = data_pt(service.today() + timedelta(days=5))
    out = service.run_bonus([spy], [fonte(f"Azul Fidelidade oferece até 90% de bônus nas transferências da Livelo até {fim}")])
    assert [k for k, _ in out["alerts"]] == ["new"]
    out = service.run_bonus([spy], [fonte(f"Azul Fidelidade oferece até 90% de bônus nas transferências da Livelo até {fim}")])
    assert out["alerts"] == []  # mesmo post, nada novo
    out = service.run_bonus([spy], [fonte(f"Bônus subiu: Livelo para Azul agora com até 120% de bônus na transferência até {fim}", nome="Outro")])
    assert [k for k, _ in out["alerts"]] == ["increase"]
    with SessionLocal() as s:
        assert s.execute(select(BonusCampaign)).scalars().one().bonus_pct == 120
    assert "subiu" in spy.sent[-1][0]


def test_respeita_minimo_e_programas():
    with SessionLocal() as s:
        cfg = service.get_settings(s)
        cfg.min_pct, cfg.programs = 100, "Esfera"
        s.commit()
    out = service.run_bonus([Spy()], [fonte(
        "Azul oferece até 120% de bônus na transferência de pontos Livelo",   # programa não marcado
        "Azul oferece até 90% de bônus na transferência de pontos Esfera",    # abaixo do mínimo
    )])
    assert out["alerts"] == []
    out = service.run_bonus([Spy()], [fonte("Azul oferece até 110% de bônus na transferência de pontos Esfera", nome="B")])
    assert len(out["alerts"]) == 1


def test_lembrete_no_ultimo_dia_e_ja_transferi():
    spy = Spy()
    hoje = data_pt(service.today())
    amanha = service.today() + timedelta(days=1)
    service.run_bonus([spy], [fonte(f"Azul dá até 100% de bônus na transferência de pontos Esfera até {data_pt(amanha)}")])
    assert len(spy.sent) == 1
    with SessionLocal() as s:  # simula a virada do dia: a campanha termina hoje
        c = s.execute(select(BonusCampaign)).scalars().one()
        c.ends_on = service.today().isoformat()
        s.commit()
    out = service.run_bonus([spy], [fonte(f"Último dia: Esfera para Azul com 100% de bônus na transferência até {hoje}", nome="C")])
    assert [k for k, _ in out["alerts"]] == ["reminder"]
    assert "Último dia" in spy.sent[-1][0]
    assert service.run_bonus([spy], [])["alerts"] == []  # lembrete só uma vez


def test_ja_transferi_cancela_lembrete():
    spy = Spy()
    service.run_bonus([spy], [fonte(f"Azul dá até 100% de bônus na transferência de pontos Livelo até {data_pt(service.today())}")])
    assert len(spy.sent) == 1 and "termina hoje" in spy.sent[0][1]  # o próprio alerta avisa que é hoje
    with SessionLocal() as s:
        s.execute(select(BonusCampaign)).scalars().one().transferred = True
        s.commit()
    assert service.run_bonus([spy], [])["alerts"] == []


def test_feed_fora_do_ar_avisa_depois_de_duas_falhas():
    class Quebrada:
        name = "Blog quebrado"

        def fetch(self):
            raise RuntimeError("HTTP 403")

    spy = Spy()
    service.run_bonus([spy], [Quebrada()])
    assert spy.sent == []
    service.run_bonus([spy], [Quebrada()])
    assert len(spy.sent) == 1 and "Blog quebrado" in spy.sent[0][0]


def test_le_rss_do_wordpress():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Blog</title>
      <item><title>Azul Fidelidade oferece até 90% de bônus nas transferências de pontos da Livelo até 12 de agosto de 2026</title>
        <link>https://passageirodeprimeira.com/?p=557475</link>
        <pubDate>Mon, 03 Aug 2026 12:00:00 +0000</pubDate>
        <description><![CDATA[<p>A Azul Fidelidade está oferecendo bônus &amp; mais.</p>]]></description></item>
      <item><title>Outro post</title><link>https://x.invalid/2</link></item>
    </channel></rss>"""
    items = parse_feed(xml)
    assert len(items) == 2
    assert items[0].published.year == 2026 and "bônus & mais" in items[0].summary
