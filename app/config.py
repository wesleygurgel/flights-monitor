from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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
    min_samples: int = 60
    candidates: int = 5
    currency: str = "BRL"
    active: bool = True
    notify_email: bool = True
    notify_whatsapp: bool = True

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
    coarse_cron: str = field(default_factory=lambda: env("COARSE_CRON", "0 6 * * *"))
    fine_cron: str = field(default_factory=lambda: env("FINE_CRON", "0 8,14,20 * * *"))
    tz: str = field(default_factory=lambda: env("TZ", "America/Sao_Paulo"))
    watchlist_path: Path = field(default_factory=lambda: Path(env("WATCHLIST", str(ROOT / "watchlist.yaml"))))

    app_password: str | None = field(default_factory=lambda: env("APP_PASSWORD"))
    secret_key: str = field(default_factory=lambda: env("SECRET_KEY", "troque-esta-chave"))

    def load_yaml_watches(self) -> list[Watch]:
        """Lê o watchlist.yaml. Usado só para popular o banco na primeira execução."""
        if not self.watchlist_path.exists():
            return []
        data = yaml.safe_load(self.watchlist_path.read_text(encoding="utf-8")) or {}
        return [Watch(**w) for w in data.get("watches", [])]


settings = Settings()
