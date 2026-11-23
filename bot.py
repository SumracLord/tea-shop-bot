"""
bot.py — Telegram-бот «Интернет-магазин китайского чая» на Aiogram 3.

Режимы:
  * webhook (по умолчанию) — запускается через server.py;
  * polling — этот файл, удобно для отладки без публичного HTTPS.

Логика:
  /start          — регистрирует пользователя и присылает кнопку WebApp;
  WebAppData      — принимает корзину из витрины, пересчитывает сумму
                    по ценам из БД и сохраняет заказ.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from config import (
    BOT_TOKEN,
    HTTPS_PROXY,
    KIBERSHCHIT_SOCKS_HOST,
    KIBERSHCHIT_SOCKS_PORT,
    SKIP_TELEGRAM,
    TELEGRAM_PROXY,
    SessionLocal,
    check_connection,
    get_telegram_proxy,
    get_webapp_url,
)
from models import Order, OrderItem, Product, User, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("tea_bot")

MIN_QUANTITY = 1
MAX_QUANTITY = 99
MAX_POSITIONS = 50

# Валидный по формату, но заведомо нерабочий токен. Нужен только для
# локального запуска витрины с SKIP_TELEGRAM=1 без Telegram API.
OFFLINE_BOT_TOKEN = "0:" + "A" * 35


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def format_money(value: Decimal) -> str:
    """Красиво форматирует сумму в рублях."""
    value = value.quantize(Decimal("0.01"))
    if value == value.to_integral_value():
        return f"{int(value):,}".replace(",", " ") + " ₽"
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def format_order_receipt(order: Order, lines: list[str], total: Decimal, note: str = "") -> str:
    """Формирует текст чека о принятом заказе для бота и HTTP API."""
    return (
        "✅ Заказ принят!\n\n"
        f"Номер заказа: #{order.id}\n"
        + "\n".join(lines)
        + f"\n\nИтого: {format_money(total)}{note}\n\n"
        "💳 Оплата: СБП (оплачено, демо)\n"
        "📍 Пункт выдачи: г. Москва, ул. Мясницкая, д. 13, стр. 3\n\n"
        "Мы свяжемся с вами для подтверждения доставки. Спасибо за покупку! 🍵"
    )


def get_webapp_keyboard() -> ReplyKeyboardMarkup | None:
    """Кнопка открытия витрины в клавиатуре ответа.

    sendData() работает только для WebApp, открытого через KeyboardButton с web_app.
    """
    url = get_webapp_url()
    if not url:
        return None
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🍵 Открыть магазин чая",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ],
        resize_keyboard=True,
    )


def upsert_user(session: Session, tg_user: Any) -> User:
    """Создаёт или обновляет пользователя по telegram_id."""
    user = session.get(User, tg_user.id)
    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        session.add(user)
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
    return user


def create_order(
    session: Session,
    tg_user: Any,
    quantities: dict[int, int],
) -> tuple[Order, list[str], Decimal]:
    """Создаёт заказ по ценам из БД. Коммит делает вызывающий код.

    Возвращает (order, строки для ответа, итог). Если товара нет в БД,
    выбрасывает LookupError — заказ не должен сохраняться частично.
    """
    if not quantities:
        raise ValueError("Корзина пуста")

    user = upsert_user(session, tg_user)
    session.flush()

    products = (
        session.query(Product)
        .filter(Product.id.in_(quantities.keys()))
        .all()
    )
    product_map = {product.id: product for product in products}
    missing = [product_id for product_id in quantities if product_id not in product_map]
    if missing:
        raise LookupError(f"Товары не найдены: {missing}")

    order = Order(
        user_id=user.telegram_id,
        total_price=Decimal("0.00"),
        status="new",
    )
    session.add(order)
    session.flush()

    total = Decimal("0.00")
    lines: list[str] = []
    for product_id, quantity in quantities.items():
        product = product_map[product_id]
        unit_price = Decimal(product.price)
        line_total = (unit_price * quantity).quantize(Decimal("0.01"))
        total += line_total
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=quantity,
            )
        )
        lines.append(
            f"• {html.escape(product.name)} × {quantity} = {format_money(line_total)}"
        )

    order.total_price = total.quantize(Decimal("0.01"))
    return order, lines, total


def _parse_positive_int(value: Any) -> int:
    """Строго приводит id/количество к int, не принимая bool и float."""
    if isinstance(value, bool):
        raise ValueError("Некорректный id или количество товара")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError("Некорректный id или количество товара")


def parse_order_payload(payload: Any) -> tuple[dict[int, int], Decimal | None]:
    """Проверяет JSON витрины и возвращает {product_id: quantity} и total.

    Клиентскому total не доверяем: он нужен только для информационного
    сравнения. Количество ограничиваем разумными пределами.
    """
    if not isinstance(payload, dict):
        raise ValueError("JSON заказа должен быть объектом")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Корзина пуста")
    if len(raw_items) > MAX_POSITIONS:
        raise ValueError("Слишком много позиций в заказе")

    quantities: dict[int, int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Позиция заказа должна быть объектом")
        try:
            product_id = _parse_positive_int(raw.get("id"))
            quantity = _parse_positive_int(raw.get("quantity", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректный id или количество товара") from exc

        if product_id <= 0 or quantity <= 0:
            raise ValueError("id и количество должны быть положительными")

        quantity = min(quantity, MAX_QUANTITY)
        quantities[product_id] = min(
            quantities.get(product_id, 0) + quantity,
            MAX_QUANTITY,
        )

    declared_total: Decimal | None = None
    if "total" in payload:
        try:
            declared_total = Decimal(str(payload["total"])).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            declared_total = None
        if declared_total is not None and not declared_total.is_finite():
            declared_total = None

    return quantities, declared_total


# ---------------------------------------------------------------------------
# Хэндлеры
# ---------------------------------------------------------------------------

async def cmd_start(message: Message) -> None:
    """Приветствие, регистрация пользователя и кнопка витрины."""
    tg_user = message.from_user
    if tg_user is None:
        return

    session = SessionLocal()
    try:
        upsert_user(session, tg_user)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Не удалось сохранить пользователя")
        await message.answer("⚠️ Ошибка базы данных. Попробуйте позже.")
        return
    finally:
        session.close()

    greeting = (
        f"Привет, {html.escape(tg_user.first_name or 'гость')}! 🍵\n\n"
        "Добро пожаловать в интернет-магазин китайского чая.\n"
        "Откройте витрину, выберите сорта и оформите заказ."
    )
    keyboard = get_webapp_keyboard()
    if keyboard is None:
        await message.answer(
            greeting
            + "\n\n⚠️ Публичный HTTPS-адрес витрины не настроен.\n"
            "Запустите ngrok и укажите его адрес в BASE_URL, затем перезапустите сервер."
        )
        return

    await message.answer(greeting, reply_markup=keyboard)


async def cmd_help(message: Message) -> None:
    """Короткая справка."""
    await message.answer(
        "🍵 <b>Чайный магазин</b>\n\n"
        "/start — открыть витрину\n"
        "/help — эта справка\n\n"
        "Заказ оформляется в витрине: выберите чай, нажмите «Оформить заказ».\n"
        "Сумма всегда пересчитывается по актуальным ценам из базы."
    )


async def handle_webapp_data(message: Message) -> None:
    """Принимает JSON из Telegram.WebApp.sendData() и создаёт заказ."""
    web_app_data = message.web_app_data
    if web_app_data is None or not web_app_data.data:
        await message.answer("⚠️ Не получил данные из витрины. Попробуйте ещё раз.")
        return

    try:
        payload = json.loads(web_app_data.data)
        quantities, declared_total = parse_order_payload(payload)
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("Некорректные данные из WebApp: %r", web_app_data.data[:300])
        await message.answer(
            "⚠️ Не удалось разобрать заказ. Обновите витрину и попробуйте снова."
        )
        return

    tg_user = message.from_user
    if tg_user is None:
        return

    session = SessionLocal()
    answer = ""
    try:
        order, lines, total = create_order(session, tg_user, quantities)
        session.commit()

        note = ""
        if declared_total is not None and abs(declared_total - total) > Decimal("0.01"):
            note = "\n(сумма пересчитана по актуальным ценам каталога)"

        answer = format_order_receipt(order, lines, total, note)
        logger.info(
            "Создан заказ #%s: user_id=%s, позиций=%s, сумма=%s",
            order.id,
            order.user_id,
            len(quantities),
            total,
        )

    except LookupError as exc:
        session.rollback()
        logger.warning("Заказ не создан: %s", exc)
        await message.answer(
            "⚠️ Часть товаров больше недоступна. Обновите витрину и попробуйте снова."
        )
        return
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Ошибка БД при сохранении заказа")
        await message.answer(
            "⚠️ Техническая ошибка, заказ не сохранён. Попробуйте позже."
        )
        return
    except Exception:
        session.rollback()
        logger.exception("Неожиданная ошибка при сохранении заказа")
        await message.answer(
            "⚠️ Техническая ошибка, заказ не сохранён. Попробуйте позже."
        )
        return
    finally:
        session.close()

    await message.answer(answer)


# ---------------------------------------------------------------------------
# Фабрики и запуск
# ---------------------------------------------------------------------------

def create_bot() -> Bot:
    """Создаёт экземпляр бота с общими настройками."""
    token = BOT_TOKEN
    offline = False
    if not token:
        if not SKIP_TELEGRAM:
            raise RuntimeError(
                "BOT_TOKEN не задан. Откройте .env и вставьте токен от @BotFather."
            )
        logger.warning(
            "BOT_TOKEN не задан, но SKIP_TELEGRAM=1: используется офлайн-заглушка"
        )
        token = OFFLINE_BOT_TOKEN
        offline = True

    session = None
    proxy = "" if offline else get_telegram_proxy()
    if proxy:
        if proxy.startswith("socks") and not _has_aiohttp_socks():
            raise RuntimeError(
                "Для SOCKS-прокси установите пакет aiohttp-socks:\n"
                "  python3.13 -m pip install aiohttp-socks"
            )
        session = AiohttpSession(proxy=proxy)
        if not TELEGRAM_PROXY and not HTTPS_PROXY:
            logger.warning(
                "TELEGRAM_PROXY и HTTPS_PROXY не заданы — использую локальный SOCKS Киберщита: %s",
                proxy,
            )

    return Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _has_aiohttp_socks() -> bool:
    """Проверяет наличие aiohttp-socks без обязательной зависимости."""
    import importlib.util

    return importlib.util.find_spec("aiohttp_socks") is not None


def create_router() -> Router:
    """Создаёт новый роутер с обработчиками.

    Нужен именно новый экземпляр: Aiogram запрещает подключать один и тот же
    Router к двум Dispatcher (это ломало повторную сборку приложения в тестах).
    """
    router = Router()
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    router.message.register(handle_webapp_data, F.web_app_data)
    return router


# Совместимый алиас для внешнего кода, который обращался к bot.router.
router = create_router()


def create_dispatcher() -> Dispatcher:
    """Создаёт диспетчер и подключает к нему свежий роутер."""
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    return dispatcher


async def set_bot_commands(bot: Bot) -> None:
    """Команды в меню Telegram."""
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть магазин"),
            BotCommand(command="help", description="Помощь"),
        ],
        BotCommandScopeDefault(),
    )


async def main() -> None:
    """Запуск в режиме long polling (резервный режим для отладки)."""
    if not check_connection():
        raise SystemExit(1)

    init_db()
    bot = create_bot()
    dispatcher = create_dispatcher()

    try:
        try:
            await set_bot_commands(bot)
        except TelegramNetworkError as exc:
            logger.warning(
                "Не удалось обновить меню команд (%s), продолжаю запуск", exc
            )
        await bot.delete_webhook(drop_pending_updates=True)

        logger.info("Бот запущен в режиме polling")
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
    except TelegramNetworkError as exc:
        logger.error(
            "Telegram недоступен: %s\n"
            "Проверьте, запущен ли Киберщит (локальный SOCKS %s:%s), "
            "или задайте TELEGRAM_PROXY в .env. Затем выполните ./run.sh check.",
            exc,
            KIBERSHCHIT_SOCKS_HOST,
            KIBERSHCHIT_SOCKS_PORT,
        )
        raise SystemExit(1)
