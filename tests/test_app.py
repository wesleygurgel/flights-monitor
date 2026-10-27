import pytest

from app.collectors import Offer
from app.config import Watch
from app.db import Route, SessionLocal
from app.rules import evaluate

SAO = "nat-sao-mar-abr-2027"


def test_pede_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar"


def test_senha_errada(client):
    r = client.post("/entrar", data={"senha": "x"})
    assert r.status_code == 401 and "Senha incorreta" in r.text


def test_tela_rotas(logged):
    r = logged.get("/")
    assert r.status_code == 200
    assert "Melhor oportunidade agora" in r.text
    assert "NAT SAO" in r.text and "NAT LIS" in r.text and "Pausada" in r.text
    assert "6 de 20 buscas" in r.text


def test_tela_detalhe(logged):
    r = logged.get(f"/rotas/{SAO}")
    assert r.status_code == 200
    for trecho in ("Preço ao longo dos últimos 30 dias", "Março de 2027", "Abril de 2027",
                   "Melhores voos da última busca", "E-mail e WhatsApp"):
        assert trecho in r.text
    assert logged.get("/rotas/nao-existe").status_code == 404


def test_pausar_e_retomar(logged):
    r = logged.post(f"/rotas/{SAO}/pausar", headers={"HX-Request": "true"})
    assert "Retomar" in r.text
    with SessionLocal() as s:
        assert s.get(Route, SAO).active is False
    logged.post(f"/rotas/{SAO}/pausar", headers={"HX-Request": "true"})
    with SessionLocal() as s:
        assert s.get(Route, SAO).active is True


