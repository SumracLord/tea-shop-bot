from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot
import config
from models import Order, OrderItem, Product, SessionLocal, User


def make_user(user_id: int = 900001, username: str | None = "buyer", first_name: str | None = "Иван"):
    return SimpleNamespace(id=user_id, username=username, first_name=first_name)


def make_message(
    *,
    user_id: int = 900001,
    username: str | None = "buyer",
    first_name: str | None = "Иван",
    web_app_data=None,
):
    answers: list[tuple[str, dict]] = []

    async def answer(text: str, **kwargs) -> None:
        answers.append((text, kwargs))

    message = SimpleNamespace(
        from_user=make_user(user_id, username, first_name),
        web_app_data=web_app_data,
        answer=answer,
        answers=answers,
    )
    return message


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("500"), "500 ₽"),
        (Decimal("1234.5"), "1 234,50 ₽"),
        (Decimal("0.01"), "0,01 ₽"),
        (Decimal("1234567.89"), "1 234 567,89 ₽"),
        (Decimal("0"), "0 ₽"),
    ],
)
def test_format_money(value, expected):
    assert bot.format_money(value) == expected


def test_parse_order_valid():
    quantities, total = bot.parse_order_payload(
        {"items": [{"id": 1, "quantity": 2}], "total": 1000}
    )
    assert quantities == {1: 2}
    assert total == Decimal("1000.00")


def test_parse_order_default_quantity_and_strings():
    quantities, total = bot.parse_order_payload(
        {"items": [{"id": "1"}, {"id": "2", "quantity": "3"}], "total": "2250.50"}
    )
    assert quantities == {1: 1, 2: 3}
    assert total == Decimal("2250.50")


def test_parse_order_aggregates_duplicates_and_caps_quantity():
    quantities, _ = bot.parse_order_payload(
        {
            "items": [
                {"id": 1, "quantity": 60},
                {"id": 1, "quantity": 60},
                {"id": 2, "quantity": 200},
            ]
        }
    )
    assert quantities == {1: 99, 2: 99}


def test_parse_order_missing_total():
    quantities, total = bot.parse_order_payload({"items": [{"id": 1, "quantity": 1}]})
    assert quantities == {1: 1}
    assert total is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "заказ",
        {},
        {"items": None},
        {"items": []},
        {"items": "нет"},
        {"items": [None]},
        {"items": [{"quantity": 1}]},
        {"items": [{"id": "x", "quantity": 1}]},
        {"items": [{"id": 1, "quantity": "x"}]},
        {"items": [{"id": 0, "quantity": 1}]},
        {"items": [{"id": -1, "quantity": 1}]},
        {"items": [{"id": 1, "quantity": 0}]},
        {"items": [{"id": 1, "quantity": -2}]},
        {"items": [{"id": True, "quantity": 1}]},
        {"items": [{"id": 1, "quantity": 2.5}]},
        {"items": [{"id": 1.5, "quantity": 1}]},
    ],
)
def test_parse_order_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        bot.parse_order_payload(payload)


def test_parse_order_rejects_too_many_positions():
    payload = {"items": [{"id": 1, "quantity": 1}] * (bot.MAX_POSITIONS + 1)}
    with pytest.raises(ValueError, match="Слишком много позиций"):
        bot.parse_order_payload(payload)


@pytest.mark.parametrize("value", ["abc", None, [], {}, float("nan")])
def test_parse_order_bad_total_becomes_none(value):
    _, total = bot.parse_order_payload(
        {"items": [{"id": 1, "quantity": 1}], "total": value}
    )
    assert total is None


def test_create_order_uses_db_prices(seeded_database):
    session = SessionLocal()
    try:
        order, lines, total = bot.create_order(
            session,
            make_user(),
            {1: 2, 2: 1},
        )
        session.commit()
        assert total == Decimal("1450.00")
        assert order.total_price == Decimal("1450.00")
        assert lines[0].endswith("= 1 000 ₽")
        assert lines[1].endswith("= 450 ₽")
        assert session.query(Order).count() == 1
        assert session.query(OrderItem).count() == 2
        assert session.query(User).count() == 1
    finally:
        session.close()


def test_create_order_rejects_empty_quantities(seeded_database):
    session = SessionLocal()
    try:
        with pytest.raises(ValueError, match="Корзина пуста"):
            bot.create_order(session, make_user(), {})
        assert session.query(Order).count() == 0
    finally:
        session.close()


def test_create_order_rolls_back_on_missing_product(seeded_database):
    session = SessionLocal()
    try:
        with pytest.raises(LookupError, match="999"):
            bot.create_order(session, make_user(900002), {999: 1})
        session.rollback()
        assert session.query(Order).count() == 0
        assert session.query(OrderItem).count() == 0
        assert session.query(User).count() == 0
    finally:
        session.close()


