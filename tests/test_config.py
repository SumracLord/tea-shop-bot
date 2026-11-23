from __future__ import annotations

import pytest

import config


def test_absolutize_relative_sqlite():
    result = config._absolutize_sqlite("sqlite:///data.db")
    assert result == f"sqlite:///{config.PROJECT_DIR / 'data.db'}"


def test_absolutize_sqlite_with_driver_and_query():
    result = config._absolutize_sqlite("sqlite+pysqlite:///data.db?mode=ro")
    assert result == (
        f"sqlite+pysqlite:///{config.PROJECT_DIR / 'data.db'}?mode=ro"
    )


@pytest.mark.parametrize(
    "url",
    [
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite:////tmp/tea.db",
        "sqlite+pysqlite:////tmp/tea.db",
    ],
)
def test_absolutize_keeps_absolute_and_memory_urls(url):
    assert config._absolutize_sqlite(url) == url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgres://u:p@example.com:5432/db",
            "postgresql+psycopg2://u:p@example.com:5432/db",
        ),
        (
            "postgresql://u:p@example.com:5432/db",
            "postgresql+psycopg2://u:p@example.com:5432/db",
        ),
        (
            "postgresql+asyncpg://u:p@example.com:5432/db",
            "postgresql+psycopg2://u:p@example.com:5432/db",
        ),
    ],
)
def test_normalize_postgres_urls(raw, expected):
    assert config._normalize_database_url(raw) == expected


def test_normalize_strips_quotes_and_spaces():
    assert config._normalize_database_url('  "sqlite:///tea.db"  ') == (
        f"sqlite:///{config.PROJECT_DIR / 'tea.db'}"
    )


def test_normalize_rejects_direct_supabase_host():
    with pytest.raises(RuntimeError, match="Session pooler"):
        config._normalize_database_url(
            "postgresql://postgres.ref:secret@db.ref.supabase.co:5432/postgres"
        )


def test_normalize_rejects_placeholders():
    with pytest.raises(RuntimeError, match="плейсхолдер"):
        config._normalize_database_url(
            "postgresql://postgres.ref:<password>"
            "@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
        )


def test_normalize_rejects_unknown_scheme():
    with pytest.raises(RuntimeError, match="sqlite:///"):
        config._normalize_database_url("mysql://u:p@example.com/db")


def test_mask_database_url_hides_password():
    masked = config.mask_database_url("postgresql+psycopg2://u:secret@host:5432/db")
    assert "secret" not in masked
    assert masked == "postgresql+psycopg2://u:***@host:5432/db"


def test_mask_database_url_keeps_sqlite():
    assert config.mask_database_url("sqlite:////tmp/tea.db") == "sqlite:////tmp/tea.db"


def test_describe_database_does_not_leak_password(monkeypatch):
    monkeypatch.setattr(
        config,
        "DATABASE_URL",
        "postgresql+psycopg2://u:secret@host:5432/db",
    )
    monkeypatch.setattr(config, "DATABASE_BACKEND", "postgresql")
    description = config.describe_database()
    assert "secret" not in description
    assert "postgresql" in description


def test_public_base_url_from_env(monkeypatch):
    monkeypatch.setenv("BASE_URL", " https://shop.example/ ")
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    assert config.get_public_base_url() == "https://shop.example"


def test_public_base_url_from_webapp_url(monkeypatch):
    monkeypatch.setenv("BASE_URL", "")
    monkeypatch.setenv("WEBAPP_URL", "https://webapp.example/")
    assert config.get_public_base_url() == "https://webapp.example"


@pytest.mark.parametrize(
    "value",
    ["http://localhost:8000", "http://127.0.0.1:8000"],
)
def test_public_base_url_ignores_local_http(monkeypatch, value):
    monkeypatch.setenv("BASE_URL", value)
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    assert config.get_public_base_url() is None


def test_public_base_url_from_replit(monkeypatch):
    monkeypatch.setenv("BASE_URL", "")
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    monkeypatch.setenv("REPLIT_DEV_DOMAIN", "tea.replit.dev")
    assert config.get_public_base_url() == "https://tea.replit.dev"


def test_public_base_url_falls_back_to_ngrok(monkeypatch):
    monkeypatch.setenv("BASE_URL", "")
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    monkeypatch.delenv("REPLIT_DEV_DOMAIN", raising=False)
    monkeypatch.delenv("REPLIT_DOMAINS", raising=False)
    monkeypatch.setattr(
        config,
        "_detect_ngrok_base_url",
        lambda: "https://demo.ngrok-free.app",
    )
    assert config.get_public_base_url() == "https://demo.ngrok-free.app"


def test_get_webapp_url_adds_slash(monkeypatch):
    monkeypatch.setattr(config, "get_public_base_url", lambda: "https://shop.example")
    assert config.get_webapp_url() == "https://shop.example/"


def test_get_webapp_url_without_public_base(monkeypatch):
    monkeypatch.setattr(config, "get_public_base_url", lambda: None)
    assert config.get_webapp_url() is None


def test_check_connection_ok():
    assert config.check_connection(verbose=False) is True


def test_check_connection_error(monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("нет связи")

    monkeypatch.setattr(config, "engine", BrokenEngine())
    assert config.check_connection(verbose=False) is False


def test_friendly_database_errors():
    assert "Supabase" in config._friendly_db_error(Exception("tenant/user not found"))
    assert "пароль" in config._friendly_db_error(
        Exception("password authentication failed for user")
    )
    assert "Session pooler" in config._friendly_db_error(
        Exception("Network is unreachable")
    )
    assert config._friendly_db_error(Exception("что-то другое")) is None
