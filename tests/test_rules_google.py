"""Indicador "preço baixo" do Google: só reforça um alerta, não dispara sozinho (decisão do Wesley, 27/10/2026)."""
from app.collectors import Offer
from app.config import Watch
from app.db import SessionLocal, init_db
from app.rules import evaluate

W = Watch(id="regra-google", origin="NAT", destinations=["CGH"], depart_from="2030-03-01",
          depart_to="2030-04-30", max_price=900, drop_pct=20, min_samples=60)


def test_google_baixo_acima_do_teto_nao_avisa():
    init_db()
    with SessionLocal() as s:
        v = evaluate(s, W, "CGH", Offer("2030-03-16", "2030-03-24", 1114, "Gol", 1, "u", "low"))
    assert not v.alert


def test_google_baixo_entra_como_motivo_extra():
    init_db()
    with SessionLocal() as s:
        v = evaluate(s, W, "CGH", Offer("2030-03-17", "2030-03-24", 850, "Gol", 1, "u", "low"))
    assert v.alert
    assert v.reasons == ["abaixo do teto de R$ 900", "Google indica preço baixo"]
