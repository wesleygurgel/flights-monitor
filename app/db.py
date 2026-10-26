from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """SQLite devolve datetimes sem fuso; Postgres devolve com. Normaliza para UTC-aware."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class PriceSnapshot(Base):
    """Um ponto de preço observado. Origem: 'coarse' (calendário) ou 'fine' (busca detalhada)."""

    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(16))  # coarse | fine
    origin: Mapped[str] = mapped_column(String(8))
    destination: Mapped[str] = mapped_column(String(8))
    outbound_date: Mapped[str] = mapped_column(String(10))
    return_date: Mapped[str] = mapped_column(String(10))
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    airlines: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_level: Mapped[str | None] = mapped_column(String(16), nullable=True)  # low|typical|high (Google)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[str] = mapped_column(String(64), index=True)
    offer_key: Mapped[str] = mapped_column(String(64), index=True)  # origin|dest|out|ret
    price: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def baseline(s: Session, watch_id: str, days: int = 30) -> tuple[float | None, int]:
    """Mediana dos preços da busca fina nos últimos N dias + nº de amostras.

    Mediana em vez de média: um único preço absurdo (alto ou baixo) não distorce a referência.
    """
    from datetime import timedelta

    since = utcnow() - timedelta(days=days)
    rows = s.execute(
        select(PriceSnapshot.price)
        .where(PriceSnapshot.watch_id == watch_id, PriceSnapshot.source == "fine",
               PriceSnapshot.observed_at >= since)
        .order_by(PriceSnapshot.price)
    ).scalars().all()
    n = len(rows)
    if not n:
        return None, 0
    med = rows[n // 2] if n % 2 else (rows[n // 2 - 1] + rows[n // 2]) / 2
    return med, n


def last_alert(s: Session, watch_id: str, offer_key: str) -> Alert | None:
    return s.execute(
        select(Alert)
        .where(Alert.watch_id == watch_id, Alert.offer_key == offer_key)
        .order_by(Alert.sent_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def min_price_fine(s: Session, watch_id: str) -> float | None:
    return s.execute(
        select(func.min(PriceSnapshot.price)).where(
            PriceSnapshot.watch_id == watch_id, PriceSnapshot.source == "fine"
        )
    ).scalar_one_or_none()
