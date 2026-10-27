"""Pipeline de passagens: candidatas, alertas, preço normal, rotas vencidas e busca manual."""
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app import pipeline
from app.collectors import CoarsePrice, Offer
from app.config import Watch, local_today
from app.db import Alert, PriceSnapshot, Route, SearchRun, SessionLocal, baseline, init_db, utcnow

WID = "pipe-teste"


class Spy:
    name = "console"

    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))


class Broken:
    name = "email"

    def send(self, subject, body):
        raise RuntimeError("SMTP fora do ar")


class FakeCollector:
    """Coletor controlado: o teste diz o que cada consulta devolve."""

    name = "fake"

    def __init__(self, coarse=None, fine_price=850):
        self.coarse_map = coarse or {}
        self.fine_price = fine_price
        self.coarse_calls, self.fine_calls = [], []

    def coarse(self, watch, destination, duration):
        self.coarse_calls.append((destination, duration))
        return self.coarse_map.get((destination, duration), [])

    def fine(self, watch, destination, outbound, ret):
        self.fine_calls.append((destination, outbound, ret))
        price = self.fine_price(outbound) if callable(self.fine_price) else self.fine_price
        return [Offer(outbound, ret, price, "LATAM", 0, f"https://g.invalid/{destination}/{outbound}")]


def futuro(dias):
    return (local_today() + timedelta(days=dias)).isoformat()


def watch(**kw):
    base = dict(id=WID, origin="NAT", destinations=["GRU"], depart_from=futuro(30), depart_to=futuro(60),
                duration_days=7, duration_tolerance=0, max_price=900, drop_pct=20, min_samples=20, candidates=3)
    base.update(kw)
    return Watch(**base)


@pytest.fixture(autouse=True)
def limpo():
    init_db()
    with SessionLocal() as s:
        for m in (PriceSnapshot, Alert, SearchRun, Route):
            s.execute(delete(m).where((m.watch_id if m is not Route else m.id).like("pipe-%")))
        s.commit()
    yield


def add_route(w: Watch, **kw):
    with SessionLocal() as s:
        s.add(Route(id=w.id, origin=w.origin, destinations=",".join(w.destinations), depart_from=w.depart_from,
                    depart_to=w.depart_to, duration_days=w.duration_days, duration_tolerance=w.duration_tolerance,
                    max_price=w.max_price, drop_pct=w.drop_pct, active=True, **kw))
        s.commit()


# ---------- candidatas ----------

def test_candidatas_sao_datas_de_ida_diferentes():
    """Durações vizinhas da mesma data de ida não podem ocupar todas as vagas da busca detalhada."""
    d1, d2, d3 = futuro(31), futuro(40), futuro(50)
    found = [("GRU", CoarsePrice(d1, futuro(38), 700)), ("GRU", CoarsePrice(d1, futuro(39), 710)),
             ("CGH", CoarsePrice(d1, futuro(38), 720)), ("GRU", CoarsePrice(d2, futuro(47), 800)),
             ("GRU", CoarsePrice(d3, futuro(57), 900))]
    picked = pipeline.pick_candidates(found, 3)
    assert [c.outbound_date for _, c in picked] == [d1, d2, d3]
    assert picked[0][1].price == 700  # fica a opção mais barata daquela data


def test_varredura_ignora_datas_que_ja_passaram():
    w = watch(depart_from=futuro(-5), depart_to=futuro(20))
    col = FakeCollector(coarse={("GRU", 7): [CoarsePrice(futuro(-2), futuro(5), 100),
                                            CoarsePrice(futuro(3), futuro(10), 800)]})
    cands = pipeline.run_coarse(w, col)
    assert [c.outbound_date for _, c in cands] == [futuro(3)]


def test_janela_de_busca_comeca_hoje():
    w = watch(depart_from=futuro(-5), depart_to=futuro(20))
    assert w.search_window() == (local_today().isoformat(), futuro(20))
    assert watch(depart_from=futuro(-20), depart_to=futuro(-1)).search_window() is None


def test_candidatas_antigas_nao_trazem_datas_passadas():
    w = watch()
    with SessionLocal() as s:
        for out, price in ((futuro(-1), 500), (futuro(35), 800)):
            s.add(PriceSnapshot(watch_id=w.id, source="coarse", origin="NAT", destination="GRU", outbound_date=out,
                                return_date=futuro(42), price=price, currency="BRL"))
        s.commit()
    assert [c.outbound_date for _, c in pipeline.last_candidates(w, 5)] == [futuro(35)]


# ---------- rota vencida ----------

def test_rota_vencida_e_pausada_e_avisa_uma_vez():
    w = watch(depart_from=futuro(-30), depart_to=futuro(-1))
    add_route(w)
    spy, col = Spy(), FakeCollector()
    out = pipeline.run_watch(w, col, [spy])
    assert out.get("expired") and col.coarse_calls == [] and col.fine_calls == []
    with SessionLocal() as s:
        r = s.get(Route, w.id)
        assert r.active is False and r.paused_at is not None
    assert len(spy.sent) == 1 and "período de ida" in spy.sent[0][1]


