"""Configuração de infraestrutura: docker compose, migrações concorrentes e envio de e-mail."""
import yaml

from app.config import ROOT


def compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_banco_da_evolution_e_criado_pelo_postgres():
    svc = compose()
    uri = svc["evolution"]["environment"]["DATABASE_CONNECTION_URI"]
    dbname = uri.rsplit("/", 1)[1].split("?")[0]
    mounts = [v.split(":") for v in svc["db"]["volumes"]]
    init = [host for host, cont, *_ in mounts if cont == "/docker-entrypoint-initdb.d"]
    assert init, "o Postgres precisa de um script de inicialização"
    sql = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / init[0]).glob("*.sql")).lower()
    assert f"create database {dbname}" in sql


def test_evolution_so_escuta_na_maquina_local():
    for port in compose()["evolution"]["ports"]:
        assert str(port).startswith("127.0.0.1:")


class FakeConn:
    def __init__(self, log):
        self.log = log

    def execute(self, stmt, params=None):
        self.log.append(str(stmt))

    def commit(self):
        pass

    def close(self):
        self.log.append("close")


class FakeEngine:
    def __init__(self, dialect):
        self.dialect = type("D", (), {"name": dialect})()
        self.log = []

    def connect(self):
        return FakeConn(self.log)


def test_migracao_usa_trava_no_postgres():
    """monitor e web sobem juntos: só um pode migrar por vez."""
    from app.db import migration_lock
    eng = FakeEngine("postgresql")
    with migration_lock(eng):
        eng.log.append("migrando")
    assert "pg_advisory_lock" in eng.log[0] and eng.log[1] == "migrando"
    assert "pg_advisory_unlock" in eng.log[2] and eng.log[-1] == "close"


def test_migracao_sem_trava_no_sqlite():
    from app.db import migration_lock
    eng = FakeEngine("sqlite")
    with migration_lock(eng):
        pass
    assert eng.log == []


def test_init_db_pode_rodar_duas_vezes():
    from app.db import init_db
    init_db()
    init_db()


class FakeSMTP:
    used = []

    def __init__(self, host, port):
        FakeSMTP.used.append((type(self).__name__, port))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def starttls(self):
        FakeSMTP.used.append("starttls")

    def login(self, u, p):
        pass

    def send_message(self, m):
        pass


class FakeSMTPSSL(FakeSMTP):
    pass


def test_email_porta_465_usa_ssl_direto(monkeypatch):
    from app import notifiers
    monkeypatch.setattr(notifiers.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notifiers.smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    monkeypatch.setenv("SMTP_USER", "a@b.invalid")
    monkeypatch.setenv("EMAIL_TO", "a@b.invalid")
    FakeSMTP.used = []
    monkeypatch.setenv("SMTP_PORT", "465")
    notifiers.EmailNotifier().send("s", "b")
    assert FakeSMTP.used == [("FakeSMTPSSL", 465)]
    FakeSMTP.used = []
    monkeypatch.setenv("SMTP_PORT", "587")
    notifiers.EmailNotifier().send("s", "b")
    assert FakeSMTP.used == [("FakeSMTP", 587), "starttls"]


def test_evolution_com_versao_fixa():
    """atendai/evolution-api saiu do Docker Hub; a imagem oficial agora é evoapicloud/evolution-api."""
    image = compose()["evolution"]["image"]
    name, _, tag = image.partition(":")
    assert name == "evoapicloud/evolution-api"
    assert tag and tag != "latest"


def test_cada_servico_tem_seu_perfil_do_navegador():
    """O Chromium trava o perfil em uso: monitor e web não podem dividir o mesmo."""
    svc = compose()
    profiles = {}
    for name in ("monitor", "web"):
        path = svc[name]["environment"]["BROWSER_PROFILE"]
        mounts = {v.split(":")[1]: v.split(":")[0] for v in svc[name]["volumes"]}
        assert any(path.startswith(cont) for cont in mounts), f"{name}: perfil fora de um volume"
        profiles[name] = path
    assert profiles["monitor"] != profiles["web"]


def test_imagem_instala_o_chromium():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "playwright install" in dockerfile and "chromium" in dockerfile
    assert "playwright" in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_backup_espera_as_migracoes():
    """Na primeira subida o backup rodava antes de as tabelas existirem e gravava um arquivo vazio."""
    script = "\n".join(compose()["backup"]["command"])
    assert script.index("alembic_version") < script.index("pg_dump")
