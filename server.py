"""
server.py — единый aiohttp-сервер проекта.

Один процесс и один порт (по умолчанию 8000) обслуживают:
  * GET  /             — HTML-витрина Telegram WebApp;
  * GET  /api/products — каталог из БД;
  * POST /api/order    — оформление заказа из витрины + чек в Telegram;
  * POST /webhook      — приём обновлений Telegram;
  * GET  /healthz      — проверка сервера и БД.

Режимы (переменная MODE в .env):
  * webhook — бот принимает обновления от Telegram (нужен HTTPS/ngrok);
  * polling — бот сам опрашивает Telegram, но витрина всё равно доступна.

Для локальной проверки без Telegram: SKIP_TELEGRAM=1.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
from types import SimpleNamespace
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from sqlalchemy.exc import SQLAlchemyError

import config
from bot import create_bot, create_dispatcher, create_order, format_order_receipt, set_bot_commands
from config import (
    HOST,
    MODE,
    PORT,
    PROJECT_DIR,
    SKIP_TELEGRAM,
    WEBHOOK_PATH,
    WEBHOOK_SECRET,
    SessionLocal,
    check_connection,
    describe_database,
    get_public_base_url,
)
from models import Product, User, init_db, product_to_dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("tea_server")

DELETE_WEBHOOK_ON_SHUTDOWN = os.getenv("DELETE_WEBHOOK_ON_SHUTDOWN", "1") == "1"

BOT_KEY = web.AppKey("bot", Bot)
DISPATCHER_KEY = web.AppKey("dp", Dispatcher)
WEBHOOK_SECRET_KEY = web.AppKey("webhook_secret", str)
POLLING_TASK_KEY = web.AppKey("polling_task", asyncio.Task)

PICKUP_ADDRESS = "г. Москва, ул. Мясницкая, д. 13, стр. 3"

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


# ---------------------------------------------------------------------------
# HTTP-обработчики
# ---------------------------------------------------------------------------

async def index_handler(_request: web.Request) -> web.StreamResponse:
    """Отдаёт витрину без кэширования, чтобы Telegram не показывал старую версию."""
    return web.FileResponse(PROJECT_DIR / "index.html", headers=NO_CACHE_HEADERS)


async def favicon_handler(_request: web.Request) -> web.Response:
    return web.Response(status=204)


def _load_products() -> list[dict[str, Any]]:
    session = SessionLocal()
    try:
        products = session.query(Product).order_by(Product.id).all()
        return [product_to_dict(product) for product in products]
    finally:
        session.close()


async def products_handler(_request: web.Request) -> web.Response:
    """JSON-каталог: витрина не хранит товары и цены у себя."""
    try:
        products = await asyncio.to_thread(_load_products)
    except SQLAlchemyError:
        logger.exception("Не удалось прочитать каталог")
        return web.json_response(
            {"error": "Не удалось загрузить каталог"},
            status=500,
        )
    return web.json_response(products)


def _parse_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_api_user(payload: dict[str, Any]) -> tuple[int, str | None, str | None]:
    init_data = payload.get("initData") or payload.get("init_data")
    if init_data and config.BOT_TOKEN:
        try:
            parsed = safe_parse_webapp_init_data(config.BOT_TOKEN, str(init_data))
            if parsed.user is not None:
                return parsed.user.id, parsed.user.username, parsed.user.first_name
        except (ValueError, TypeError):
            logger.warning("Не удалось проверить initData WebApp, используем fallback")

    telegram_id = _parse_positive_int(payload.get("telegram_id"))
    if telegram_id is not None:
        return telegram_id, None, None

    session = SessionLocal()
    try:
        user = session.query(User).order_by(User.telegram_id.desc()).first()
        if user is not None:
            return user.telegram_id, user.username, user.first_name
    finally:
        session.close()
    raise ValueError("Не удалось определить telegram_id для заказа")


def _save_api_order(payload: dict[str, Any]) -> tuple[int, list[str], Any, int]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Корзина пуста")
    if len(raw_items) > 50:
        raise ValueError("Слишком много позиций в заказе")

    quantities: dict[int, int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Некорректная позиция заказа")
        product_id = _parse_positive_int(raw.get("id"))
        quantity = _parse_positive_int(raw.get("quantity", 1))
        if product_id is None or quantity is None:
            raise ValueError("Некорректный id или количество товара")
        quantities[product_id] = min(
            quantities.get(product_id, 0) + min(quantity, 99),
            99,
        )

    chat_id, username, first_name = _resolve_api_user(payload)
    tg_user = SimpleNamespace(id=chat_id, username=username, first_name=first_name)

    session = SessionLocal()
    try:
        order, lines, total = create_order(session, tg_user, quantities)
        session.commit()
        return order.id, lines, total, chat_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def order_handler(request: web.Request) -> web.Response:
    """Принимает заказ из витрины, сохраняет его и отправляет чек в Telegram."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return web.json_response({"error": "Некорректный JSON"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "Ожидался JSON-объект"}, status=400)

    try:
        order_id, lines, total, chat_id = await asyncio.to_thread(_save_api_order, payload)
    except (ValueError, LookupError) as exc:
        logger.warning("Заказ из витрины отклонён: %s", exc)
        return web.json_response({"error": str(exc)}, status=400)
    except SQLAlchemyError:
        logger.exception("Ошибка БД при сохранении заказа из витрины")
        return web.json_response({"error": "Не удалось сохранить заказ"}, status=500)
    except Exception:
        logger.exception("Неожиданная ошибка при сохранении заказа из витрины")
        return web.json_response({"error": "Не удалось сохранить заказ"}, status=500)

    receipt = format_order_receipt(SimpleNamespace(id=order_id), lines, total)
    if SKIP_TELEGRAM:
        logger.warning("SKIP_TELEGRAM=1: чек заказа #%s не отправлен", order_id)
    else:
        try:
            await request.app[BOT_KEY].send_message(chat_id=chat_id, text=receipt)
            logger.info("Чек заказа #%s отправлен в чат %s", order_id, chat_id)
        except Exception:
            logger.exception("Не удалось отправить чек заказа #%s", order_id)

    return web.json_response(
        {"ok": True, "order_id": order_id, "total": float(total), "chat_id": chat_id},
        status=201,
    )


