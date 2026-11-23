"""
deploy/pythonanywhere_wsgi.py — WSGI-адаптер для PythonAnywhere.

Зачем: бесплатный тариф PythonAnywhere не умеет запускать aiohttp/ASGI
сервер на произвольном порту. Здесь тот же бот и та же витрина работают
как обычное WSGI-приложение:

  * GET  /             — index.html;
  * GET  /api/products — каталог;
  * GET  /healthz      — проверка;
  * POST /webhook      — обновления Telegram через общий event loop.

Использование:
  1. Загрузите проект на PythonAnywhere.
  2. В .env задайте:
       BASE_URL=https://<логин>.pythonanywhere.com
       WEBHOOK_SECRET=<постоянная случайная строка>
       MODE=webhook
  3. В настройках Web-приложения укажите WSGI-файл, например:
       /home/<логин>/tea-shop/deploy/pythonanywhere_wsgi.py
     либо оставьте стандартный файл и добавьте:
       import sys
       sys.path.insert(0, "/home/<логин>/tea-shop")
       from deploy.pythonanywhere_wsgi import application
  4. Нажмите Reload.

Важно: PythonAnywhere может требовать whitelist для api.telegram.org.
Если исходящие запросы к Telegram блокируются, используйте Replit или
платный always-on task с polling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from aiogram.types import Update

import config
from bot import create_bot, create_dispatcher, set_bot_commands
from config import (
    WEBHOOK_PATH,
    WEBHOOK_SECRET,
    SessionLocal,
    check_connection,
    get_public_base_url,
)
from models import Product, init_db, product_to_dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("tea_wsgi")

# PythonAnywhere держит процесс живым: используем один event loop,
# чтобы aiohttp-сессия бота не привязывалась к разным loop'ам.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_loop_ready = True

bot = create_bot()
dispatcher = create_dispatcher()
_bootstrapped = False


def _bootstrap() -> None:
    """Инициализирует БД и устанавливает webhook (один раз на процесс)."""
    global _bootstrapped
    if _bootstrapped:
        return

    if config.SKIP_TELEGRAM:
        init_db()
        logger.warning(
            "SKIP_TELEGRAM=1: Telegram API не вызывается, WSGI работает локально"
        )
        _bootstrapped = True
        return

    if not WEBHOOK_SECRET:
        raise RuntimeError(
            "Для PythonAnywhere задайте постоянный WEBHOOK_SECRET в .env "
            "(несколько WSGI-процессов должны использовать один секрет)."
        )

    base_url = get_public_base_url()
    if not base_url:
        raise RuntimeError(
            "Задайте BASE_URL=https://<логин>.pythonanywhere.com в .env."
        )

    if not check_connection():
        raise RuntimeError("Нет подключения к базе данных.")

    init_db()

    _loop.run_until_complete(set_bot_commands(bot))
    _loop.run_until_complete(
        bot.set_webhook(
            f"{base_url}{WEBHOOK_PATH}",
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
            secret_token=WEBHOOK_SECRET,
        )
    )
    logger.info("Webhook установлен: %s%s", base_url, WEBHOOK_PATH)
    _bootstrapped = True


# Пытаемся настроить всё при загрузке WSGI-файла. Если Telegram временно
# недоступен, приложение не падает: повторная попытка будет при запросе.
try:
    _bootstrap()
except Exception:
    logger.exception("Не удалось выполнить начальную настройку")


def _read_body(environ: dict[str, Any]) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return b""
    return environ["wsgi.input"].read(length)


def _respond_bytes(
    start_response: Any,
    status: str,
    payload: bytes,
    content_type: str = "text/plain; charset=utf-8",
) -> Iterable[bytes]:
    start_response(
        status,
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(payload))),
        ],
    )
    return [payload]


def _respond_json(
    start_response: Any,
    status: str,
    data: Any,
) -> Iterable[bytes]:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return _respond_bytes(start_response, status, payload, "application/json; charset=utf-8")


def _load_products() -> list[dict[str, Any]]:
    session = SessionLocal()
    try:
        products = session.query(Product).order_by(Product.id).all()
        return [product_to_dict(product) for product in products]
    finally:
        session.close()


def application(environ: dict[str, Any], start_response: Any) -> Iterable[bytes]:
    """WSGI-точка входа."""
    method = str(environ.get("REQUEST_METHOD", "GET")).upper()
    path = str(environ.get("PATH_INFO", "/"))

    if path in ("/", "/index.html"):
        if method != "GET":
            return _respond_json(start_response, "405 Method Not Allowed", {"error": "method"})
        try:
            html = (PROJECT_DIR / "index.html").read_bytes()
        except OSError:
            return _respond_json(start_response, "500 Internal Server Error", {"error": "index.html"})
        return _respond_bytes(start_response, "200 OK", html, "text/html; charset=utf-8")

    if path == "/api/products":
        if method != "GET":
            return _respond_json(start_response, "405 Method Not Allowed", {"error": "method"})
        try:
            return _respond_json(start_response, "200 OK", _load_products())
        except Exception:
            logger.exception("Не удалось прочитать каталог")
            return _respond_json(start_response, "500 Internal Server Error", {"error": "database"})

    if path == "/healthz":
        ok = check_connection(verbose=False)
        return _respond_json(
            start_response,
            "200 OK" if ok else "503 Service Unavailable",
            {"status": "ok" if ok else "error", "database": config.DATABASE_BACKEND},
        )

    if path == WEBHOOK_PATH:
        if method != "POST":
            return _respond_json(start_response, "405 Method Not Allowed", {"error": "method"})

        received_secret = environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
        if WEBHOOK_SECRET and received_secret != WEBHOOK_SECRET:
            return _respond_json(start_response, "403 Forbidden", {"error": "secret"})

        try:
            _bootstrap()
        except Exception:
            logger.exception("Не удалось подготовить webhook")
            return _respond_json(
                start_response,
                "503 Service Unavailable",
                {"error": "not ready"},
            )

        try:
            payload = json.loads(_read_body(environ).decode("utf-8"))
            update = Update.model_validate(payload, context={"bot": bot})
            _loop.run_until_complete(dispatcher.feed_update(bot, update))
        except Exception:
            logger.exception("Ошибка обработки обновления")
            return _respond_json(
                start_response,
                "500 Internal Server Error",
                {"error": "update"},
            )

        return _respond_bytes(start_response, "200 OK", b"ok")

    return _respond_json(start_response, "404 Not Found", {"error": "not found"})
