"""Limpeza do histórico: varreduras antigas saem, o resto fica."""
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.db import Alert, PriceSnapshot, SearchRun, SessionLocal, init_db, prune_snapshots, utcnow

WID = "prune-teste"


def snap(source, days_ago):
    return PriceSnapshot(watch_id=WID, source=source, origin="NAT", destination="GRU", outbound_date="2027-03-10",
                         return_date="2027-03-17", price=1000, currency="BRL",
                         observed_at=utcnow() - timedelta(days=days_ago))


def count(s, source):
    return s.scalar(select(func.count()).select_from(PriceSnapshot)
                    .where(PriceSnapshot.watch_id == WID, PriceSnapshot.source == source))


def test_apaga_so_varredura_com_mais_de_90_dias():
    init_db()
    with SessionLocal() as s:
        s.execute(delete(PriceSnapshot).where(PriceSnapshot.watch_id == WID))
        s.add_all([snap("coarse", 120), snap("coarse", 91), snap("coarse", 89), snap("coarse", 1),
                   snap("fine", 200), snap("fine", 1)])
        old = utcnow() - timedelta(days=200)
        s.add(SearchRun(watch_id=WID, kind="coarse", trigger="schedule", status="ok", started_at=old))
        s.add(Alert(watch_id=WID, offer_key="k", price=900, reason="r", channels="email", sent_at=old))
        s.commit()

    assert prune_snapshots(days=90) >= 2

    with SessionLocal() as s:
        assert count(s, "coarse") == 2
        assert count(s, "fine") == 2  # busca detalhada é o histórico do preço normal e do gráfico
        assert s.scalar(select(func.count()).select_from(SearchRun).where(SearchRun.watch_id == WID)) == 1
        assert s.scalar(select(func.count()).select_from(Alert).where(Alert.watch_id == WID)) == 1


def test_comando_prune_existe():
    from app import main
    main.main(["prune"])
