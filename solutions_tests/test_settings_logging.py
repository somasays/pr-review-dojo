"""Hidden test: startup logging must not print credentials."""

import logging

from app.services.config import load_settings


def test_settings_log_does_not_leak_secrets(monkeypatch, caplog):
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
    monkeypatch.setenv("ADMIN_API_KEYS", "k-admin-1")
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "whsec-live-1")
    with caplog.at_level(logging.INFO, logger="app.services.config"):
        settings = load_settings()
    assert settings.smtp_password == "hunter2"
    assert "hunter2" not in caplog.text
    assert "k-admin-1" not in caplog.text
    assert "whsec-live-1" not in caplog.text
