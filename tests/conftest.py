"""Общие фикстуры автотестов.

Все тесты работают с отдельной временной SQLite-базой и никогда не
обращаются к api.telegram.org или к рабочему файлу tea_shop.db.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = Path(tempfile.mkdtemp(prefix="tea-shop-tests-"))
TEST_DB = TEST_DIR / "tea_shop_test.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["BOT_TOKEN"] = "123456789:" + "A" * 35
os.environ["SKIP_TELEGRAM"] = "1"
os.environ["BASE_URL"] = ""
os.environ.pop("WEBAPP_URL", None)
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["MODE"] = "polling"
os.environ["HOST"] = "127.0.0.1"
os.environ["PORT"] = "8000"
os.environ["DELETE_WEBHOOK_ON_SHUTDOWN"] = "0"

import config  # noqa: E402
from models import Base, SessionLocal, init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_database():
    """Удаляет временную БД после завершения сессии pytest."""
    yield
    config.engine.dispose()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_database():
    """Перед каждым тестом оставляет пустые таблицы."""
    Base.metadata.drop_all(bind=config.engine)
    Base.metadata.create_all(bind=config.engine)
    yield


@pytest.fixture()
def seeded_database():
    """Пустая БД с демо-каталогом из пяти товаров."""
    init_db()
    yield


@pytest.fixture()
def db_session():
    """Сессия SQLAlchemy для прямых проверок в тестах."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
