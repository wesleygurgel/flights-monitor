from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, select, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker

from .config import Watch, settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """SQLite devolve datetimes sem fuso; Postgres devolve com. Normaliza para UTC-aware."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class Route(Base):
    """Rota monitorada. Fonte da verdade da watchlist (o YAML só popula o banco na primeira vez)."""

    __tablename__ = "routes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(8))
    destinations: Mapped[str] = mapped_column(String(128))  # "GRU,CGH,VCP"
    depart_from: Mapped[str] = mapped_column(String(10))
    depart_to: Mapped[str] = mapped_column(String(10))
    duration_days: Mapped[int] = mapped_column(Integer, default=7)
    duration_tolerance: Mapped[int] = mapped_column(Integer, default=0)
    adults: Mapped[int] = mapped_column(Integer, default=1)
    max_stops: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    drop_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_samples: Mapped[int] = mapped_column(Integer, default=20)
    candidates: Mapped[int] = mapped_column(Integer, default=5)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_whatsapp: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # quando origem, destinos, datas, duração, adultos ou escalas mudaram: buscas anteriores não comparam
    prices_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def dest_list(self) -> list[str]:
        return [d for d in self.destinations.split(",") if d]

    def to_watch(self) -> Watch:
        return Watch(
            id=self.id, origin=self.origin, destinations=self.dest_list,
            depart_from=self.depart_from, depart_to=self.depart_to,
            duration_days=self.duration_days, duration_tolerance=self.duration_tolerance,
            adults=self.adults, max_stops=self.max_stops, max_price=self.max_price,
            drop_pct=self.drop_pct, min_samples=self.min_samples, candidates=self.candidates,
            currency=self.currency, active=self.active,
            notify_email=self.notify_email, notify_whatsapp=self.notify_whatsapp,
            prices_since=self.prices_since,
        )


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


class SearchRun(Base):
    """Uma rodada de busca (grossa ou fina) de uma rota: quando, quantas consultas, quantas falharam."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # coarse | fine
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule | manual | cli
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | partial | empty | failed
    queries: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[int] = mapped_column(Integer, default=0)
    best_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_bad(self) -> bool:
        return self.status in ("failed", "empty")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[str] = mapped_column(String(64), index=True)
    offer_key: Mapped[str] = mapped_column(String(64), index=True)  # origin|dest|out|ret
    price: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    channels: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "email,whatsapp"
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BonusPost(Base):
    """Post de blog lido de um feed. Guardamos todos os que o parser reconheceu como bônus para a Azul."""

    __tablename__ = "bonus_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(String(512), index=True)  # um post com 2 programas vira 2 linhas
    title: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    program: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bonus_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_day: Mapped[bool] = mapped_column(Boolean, default=False)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class BonusCampaign(Base):
    """Uma campanha de bônus (programa de origem -> Azul), juntando os posts de vários blogs."""

    __tablename__ = "bonus_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program: Mapped[str] = mapped_column(String(32), index=True)
    bonus_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    alerted_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transferred: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class BonusSettings(Base):
    """Preferências do monitor de bônus (uma linha só, id=1)."""

    __tablename__ = "bonus_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    programs: Mapped[str] = mapped_column(Text, default="")  # vazio = todos
    min_pct: Mapped[int] = mapped_column(Integer, default=80)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_whatsapp: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def program_list(self) -> list[str]:
        return [p for p in self.programs.split(",") if p]

    def wants(self, program: str) -> bool:
        return not self.program_list or program in self.program_list


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, future=True, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


MIGRATION_LOCK_KEY = 7_204_611  # chave arbitrária do pg_advisory_lock deste app


@contextmanager
def migration_lock(eng=None):
    """No Postgres, monitor e web sobem juntos: a trava faz um esperar o outro migrar e popular."""
    eng = eng or engine
    if eng.dialect.name != "postgresql":
        yield
        return
    conn = eng.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            conn.commit()
    finally:
        conn.close()


