import pytest

from app.services.config import ConfigError, load_settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ADMIN_API_KEYS", raising=False)
    s = load_settings()
    assert s.env == "dev"
    assert s.admin_api_keys == []
    assert s.notify_retries == 3


def test_list_and_int_parsing(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEYS", "a, b ,,c")
    monkeypatch.setenv("SMTP_PORT", "587")
    s = load_settings()
    assert s.admin_api_keys == ["a", "b", "c"]
    assert s.smtp_port == 587


def test_bad_int_and_prod_sqlite(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "abc")
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    with pytest.raises(ConfigError):
        load_settings()