async def health_handler(_request: web.Request) -> web.Response:
    """Проверка для локального запуска и хостингов."""
    ok = await asyncio.to_thread(check_connection, False)
    payload = {
        "status": "ok" if ok else "error",
        "database": config.DATABASE_BACKEND,
        "mode": MODE,
    }
    return web.json_response(payload, status=200 if ok else 503)


async def webhook_disabled_handler(_request: web.Request) -> web.Response:
    return web.json_response(
        {"error": "Webhook отключён: сервер работает в режиме polling"},
        status=409,
    )


# ---------------------------------------------------------------------------
# Жизненный цикл приложения
# ---------------------------------------------------------------------------

async def on_startup(app: web.Application) -> None:
    bot: Bot = app[BOT_KEY]
    dispatcher: Dispatcher = app[DISPATCHER_KEY]

    logger.info("Режим: %s | БД: %s", MODE, describe_database())

    if SKIP_TELEGRAM:
        logger.warning("SKIP_TELEGRAM=1: обращения к Telegram API отключены")
        return

    await set_bot_commands(bot)

    if MODE == "polling":
        await bot.delete_webhook(drop_pending_updates=False)
        task = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        )

        def _log_polling_result(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            error = done.exception()
            if error is not None:
                logger.error("Polling завершился с ошибкой: %s", error)
                logger.error(
                    "Проверьте доступ к https://api.telegram.org. "
                    "При блокировке используйте VPN или TELEGRAM_PROXY в .env."
                )

        task.add_done_callback(_log_polling_result)
        app[POLLING_TASK_KEY] = task
        logger.info("Бот запущен в режиме polling; витрина доступна по HTTPS")
        return

    base_url = get_public_base_url()
    if not base_url:
        raise RuntimeError(
            "Не найден публичный HTTPS-адрес. Запустите `ngrok http 8000` "
            "или укажите BASE_URL в .env."
        )

    secret = app[WEBHOOK_SECRET_KEY]
    webhook_url = f"{base_url}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(
            webhook_url,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=True,
            secret_token=secret if secret else None,
        )
        info = await bot.get_webhook_info()
    except Exception as exc:
        logger.error("Не удалось установить webhook: %s", exc)
        logger.error(
            "Проверьте доступ к https://api.telegram.org. "
            "При блокировке используйте VPN или TELEGRAM_PROXY в .env."
        )
        raise

    logger.info(
        "Webhook установлен: %s | ожидает обновлений: %s | ошибка: %s",
        info.url,
        info.pending_update_count,
        info.last_error_message or "нет",
    )


