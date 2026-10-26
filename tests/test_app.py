import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["APP_PASSWORD"] = "segredo"
os.environ["SECRET_KEY"] = "teste"
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
    assert "24 de 60 buscas" in r.text


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
