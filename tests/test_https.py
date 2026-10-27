"""Cookie de sessão só por HTTPS quando o painel fica atrás de um proxy/túnel com HTTPS."""
from starlette.middleware.sessions import SessionMiddleware


def test_https_only_vem_do_env(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("HTTPS_ONLY", "true")
    assert Settings().https_only is True
    monkeypatch.setenv("HTTPS_ONLY", "0")
    assert Settings().https_only is False
    monkeypatch.delenv("HTTPS_ONLY")
    assert Settings().https_only is False


def test_cookie_de_sessao_segue_a_configuracao():
    from app.config import settings
    from app.web.app import app
    mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    assert mw.kwargs["https_only"] is settings.https_only