async def on_cleanup(app: web.Application) -> None:
    task: asyncio.Task | None = app.get(POLLING_TASK_KEY)
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    bot: Bot | None = app.get(BOT_KEY)
    if bot is None:
        return

    if (
        not SKIP_TELEGRAM
        and MODE == "webhook"
        and DELETE_WEBHOOK_ON_SHUTDOWN
    ):
        with contextlib.suppress(Exception):
            await bot.delete_webhook(drop_pending_updates=False)
            logger.info("Webhook удалён при остановке сервера")

    with contextlib.suppress(Exception):
        await bot.session.close()


# ---------------------------------------------------------------------------
# Сборка и запуск
# ---------------------------------------------------------------------------

def build_application() -> web.Application:
    """Создаёт aiohttp-приложение со всеми маршрутами."""
    bot = create_bot()
    dispatcher = create_dispatcher()
    secret = WEBHOOK_SECRET or secrets.token_urlsafe(32)

    app = web.Application()
    app[BOT_KEY] = bot
    app[DISPATCHER_KEY] = dispatcher
    app[WEBHOOK_SECRET_KEY] = secret

    app.router.add_get("/", index_handler)
    app.router.add_get("/index.html", index_handler)
    app.router.add_get("/api/products", products_handler)
    app.router.add_post("/api/order", order_handler)
    app.router.add_get("/healthz", health_handler)
    app.router.add_get("/favicon.ico", favicon_handler)

    if MODE == "webhook":
        webhook_handler = SimpleRequestHandler(
            dispatcher=dispatcher,
            bot=bot,
            secret_token=secret,
            handle_in_background=False,
        )
        webhook_handler.register(app, path=WEBHOOK_PATH)
    else:
        app.router.add_post(WEBHOOK_PATH, webhook_disabled_handler)

    setup_application(app, dispatcher, bot=bot)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    """Точка входа: проверки, инициализация БД и запуск aiohttp."""
    if MODE not in ("webhook", "polling"):
        raise SystemExit(
            f"Неизвестный MODE={MODE!r}. Допустимо: webhook или polling."
        )

    if not check_connection():
        raise SystemExit(1)

    init_db()

    if MODE == "webhook" and not SKIP_TELEGRAM:
        base_url = get_public_base_url()
        if not base_url:
            print(
                "\n=== НЕТ ПУБЛИЧНОГО HTTPS-АДРЕСА ===\n"
                "Для webhook нужен внешний HTTPS. Локально проще всего ngrok:\n"
                "  1) установите ngrok и выполните: ngrok config add-authtoken <токен>\n"
                "  2) в другом терминале: ngrok http 8000\n"
                "  3) адрес https://....ngrok-free.app подхватится автоматически\n"
                "     (или пропишите его в .env как BASE_URL)\n"
                "  4) перезапустите сервер: ./run.sh web\n"
                "Либо запустите без webhook: ./run.sh polling\n"
            )
            raise SystemExit(1)
        if not base_url.startswith("https://"):
            print(
                f"\nBASE_URL должен начинаться с https:// (сейчас {base_url!r}).\n"
                "Telegram не принимает HTTP-адреса для webhook и WebApp.\n"
            )
            raise SystemExit(1)

    app = build_application()
    logger.info("Сервер слушает http://%s:%s", HOST, PORT)
    web.run_app(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