def migrate() -> None:
    """Aplica as migrações do Alembic (alembic upgrade head).

    Bancos criados antes do Alembic (sem a tabela alembic_version) recebem as tabelas que
    faltam e são marcados na migração inicial, para as próximas seguirem normalmente.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from .config import ROOT

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["configure_logger"] = False
    tables = inspect(engine).get_table_names()
    if "routes" in tables and "alembic_version" not in tables:
        Base.metadata.create_all(engine)  # cria o esquema atual inteiro...
        command.stamp(cfg, "head")         # ...então ele já está na última versão
    command.upgrade(cfg, "head")


def init_db() -> None:
    with migration_lock():
        migrate()
        seed_routes_from_yaml()


def seed_routes_from_yaml() -> int:
    """Na primeira execução (tabela vazia), importa as rotas do watchlist.yaml."""
    with SessionLocal() as s:
        if s.execute(select(func.count()).select_from(Route)).scalar_one():
            return 0
        watches = settings.load_yaml_watches()
        for w in watches:
            s.add(Route(
                id=w.id, origin=w.origin, destinations=",".join(w.destinations),
                depart_from=w.depart_from, depart_to=w.depart_to, duration_days=w.duration_days,
                duration_tolerance=w.duration_tolerance, adults=w.adults, max_stops=w.max_stops,
                max_price=w.max_price, drop_pct=w.drop_pct, min_samples=w.min_samples,
                candidates=w.candidates, currency=w.currency,
            ))
        s.commit()
        return len(watches)


def active_watches() -> list[Watch]:
    with SessionLocal() as s:
        rows = s.execute(select(Route).where(Route.active.is_(True)).order_by(Route.created_at)).scalars().all()
        return [r.to_watch() for r in rows]


def baseline(s: Session, watch_id: str, days: int = 30, since: datetime | None = None) -> tuple[float | None, int]:
    """Preço normal da rota: mediana do menor preço de cada busca fina dos últimos N dias.

    Uma amostra por busca (o melhor preço daquela rodada), não todas as ofertas: senão as
    tarifas caras de outras datas e companhias inflam o "normal" e geram alertas falsos.
    A busca em andamento não entra (o melhor preço dela só é gravado ao terminar).
    Buscas manuais ("Buscar agora") não entram: cliques seguidos puxariam o normal para um momento só.
    `since`: buscas antes da última mudança de parâmetros da rota não valem.
    Mediana em vez de média: um valor absurdo isolado não distorce a referência.
    """
    start = utcnow() - timedelta(days=days)
    if since and as_utc(since) > start:
        start = as_utc(since)
    rows = s.execute(
        select(SearchRun.best_price)
        .where(SearchRun.watch_id == watch_id, SearchRun.kind == "fine", SearchRun.trigger != "manual",
               SearchRun.best_price.is_not(None), SearchRun.started_at >= start)
        .order_by(SearchRun.best_price)
    ).scalars().all()
    n = len(rows)
    if not n:
        return None, 0
    med = rows[n // 2] if n % 2 else (rows[n // 2 - 1] + rows[n // 2]) / 2
    return med, n


def last_alert(s: Session, watch_id: str, offer_key: str, since: datetime | None = None) -> Alert | None:
    """Último alerta ENTREGUE dessa oferta (um que nenhum canal entregou não bloqueia o próximo)."""
    q = select(Alert).where(Alert.watch_id == watch_id, Alert.offer_key == offer_key,
                            Alert.channels.is_not(None), Alert.channels != "")
    if since:
        q = q.where(Alert.sent_at >= as_utc(since))
    return s.execute(
        q.order_by(Alert.sent_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def min_price_fine(s: Session, watch_id: str) -> float | None:
    return s.execute(
        select(func.min(PriceSnapshot.price)).where(
            PriceSnapshot.watch_id == watch_id, PriceSnapshot.source == "fine"
        )
    ).scalar_one_or_none()
