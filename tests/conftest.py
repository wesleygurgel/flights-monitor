"""Ambiente comum dos testes: SQLite temporário, coletor e feeds simulados, nada de rede."""
import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["APP_PASSWORD"] = "segredo"
os.environ["SECRET_KEY"] = "chave-de-teste-com-mais-de-32-caracteres"
os.environ["COLLECTOR"] = "mock"
os.environ["WATCHLIST"] = "/nao/existe.yaml"

import pytest  # noqa: E402  (depois das variáveis de ambiente)
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from app import demo
    from app.db import init_db
    from app.web.app import app
    init_db()
    demo.run()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def logged(client):
    client.post("/entrar", data={"senha": "segredo"})
    yield client
    client.post("/sair")