def test_upsert_user_creates_and_updates(seeded_database):
    session = SessionLocal()
    try:
        user = bot.upsert_user(session, make_user(900003, "old", "Старое"))
        session.commit()
        assert user.telegram_id == 900003
        assert user.username == "old"

        updated = bot.upsert_user(session, make_user(900003, "new", "Новое"))
        session.commit()
        assert updated.telegram_id == 900003
        assert updated.username == "new"
        assert updated.first_name == "Новое"
        assert session.query(User).count() == 1
    finally:
        session.close()


def test_get_webapp_keyboard(monkeypatch):
    monkeypatch.setattr(bot, "get_webapp_url", lambda: None)
    assert bot.get_webapp_keyboard() is None

    monkeypatch.setattr(bot, "get_webapp_url", lambda: "https://shop.example/")
    keyboard = bot.get_webapp_keyboard()
    assert keyboard is not None
    button = keyboard.keyboard[0][0]
    assert button.web_app.url == "https://shop.example/"
    assert keyboard.resize_keyboard is True


@pytest.mark.asyncio
async def test_cmd_start_with_public_url(monkeypatch, seeded_database):
    monkeypatch.setattr(bot, "get_webapp_url", lambda: "https://shop.example/")
    message = make_message(user_id=900010, first_name="Анна")
    await bot.cmd_start(message)

    assert len(message.answers) == 1
    text, kwargs = message.answers[0]
    assert "Анна" in text
    assert kwargs["reply_markup"] is not None
    with SessionLocal() as session:
        assert session.get(User, 900010) is not None


@pytest.mark.asyncio
async def test_cmd_start_without_public_url(monkeypatch, seeded_database):
    monkeypatch.setattr(bot, "get_webapp_url", lambda: None)
    message = make_message(user_id=900011)
    await bot.cmd_start(message)

    text, kwargs = message.answers[0]
    assert "HTTPS-адрес" in text
    assert "reply_markup" not in kwargs


@pytest.mark.asyncio
async def test_cmd_help():
    message = make_message()
    await bot.cmd_help(message)
    text, _ = message.answers[0]
    assert "/start" in text
    assert "/help" in text


@pytest.mark.asyncio
async def test_handle_webapp_data_creates_order_and_recalculates(seeded_database):
    payload = {"items": [{"id": 1, "quantity": 1}], "total": 1}
    message = make_message(
        user_id=900020,
        web_app_data=SimpleNamespace(data=json.dumps(payload)),
    )
    await bot.handle_webapp_data(message)

    text, _ = message.answers[0]
    assert "Заказ принят" in text
    assert "сумма пересчитана" in text
    assert "500 ₽" in text

    with SessionLocal() as session:
        order = session.query(Order).one()
        assert order.user_id == 900020
        assert order.total_price == Decimal("500.00")
        assert session.query(OrderItem).one().quantity == 1


@pytest.mark.asyncio
async def test_handle_webapp_data_without_recalculation(seeded_database):
    payload = {"items": [{"id": 1, "quantity": 1}], "total": 500}
    message = make_message(
        user_id=900021,
        web_app_data=SimpleNamespace(data=json.dumps(payload)),
    )
    await bot.handle_webapp_data(message)

    text, _ = message.answers[0]
    assert "сумма пересчитана" not in text


@pytest.mark.asyncio
async def test_handle_webapp_data_malformed_json(seeded_database):
    message = make_message(web_app_data=SimpleNamespace(data="{не json"))
    await bot.handle_webapp_data(message)
    text, _ = message.answers[0]
    assert "Не удалось разобрать заказ" in text
    with SessionLocal() as session:
        assert session.query(Order).count() == 0


@pytest.mark.asyncio
async def test_handle_webapp_data_empty(seeded_database):
    message = make_message(web_app_data=None)
    await bot.handle_webapp_data(message)
    text, _ = message.answers[0]
    assert "Не получил данные" in text


@pytest.mark.asyncio
async def test_handle_webapp_data_missing_product(seeded_database):
    payload = {"items": [{"id": 999, "quantity": 1}]}
    message = make_message(
        user_id=900022,
        web_app_data=SimpleNamespace(data=json.dumps(payload)),
    )
    await bot.handle_webapp_data(message)
    text, _ = message.answers[0]
    assert "больше недоступна" in text
    with SessionLocal() as session:
        assert session.query(Order).count() == 0


@pytest.mark.asyncio
async def test_handle_webapp_data_without_user(seeded_database):
    payload = {"items": [{"id": 1, "quantity": 1}]}
    message = make_message(web_app_data=SimpleNamespace(data=json.dumps(payload)))
    message.from_user = None
    await bot.handle_webapp_data(message)

    assert message.answers == []
    with SessionLocal() as session:
        assert session.query(Order).count() == 0