def test_criar_rota_valida(logged):
    r = logged.post("/gerenciar/salvar", data={
        "origin": "nat", "destinations": "rec, ssa", "depart_from": "2030-01-10", "depart_to": "2030-02-10",
        "duration_days": "5", "duration_tolerance": "1", "adults": "2", "max_stops": "0",
        "max_price": "1.200", "drop_pct": "", "min_samples": "60", "candidates": "3", "notify_email": "on",
    }, follow_redirects=False)
    assert r.status_code == 303 and "salvo=1" in r.headers["location"]
    rid = r.headers["location"].split("/")[2].split("?")[0]
    with SessionLocal() as s:
        route = s.get(Route, rid)
        assert route.destinations == "REC,SSA" and route.max_price == 1200 and route.drop_pct is None
        assert route.notify_email and not route.notify_whatsapp
    assert logged.post(f"/rotas/{rid}/excluir", follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.get(Route, rid) is None


def test_rota_invalida_mostra_erros(logged):
    r = logged.post("/gerenciar/salvar", data={
        "origin": "natal", "destinations": "", "depart_from": "2030-02-10", "depart_to": "2030-01-10",
        "duration_days": "5", "duration_tolerance": "1", "adults": "1", "max_stops": "1",
        "max_price": "", "drop_pct": "", "min_samples": "60", "candidates": "3",
    })
    assert r.status_code == 400
    assert "Corrija os campos marcados" in r.text
    assert "Escolha o aeroporto na lista" in r.text


def test_regra_fase_1_so_teto():
    w = Watch(id="regra-teste", origin="NAT", destinations=["GRU"], depart_from="2030-01-01",
              depart_to="2030-01-31", max_price=900, drop_pct=20, min_samples=60)
    with SessionLocal() as s:
        assert evaluate(s, w, "GRU", Offer("2030-01-10", "2030-01-17", 850, "LATAM", 0, "u")).alert
        assert not evaluate(s, w, "GRU", Offer("2030-01-10", "2030-01-17", 950, "LATAM", 0, "u")).alert


def test_preco_normal_usa_uma_amostra_por_busca():
    from datetime import timedelta
    from app.db import PriceSnapshot, SearchRun, baseline, utcnow
    wid = "baseline-teste"
    with SessionLocal() as s:
        for i, best in enumerate([1000, 1100, 1200]):
            at = utcnow() - timedelta(hours=i)
            s.add(SearchRun(watch_id=wid, kind="fine", status="ok", best_price=best, started_at=at))
            # ofertas caras da mesma busca não podem puxar o normal para cima
            for extra in (2500, 3000, 3500):
                s.add(PriceSnapshot(watch_id=wid, source="fine", origin="NAT", destination="GRU",
                                    outbound_date="2030-01-01", return_date="2030-01-08", price=extra,
                                    currency="BRL", observed_at=at))
        s.add(SearchRun(watch_id=wid, kind="fine", status="failed", best_price=None, started_at=utcnow()))
        s.commit()
        med, n = baseline(s, wid)
    assert (med, n) == (1100, 3)


def test_aviso_quando_coleta_quebra_e_volta():
    from app.db import SearchRun, utcnow
    from app.pipeline import check_health
    from datetime import timedelta

    class Spy:
        name = "console"
        def __init__(self):
            self.sent = []
        def send(self, subject, body):
            self.sent.append(subject)

    w = Watch(id="saude-teste", origin="NAT", destinations=["GRU"], depart_from="2030-01-01", depart_to="2030-01-31")
    spy = Spy()

    def add(status, minutes):
        with SessionLocal() as s:
            s.add(SearchRun(watch_id=w.id, kind="fine", status=status, error="HTTP 429" if status == "failed" else None,
                            started_at=utcnow() + timedelta(minutes=minutes)))
            s.commit()

    add("ok", 0)
    add("failed", 1)
    assert check_health(w, [spy]) is None          # uma falha só ainda não avisa
    add("failed", 2)
    assert check_health(w, [spy]) == "down"
    add("failed", 3)
    assert check_health(w, [spy]) is None          # não repete o aviso
    add("ok", 4)
    assert check_health(w, [spy]) == "up"
    assert len(spy.sent) == 2 and "pararam" in spy.sent[0] and "voltaram" in spy.sent[1]


def test_buscar_agora(logged):
    import time
    from sqlalchemy import select
    from app.db import SearchRun
    r = logged.post(f"/rotas/{SAO}/buscar", headers={"HX-Request": "true"})
    assert "Buscando preços" in r.text
    for _ in range(50):
        st = logged.get(f"/rotas/{SAO}/busca", headers={"HX-Request": "true"})
        if st.headers.get("HX-Refresh") == "true":
            break
        time.sleep(0.1)
    assert st.headers.get("HX-Refresh") == "true"
    with SessionLocal() as s:
        kinds = set(s.execute(select(SearchRun.kind).where(SearchRun.watch_id == SAO,
                                                           SearchRun.trigger == "manual")).scalars())
    assert kinds == {"coarse", "fine"}
    assert "Últimas buscas" in logged.get(f"/rotas/{SAO}").text


def test_recusa_secret_key_fraca():
    from app.web.app import secret_problem
    assert secret_problem("senha", "troque-esta-chave")
    assert secret_problem("senha", "curta")
    assert secret_problem("senha", "a" * 64) is None
    assert secret_problem(None, "troque-esta-chave") is None  # sem senha, sem cookie a proteger


def test_tela_bonus(logged):
    from sqlalchemy import select
    from app.db import BonusCampaign
    r = logged.get("/bonus")
    assert r.status_code == 200
    for trecho in ("Bônus de transferência para a Azul", "Campanhas ativas", "Últimos 6 meses por programa",
                   "Quando avisar", "Livelo", "Fontes"):
        assert trecho in r.text, trecho
    with SessionLocal() as s:
        c = s.execute(select(BonusCampaign).where(BonusCampaign.program == "Livelo",
                                                  BonusCampaign.bonus_pct == 100)).scalars().first()
    assert logged.post(f"/bonus/{c.id}/transferi", headers={"HX-Request": "true"}).headers.get("HX-Refresh") == "true"
    with SessionLocal() as s:
        assert s.get(BonusCampaign, c.id).transferred


def test_config_bonus(logged):
    from app.db import BonusSettings
    logged.post("/bonus/config", data={"min_pct": "100", "programs": ["Livelo", "Esfera"], "notify_email": "on"})
    with SessionLocal() as s:
        cfg = s.get(BonusSettings, 1)
        assert cfg.min_pct == 100 and cfg.program_list == ["Livelo", "Esfera"]
        assert cfg.notify_email and not cfg.notify_whatsapp and not cfg.active


# ---------- correções da revisão de 26/10/2026 ----------

def _form(**kw):
    from datetime import timedelta
    from app.config import local_today
    base = {"origin": "NAT", "destinations": "REC", "depart_from": (local_today() + timedelta(days=30)).isoformat(),
            "depart_to": (local_today() + timedelta(days=60)).isoformat(), "duration_days": "5",
            "duration_tolerance": "1", "adults": "1", "max_stops": "1", "max_price": "900", "drop_pct": "20",
            "min_samples": "20", "candidates": "3", "notify_email": "on"}
    base.update(kw)
    return base


def _criar(logged, **kw):
    r = logged.post("/gerenciar/salvar", data=_form(**kw), follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"].split("/")[2].split("?")[0]


def test_mudar_parametros_de_preco_reinicia_o_preco_normal(logged):
    from datetime import timedelta
    from app.db import SearchRun, baseline, utcnow
    rid = _criar(logged)
    with SessionLocal() as s:
        for p in (1000, 1100, 1200):
            s.add(SearchRun(watch_id=rid, kind="fine", status="ok", best_price=p,
                            started_at=utcnow() - timedelta(hours=1)))
        s.commit()
    _criar(logged, id=rid, max_price="950", notify_whatsapp="on")  # teto e canais não mudam o preço
    with SessionLocal() as s:
        r = s.get(Route, rid)
        assert r.prices_since is None and baseline(s, rid, since=r.prices_since)[1] == 3
    _criar(logged, id=rid, adults="2")  # 2 adultos: o preço dobra, o normal antigo não serve
    with SessionLocal() as s:
        r = s.get(Route, rid)
        assert r.prices_since is not None
        assert baseline(s, rid, since=r.prices_since) == (None, 0)
        assert r.to_watch().prices_since == r.prices_since
    logged.post(f"/rotas/{rid}/excluir")


def test_nova_rota_com_ida_no_passado_e_recusada(logged):
    from datetime import timedelta
    from app.config import local_today
    r = logged.post("/gerenciar/salvar", data=_form(depart_from=(local_today() - timedelta(days=3)).isoformat()))
    assert r.status_code == 400 and "já passou" in r.text


def test_editar_rota_em_andamento_aceita_inicio_no_passado(logged):
    from datetime import timedelta
    from app.config import local_today
    rid = _criar(logged)
    passado = (local_today() - timedelta(days=3)).isoformat()
    with SessionLocal() as s:  # a janela começou há 3 dias: a rota já está em andamento
        s.get(Route, rid).depart_from = passado
        s.commit()
    _criar(logged, id=rid, depart_from=passado, max_price="800")
    logged.post(f"/rotas/{rid}/excluir")


def test_excluir_rota_apaga_o_historico_de_buscas(logged):
    from sqlalchemy import select
    from app.db import SearchRun
    rid = _criar(logged)
    with SessionLocal() as s:
        s.add(SearchRun(watch_id=rid, kind="fine", status="ok"))
        s.commit()
    logged.post(f"/rotas/{rid}/excluir")
    with SessionLocal() as s:
        assert s.execute(select(SearchRun).where(SearchRun.watch_id == rid)).first() is None


@pytest.mark.parametrize("texto,esperado", [("1.200", 1200), ("1.200,50", 1200), ("20,5", 20), ("20.5", 20),
                                            ("900", 900), ("12.345.678", 12345678)])
def test_numeros_com_ponto_ou_virgula(texto, esperado):
    from app.web.app import _int
    errors = {}
    assert _int(texto, "x", errors, 0, 100_000_000) == esperado and not errors


def test_detalhe_nao_usa_jargao(logged):
    assert "ediana" not in logged.get(f"/rotas/{SAO}").text


def test_fase_1_nao_compara_com_o_normal():
    from app.web import queries
    with SessionLocal() as s:
        lis = queries.route_view(s, s.get(Route, "nat-lis-demo"))   # 6 buscas: ainda na fase 1
        sao = queries.route_view(s, s.get(Route, SAO))               # 30 dias: fase 2
    assert lis.phase == 1 and lis.ref and lis.vs_ref(4000) is None
    assert sao.phase == 2 and sao.vs_ref(sao.ref) == 0


def test_destaque_na_fase_1_usa_o_teto():
    from types import SimpleNamespace
    from app.web import queries
    v = SimpleNamespace(phase=1, ref=2000, best=SimpleNamespace(price=800), route=SimpleNamespace(max_price=900))
    assert queries.deal_score(v) == pytest.approx(800 / 900)
    v.phase = 2
    assert queries.deal_score(v) == pytest.approx(800 / 2000)


def test_busca_manual_reaproveita_varredura_recente(logged):
    import time
    from datetime import timedelta
    from sqlalchemy import select
    from app.db import SearchRun, utcnow
    rid = _criar(logged)
    with SessionLocal() as s:
        s.add(SearchRun(watch_id=rid, kind="coarse", status="ok", started_at=utcnow() - timedelta(hours=1)))
        s.commit()
    logged.post(f"/rotas/{rid}/buscar", headers={"HX-Request": "true"})
    for _ in range(50):
        if logged.get(f"/rotas/{rid}/busca", headers={"HX-Request": "true"}).headers.get("HX-Refresh") == "true":
            break
        time.sleep(0.1)
    with SessionLocal() as s:
        kinds = list(s.execute(select(SearchRun.kind).where(SearchRun.watch_id == rid,
                                                            SearchRun.trigger == "manual")).scalars())
    assert "coarse" not in kinds
    logged.post(f"/rotas/{rid}/excluir")
