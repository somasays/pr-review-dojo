"""Application settings loaded once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


class ConfigError(Exception):
    pass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    env: str = "dev"
    database_url: str = "sqlite:///./dojo.db"
    admin_api_keys: list[str] = field(default_factory=list)
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_password: str = ""
    warehouse_email: str = "warehouse@example.com"
    notify_retries: int = 3
    notify_backoff_seconds: float = 0.2
    worker_concurrency: int = 4
    data_lake_root: str = "./data"
    page_size_max: int = 200

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


def load_settings() -> Settings:
    env = os.environ.get("APP_ENV", "dev")
    database_url = os.environ.get("DATABASE_URL", "sqlite:///./dojo.db")
    if env == "prod" and database_url.startswith("sqlite"):
        raise ConfigError("sqlite is not allowed in prod")
    return Settings(
        env=env,
        database_url=database_url,
        admin_api_keys=_list("ADMIN_API_KEYS", []),
        smtp_host=os.environ.get("SMTP_HOST", "localhost"),
        smtp_port=_int("SMTP_PORT", 25),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        warehouse_email=os.environ.get("WAREHOUSE_EMAIL", "warehouse@example.com"),
        notify_retries=_int("NOTIFY_RETRIES", 3),
        notify_backoff_seconds=float(os.environ.get("NOTIFY_BACKOFF_SECONDS", "0.2")),
        worker_concurrency=_int("WORKER_CONCURRENCY", 4),
        data_lake_root=os.environ.get("DATA_LAKE_ROOT", "./data"),
        page_size_max=_int("PAGE_SIZE_MAX", 200),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