# ---------- alertas ----------

def test_alertas_da_rodada_vao_numa_mensagem_so():
    w = watch()
    cands = [("GRU", CoarsePrice(futuro(31 + i), futuro(38 + i), 800)) for i in range(3)]
    spy = Spy()
    sent = pipeline.run_fine(w, FakeCollector(fine_price=850), cands, [spy])
    assert sent == 3
    assert len(spy.sent) == 1
    subject, body = spy.sent[0]
    assert "+2 datas" in subject
    for _, c in cands:
        assert f"https://g.invalid/GRU/{c.outbound_date}" in body
    with SessionLocal() as s:
        assert len(s.execute(select(Alert).where(Alert.watch_id == w.id)).scalars().all()) == 3


def test_alerta_nao_entregue_nao_bloqueia_o_proximo():
    w = watch()
    cands = [("GRU", CoarsePrice(futuro(31), futuro(38), 800))]
    assert pipeline.run_fine(w, FakeCollector(), cands, [Broken()]) == 1
    with SessionLocal() as s:
        a = s.execute(select(Alert).where(Alert.watch_id == w.id)).scalars().one()
        assert a.channels == ""  # o painel mostra "Nenhum canal entregou"
    spy = Spy()
    assert pipeline.run_fine(w, FakeCollector(), cands, [spy]) == 1  # tenta de novo na rodada seguinte
    assert len(spy.sent) == 1


def test_alerta_entregue_continua_com_dedup_de_24h():
    w = watch()
    cands = [("GRU", CoarsePrice(futuro(31), futuro(38), 800))]
    spy = Spy()
    pipeline.run_fine(w, FakeCollector(), cands, [spy])
    assert pipeline.run_fine(w, FakeCollector(), cands, [spy]) == 0
    assert len(spy.sent) == 1


def test_rota_sem_canais_nao_registra_alerta():
    w = watch(notify_email=False, notify_whatsapp=False)
    cands = [("GRU", CoarsePrice(futuro(31), futuro(38), 800))]
    email = Broken()
    assert pipeline.run_fine(w, FakeCollector(), cands, [email]) == 0
    with SessionLocal() as s:
        assert s.execute(select(Alert).where(Alert.watch_id == w.id)).first() is None


# ---------- preço normal ----------

def add_run(wid, best, trigger="schedule", hours_ago=1, kind="fine", status="ok"):
    with SessionLocal() as s:
        s.add(SearchRun(watch_id=wid, kind=kind, trigger=trigger, status=status, best_price=best,
                        started_at=utcnow() - timedelta(hours=hours_ago)))
        s.commit()


def test_busca_manual_nao_entra_no_preco_normal():
    for p in (1000, 1100, 1200):
        add_run(WID, p)
    for _ in range(5):
        add_run(WID, 500, trigger="manual")
    with SessionLocal() as s:
        assert baseline(s, WID) == (1100, 3)


def test_preco_normal_ignora_buscas_antes_da_mudanca_da_rota():
    add_run(WID, 1000, hours_ago=10)
    add_run(WID, 1100, hours_ago=9)
    add_run(WID, 2000, hours_ago=1)
    with SessionLocal() as s:
        assert baseline(s, WID, since=utcnow() - timedelta(hours=5)) == (2000, 1)


# ---------- busca manual e reinício ----------

def test_varredura_recente_e_reaproveitada():
    add_run(WID, None, kind="coarse", hours_ago=3)
    assert pipeline.recent_run(WID, "coarse")
    assert not pipeline.recent_run(WID, "coarse", since=utcnow() - timedelta(hours=1))  # rota mudou depois
    add_run("pipe-velha", None, kind="coarse", hours_ago=20)
    assert not pipeline.recent_run("pipe-velha", "coarse")
    add_run("pipe-falhou", None, kind="coarse", hours_ago=1, status="failed")
    assert not pipeline.recent_run("pipe-falhou", "coarse")


def test_ciclo_ao_iniciar_pula_rotas_com_busca_recente():
    fresca, velha = watch(id="pipe-fresca"), watch(id="pipe-velha2")
    add_run(fresca.id, None, kind="coarse", hours_ago=2)
    col = FakeCollector(coarse={("GRU", 7): [CoarsePrice(futuro(31), futuro(38), 1500)]}, fine_price=1500)
    ran = pipeline.startup_cycle([fresca, velha], col, [Spy()])
    assert ran == [velha.id]


def test_data_de_hoje_usa_o_fuso_configurado():
    assert isinstance(local_today(), date)


def test_assunto_no_singular_com_uma_data_extra():
    from app.notifiers import format_message
    w = watch()
    deals = [("GRU", Offer(futuro(31 + i), futuro(38 + i), 800 + i, "LATAM", 0, "u"), ["abaixo do teto"])
             for i in range(2)]
    subject, _ = format_message(w, deals)
    assert subject.endswith("(+1 data)")
