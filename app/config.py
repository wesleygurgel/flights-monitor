from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


@dataclass
class Watch:
    id: str
    origin: str
    destinations: list[str]
    depart_from: str
    depart_to: str
    duration_days: int = 7
    duration_tolerance: int = 0
    adults: int = 1
    max_stops: int | None = 1
    max_price: float | None = None
    drop_pct: float | None = None
    min_samples: int = 20
    candidates: int = 5
    currency: str = "BRL"
    active: bool = True
    notify_email: bool = True
    notify_whatsapp: bool = True
    prices_since: datetime | None = None  # parâmetros de preço mudaram aqui: histórico anterior não vale

    def search_window(self, today: date | None = None) -> tuple[str, str] | None:
        """Período de ida ainda buscável: de hoje (ou do início, se futuro) até o fim. None = já passou."""
        today = today or local_today()
        if date.fromisoformat(self.depart_to) < today:
            return None
        return max(date.fromisoformat(self.depart_from), today).isoformat(), self.depart_to

    @property
    def durations(self) -> list[int]:
        t = self.duration_tolerance
        return list(range(self.duration_days - t, self.duration_days + t + 1))

    @property
    def label(self) -> str:
        return f"{self.origin} → {'/'.join(self.destinations)}"


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: env("DATABASE_URL", "sqlite:///./monitor.db"))
    request_delay: float = field(default_factory=lambda: float(env("REQUEST_DELAY", "4")))
    # proxy (residencial) para as consultas ao Google, ex.: http://usuario:senha@host:porta
    proxy_url: str | None = field(default_factory=lambda: env("PROXY_URL") or None)
    # perfil do Chromium usado no calendário de preços (guarda os cookies do Google entre rodadas)
    browser_profile: Path = field(default_factory=lambda: Path(env("BROWSER_PROFILE", str(ROOT / ".browser-profile"))))
    coarse_cron: str = field(default_factory=lambda: env("COARSE_CRON", "0 6 * * *"))
    fine_cron: str = field(default_factory=lambda: env("FINE_CRON", "0 8,14,20 * * *"))
    bonus_cron: str = field(default_factory=lambda: env("BONUS_CRON", "20 7-23/2 * * *"))
    tz: str = field(default_factory=lambda: env("TZ", "America/Sao_Paulo"))
    watchlist_path: Path = field(default_factory=lambda: Path(env("WATCHLIST", str(ROOT / "watchlist.yaml"))))

    app_password: str | None = field(default_factory=lambda: env("APP_PASSWORD"))
    secret_key: str = field(default_factory=lambda: env("SECRET_KEY", "troque-esta-chave"))
    collector: str = field(default_factory=lambda: env("COLLECTOR", "google_flights"))
    # ligue quando o painel estiver atrás de HTTPS (Caddy, Cloudflare Tunnel, Tailscale Serve)
    https_only: bool = field(default_factory=lambda: (env("HTTPS_ONLY") or "").lower() in ("1", "true", "sim"))

    def load_yaml_watches(self) -> list[Watch]:
        """Lê o watchlist.yaml. Usado só para popular o banco na primeira execução."""
        if not self.watchlist_path.exists():
            return []
        data = yaml.safe_load(self.watchlist_path.read_text(encoding="utf-8")) or {}
        return [Watch(**w) for w in data.get("watches", [])]


settings = Settings()


def local_today() -> date:
    """Data de hoje no fuso configurado (TZ), não no do servidor."""
    return datetime.now(ZoneInfo(settings.tz)).date()