def test_create_bot_requires_token(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", "")
    monkeypatch.setattr(bot, "SKIP_TELEGRAM", False)
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        bot.create_bot()


@pytest.mark.asyncio
async def test_create_bot_offline_without_token(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", "")
    monkeypatch.setattr(bot, "SKIP_TELEGRAM", True)
    instance = bot.create_bot()
    try:
        assert instance.token == bot.OFFLINE_BOT_TOKEN
    finally:
        await instance.session.close()


def test_create_bot_rejects_missing_socks_support(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setattr(bot, "SKIP_TELEGRAM", False)
    monkeypatch.setattr(
        bot, "get_telegram_proxy", lambda: "socks5://127.0.0.1:1080"
    )
    monkeypatch.setattr(bot, "_has_aiohttp_socks", lambda: False)
    with pytest.raises(RuntimeError, match="aiohttp-socks"):
        bot.create_bot()


@pytest.mark.asyncio
async def test_create_bot_uses_autodetected_proxy(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setattr(bot, "SKIP_TELEGRAM", False)
    monkeypatch.setattr(bot, "TELEGRAM_PROXY", "")
    monkeypatch.setattr(
        bot, "get_telegram_proxy", lambda: "socks5://127.0.0.1:10808"
    )
    instance = bot.create_bot()
    try:
        assert instance.session.proxy == "socks5://127.0.0.1:10808"
    finally:
        await instance.session.close()


def _fake_socket_factory(connect_result: int):
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def settimeout(self, timeout):
            assert timeout == 0.5

        def connect_ex(self, address):
            assert address == (
                config.KIBERSHCHIT_SOCKS_HOST,
                config.KIBERSHCHIT_SOCKS_PORT,
            )
            return connect_result

    return lambda *args, **kwargs: FakeSocket()


def test_get_telegram_proxy_prefers_explicit_value(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setattr(config, "TELEGRAM_PROXY_AUTODETECT", True)
    monkeypatch.setattr(config, "SKIP_TELEGRAM", False)
    assert config.get_telegram_proxy() == "http://127.0.0.1:8080"


def test_get_telegram_proxy_autodetects_kibershchit(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_PROXY", "")
    monkeypatch.setattr(config, "TELEGRAM_PROXY_AUTODETECT", True)
    monkeypatch.setattr(config, "SKIP_TELEGRAM", False)
    monkeypatch.setattr(config.socket, "socket", _fake_socket_factory(0))
    assert config.get_telegram_proxy() == "socks5://127.0.0.1:10808"


def test_get_telegram_proxy_skips_closed_port(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_PROXY", "")
    monkeypatch.setattr(config, "TELEGRAM_PROXY_AUTODETECT", True)
    monkeypatch.setattr(config, "SKIP_TELEGRAM", False)
    monkeypatch.setattr(config.socket, "socket", _fake_socket_factory(111))
    assert config.get_telegram_proxy() == ""


def test_get_telegram_proxy_skips_offline_mode(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_PROXY", "")
    monkeypatch.setattr(config, "TELEGRAM_PROXY_AUTODETECT", True)
    monkeypatch.setattr(config, "SKIP_TELEGRAM", True)
    assert config.get_telegram_proxy() == ""


@pytest.mark.asyncio
async def test_main_survives_menu_error_and_closes_session(monkeypatch):
    closed = False

    class FakeSession:
        async def close(self):
            nonlocal closed
            closed = True

    fake_bot = SimpleNamespace(
        session=FakeSession(),
        delete_webhook=AsyncMock(),
    )
    fake_dispatcher = SimpleNamespace(
        start_polling=AsyncMock(),
        resolve_used_update_types=lambda: [],
    )

    async def fail_commands(_):
        raise bot.TelegramNetworkError(
            method=SimpleNamespace(), message="timeout"
        )

    monkeypatch.setattr(bot, "check_connection", lambda: True)
    monkeypatch.setattr(bot, "init_db", lambda: None)
    monkeypatch.setattr(bot, "create_bot", lambda: fake_bot)
    monkeypatch.setattr(bot, "create_dispatcher", lambda: fake_dispatcher)
    monkeypatch.setattr(bot, "set_bot_commands", fail_commands)

    await bot.main()

    assert closed is True
    fake_bot.delete_webhook.assert_awaited_once()
    fake_dispatcher.start_polling.assert_awaited_once()


def test_create_dispatcher_can_be_called_twice():
    first = bot.create_dispatcher()
    second = bot.create_dispatcher()
    assert first is not second
    assert len(first.sub_routers) == 1
    assert len(second.sub_routers) == 1
    assert first.sub_routers[0] is not second.sub_routers[0]


@pytest.mark.asyncio
async def test_set_bot_commands():
    fake_bot = SimpleNamespace(set_my_commands=AsyncMock())
    await bot.set_bot_commands(fake_bot)
    fake_bot.set_my_commands.assert_awaited_once()
    commands = fake_bot.set_my_commands.await_args.args[0]
    assert [command.command for command in commands] == ["start", "help"]
