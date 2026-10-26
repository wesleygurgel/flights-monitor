import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["APP_PASSWORD"] = "segredo"
os.environ["SECRET_KEY"] = "chave-de-teste-com-mais-de-32-caracteres"
os.environ["COLLECTOR"] = "mock"
os.environ["WATCHLIST"] = "/nao/existe.yaml"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import demo  # noqa: E402
from app.collectors import Offer  # noqa: E402
from app.config import Watch  # noqa: E402
from app.db import Route, SessionLocal, init_db  # noqa: E402
from app.rules import evaluate  # noqa: E402
from app.web.app import app  # noqa: E402

SAO = "nat-sao-mar-abr-2027"


@pytest.fixture(scope="module")
def client():
    init_db()
    demo.run()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def logged(client):
    client.post("/entrar", data={"senha": "segredo"})
    yield client
    client.post("/sair")


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
    assert "código de 3 letras" in r.text


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
