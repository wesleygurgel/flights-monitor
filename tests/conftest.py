"""Ambiente comum dos testes: SQLite temporário, coletor e feeds simulados, nada de rede."""
import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["APP_PASSWORD"] = "segredo"
os.environ["SECRET_KEY"] = "chave-de-teste-com-mais-de-32-caracteres"
os.environ["COLLECTOR"] = "mock"
os.environ["WATCHLIST"] = "/nao/existe.yaml"
