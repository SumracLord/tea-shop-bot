"""
config.py — единая конфигурация проекта «Чайный магазин».

Здесь создаются:
  * engine       — движок SQLAlchemy (SQLite для локального демо или Supabase);
  * SessionLocal — фабрика сессий;
  * Base         — базовый класс моделей.

Все остальные модули (models.py, bot.py, server.py, dashboard.py) берут
подключение к БД только отсюда, чтобы не создавать несколько движков.

По умолчанию используется SQLite (tea_shop.db). Для Supabase достаточно
поменять DATABASE_URL в .env на строку Session pooler.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# Версия Python и пути
# ---------------------------------------------------------------------------

if sys.version_info < (3, 13):
    raise RuntimeError(
        f"Требуется Python 3.13+, найден {sys.version.split()[0]}. "
        "Установка: uv python install 3.13 && uv sync"
    )

PROJECT_DIR = Path(__file__).resolve().parent
ENV_PATH = PROJECT_DIR / ".env"

# Явный путь: настройки одинаково читаются из любого рабочего каталога.
load_dotenv(ENV_PATH)


# ---------------------------------------------------------------------------
# Переменные окружения
# ---------------------------------------------------------------------------

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

# Необязательный прокси для доступа к api.telegram.org,
# например socks5://127.0.0.1:10808. Для SOCKS нужен пакет aiohttp-socks.
TELEGRAM_PROXY = (os.getenv("TELEGRAM_PROXY") or "").strip()

# Порт локального SOCKS5, который поднимает приложение «Киберщит».
KIBERSHCHIT_SOCKS_HOST = "127.0.0.1"
KIBERSHCHIT_SOCKS_PORT = 10808

# Автоподхват локального SOCKS Киберщита, если TELEGRAM_PROXY не задан.
TELEGRAM_PROXY_AUTODETECT = (
    os.getenv("TELEGRAM_PROXY_AUTODETECT") or "1"
).strip().lower() not in {"0", "false", "no", "off"}

_DEFAULT_DATABASE_URL = "sqlite:///tea_shop.db"
_RAW_DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

HOST = (os.getenv("HOST") or "0.0.0.0").strip()
try:
    PORT = int((os.getenv("PORT") or "8000").strip())
except ValueError:
    PORT = 8000

MODE = (os.getenv("MODE") or "webhook").strip().lower()
SKIP_TELEGRAM = (os.getenv("SKIP_TELEGRAM") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def get_telegram_proxy() -> str:
    """Возвращает прокси для запросов к Telegram.

    Явно заданный TELEGRAM_PROXY всегда имеет приоритет. Если он пуст,
    проверяется локальный SOCKS5 Киберщита на 127.0.0.1:10808. В офлайн-
    режиме (SKIP_TELEGRAM=1) автоподхват отключён, чтобы тесты и витрина
    без токена не зависели от запущенного VPN-приложения.
    """
    if TELEGRAM_PROXY:
        return TELEGRAM_PROXY
    if not TELEGRAM_PROXY_AUTODETECT or SKIP_TELEGRAM:
        return ""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(
                (KIBERSHCHIT_SOCKS_HOST, KIBERSHCHIT_SOCKS_PORT)
            ) == 0:
                return (
                    f"socks5://{KIBERSHCHIT_SOCKS_HOST}:"
                    f"{KIBERSHCHIT_SOCKS_PORT}"
                )
    except OSError:
        pass
    return ""


_webhook_path = (os.getenv("WEBHOOK_PATH") or "/webhook").strip()
if not _webhook_path.startswith("/"):
    _webhook_path = "/" + _webhook_path
WEBHOOK_PATH = _webhook_path.rstrip("/") or "/webhook"

WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()

# Плейсхолдеры, которые нельзя оставлять в .env.
_DB_PLACEHOLDERS = (
    "YOUR_PASSWORD",
    "your-project-ref",
    "ВАШ_ПАРОЛЬ",
    "ВАШ_РЕАЛЬНЫЙ_ПАРОЛЬ",
    "<password>",
    "<ref>",
)


def _absolutize_sqlite(url: str) -> str:
    """Делает путь к SQLite-файлу абсолютным относительно папки проекта.

    Иначе бот и Streamlit, запущенные из разных каталогов, открыли бы
    разные файлы tea_shop.db.
    """
    if url in ("sqlite://", "sqlite:///:memory:"):
        return url

    match = re.match(
        r"^(sqlite(?:\+[A-Za-z0-9_]+)?):///([^?]*)(\?.*)?$",
        url,
    )
    if not match:
        return url

    prefix, path, query = match.groups()
    if not path or path == ":memory:" or os.path.isabs(path):
        return url
    return f"{prefix}:///{PROJECT_DIR / path}{query or ''}"


def _normalize_database_url(raw: str) -> str:
    """Проверяет и приводит DATABASE_URL к формату SQLAlchemy."""
    url = raw.strip().strip('"').strip("'")
    if not url:
        url = _DEFAULT_DATABASE_URL

    if url.startswith("sqlite"):
        return _absolutize_sqlite(url)

    # postgres://, postgresql://, postgresql+asyncpg:// -> psycopg2
    url = re.sub(
        r"^postgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://",
        "postgresql+psycopg2://",
        url,
    )

    if not url.startswith("postgresql+psycopg2://"):
        raise RuntimeError(
            "DATABASE_URL должен начинаться с sqlite:/// или "
            "postgresql+psycopg2://.\n"
            f"Сейчас: {url!r}"
        )

    if re.search(r"@db\.[a-z0-9-]+\.supabase\.co", url):
        raise RuntimeError(
            "Для Supabase нельзя использовать прямой хост db.<ref>.supabase.co: "
            "у него нет IPv4, и из многих сетей он недоступен.\n"
            "Возьмите строку Session pooler в Supabase Dashboard → Settings → "
            "Database → Connection string → Session pooler (порт 5432).\n"
            "Пример:\n"
            "  postgresql+psycopg2://postgres.<ref>:<пароль>"
            "@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
        )

    if any(token in url for token in _DB_PLACEHOLDERS):
        raise RuntimeError(
            "DATABASE_URL содержит плейсхолдер. Замените <ref>, <password> "
            "и регион на реальные значения из Supabase."
        )

    return url


try:
    DATABASE_URL = _normalize_database_url(_RAW_DATABASE_URL or _DEFAULT_DATABASE_URL)
except RuntimeError as exc:
    print(f"\n=== ОШИБКА В DATABASE_URL ===\n{exc}\n", file=sys.stderr)
    raise SystemExit(1)

DATABASE_BACKEND = "sqlite" if DATABASE_URL.startswith("sqlite") else "postgresql"


# ---------------------------------------------------------------------------
# Движок SQLAlchemy
# ---------------------------------------------------------------------------

if DATABASE_BACKEND == "sqlite":
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

else:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15},
    )

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)

Base = declarative_base()


# ---------------------------------------------------------------------------
# Публичный адрес витрины
# ---------------------------------------------------------------------------

_ngrok_cache: dict[str, object] = {"url": None, "checked_at": 0.0}


def _detect_replit_base_url() -> str | None:
    """Публичный HTTPS-адрес Repl, если код запущен на Replit."""
    domain = (os.getenv("REPLIT_DEV_DOMAIN") or "").strip()
    if domain:
        return f"https://{domain}".rstrip("/")

    domains = (os.getenv("REPLIT_DOMAINS") or "").strip()
    if domains:
        first = domains.split(",")[0].strip()
        if first:
            return f"https://{first}".rstrip("/")
    return None


def _detect_ngrok_base_url() -> str | None:
    """Берёт HTTPS-адрес первого туннеля из локального API ngrok.

    Результат кэшируется на 5 секунд, чтобы каждый /start не ждал HTTP.
    """
    now = time.monotonic()
    if now - float(_ngrok_cache["checked_at"]) < 5:
        cached = _ngrok_cache["url"]
        return cached if isinstance(cached, str) else None

    url: str | None = None
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:4040/api/tunnels", timeout=2
        ) as response:
            payload = json.load(response)
        for tunnel in payload.get("tunnels", []):
            public_url = str(tunnel.get("public_url") or "")
            if public_url.startswith("https://"):
                url = public_url.rstrip("/")
                break
    except Exception:
        url = None

    _ngrok_cache["url"] = url
    _ngrok_cache["checked_at"] = now
    return url


def get_public_base_url() -> str | None:
    """Публичный базовый URL: BASE_URL/WEBAPP_URL, Replit или ngrok."""
    explicit = (os.getenv("BASE_URL") or os.getenv("WEBAPP_URL") or "").strip()
    if explicit:
        explicit = explicit.rstrip("/")
        if explicit.startswith("http://localhost") or explicit.startswith("http://127.0.0.1"):
            return None
        return explicit

    return _detect_replit_base_url() or _detect_ngrok_base_url()


def get_webapp_url() -> str | None:
    """URL страницы витрины для кнопки Telegram WebApp."""
    base = get_public_base_url()
    return f"{base}/" if base else None


# ---------------------------------------------------------------------------
# Диагностика подключения
# ---------------------------------------------------------------------------

def mask_database_url(url: str | None = None) -> str:
    """Скрывает пароль в строке подключения для логов и ошибок."""
    value = url or DATABASE_URL
    if value.startswith("sqlite"):
        return value
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", value)


def _friendly_db_error(exc: Exception) -> str | None:
    text_error = str(exc)

    if "tenant/user" in text_error or "ENOTFOUND" in text_error:
        return (
            "Supabase не нашёл проект или пользователя. Проверьте:\n"
            "  • не удалён/не приостановлен ли проект в Supabase Dashboard;\n"
            "  • ref проекта в имени пользователя postgres.<ref>;\n"
            "  • что используется Session pooler, а не прямой хост."
        )
    if "password authentication failed" in text_error:
        return (
            "Неверный пароль БД. Возьмите свежую строку подключения в "
            "Supabase Dashboard → Settings → Database или сбросьте пароль."
        )
    if "Network is unreachable" in text_error or "Cannot assign requested address" in text_error:
        return (
            "Сеть не достучалась до БД. Для Supabase используйте Session pooler "
            "(aws-0-<region>.pooler.supabase.com:5432): у прямого хоста "
            "db.<ref>.supabase.co обычно нет IPv4."
        )
    if "timed out" in text_error.lower():
        return (
            "Подключение к БД зависло. Проверьте интернет и настройки "
            "Supabase → Settings → Networking (Allow all / ваш IP)."
        )
    if "unable to open database file" in text_error.lower():
        return "Не удалось открыть файл SQLite. Проверьте права на папку проекта."
    return None


def check_connection(verbose: bool = True) -> bool:
    """Проверяет БД запросом SELECT 1 и не роняет процесс трейсбеком."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — показываем понятную подсказку
        if verbose:
            print(
                f"\n=== НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ К БАЗЕ ===\n"
                f"{mask_database_url()}\n{exc}",
                file=sys.stderr,
            )
            hint = _friendly_db_error(exc)
            if hint:
                print(f"\n>>> Подсказка:\n{hint}", file=sys.stderr)
            print("", file=sys.stderr)
        return False


def describe_database() -> str:
    """Короткое описание БД без секретов (для логов и run.sh check)."""
    return f"{DATABASE_BACKEND}: {mask_database_url()}"
