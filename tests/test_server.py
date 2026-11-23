from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.exc import SQLAlchemyError

import server
from models import Order, SessionLocal, init_db


def make_client() -> TestClient:
    return TestClient(TestServer(server.build_application()))


def unknown_update(update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "Тест"},
            "text": "не команда",
        },
    }


@pytest.mark.asyncio
async def test_index_serves_storefront():
    async with make_client() as client:
        response = await client.get("/")
        assert response.status == 200
        assert response.content_type == "text/html"
        text = await response.text()
        assert "Китайский чай" in text
        assert "/api/products" in text


@pytest.mark.asyncio
async def test_index_html_alias():
    async with make_client() as client:
        response = await client.get("/index.html")
        assert response.status == 200
        assert "Telegram.WebApp" in await response.text()
        assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.asyncio
async def test_api_order_saves_and_sends_receipt(monkeypatch, seeded_database):
    send_message = AsyncMock()
    monkeypatch.setattr(Bot, "send_message", send_message)
    monkeypatch.setattr(server, "SKIP_TELEGRAM", True)

    async with make_client() as client:
        server.SKIP_TELEGRAM = False
        response = await client.post(
            "/api/order",
            json={
                "items": [{"id": 1, "quantity": 2}],
                "pickup": "г. Москва, ул. Мясницкая, д. 13, стр. 3",
                "payment": "sbp",
                "telegram_id": 555001,
            },
        )
        assert response.status == 201
        data = await response.json()
        assert data["ok"] is True
        assert data["total"] == 1000.0
        assert data["chat_id"] == 555001

    with SessionLocal() as session:
        order = session.get(Order, data["order_id"])
        assert order is not None
        assert order.total_price == 1000
        assert len(order.items) == 1
        assert order.items[0].quantity == 2

    assert send_message.await_count == 1
    kwargs = send_message.await_args.kwargs
    assert kwargs["chat_id"] == 555001
    assert f"#{data['order_id']}" in kwargs["text"]
    assert "СБП (оплачено, демо)" in kwargs["text"]
    assert "Мясницкая" in kwargs["text"]


@pytest.mark.asyncio
async def test_products_returns_catalog(seeded_database):
    async with make_client() as client:
        response = await client.get("/api/products")
        assert response.status == 200
        assert response.content_type == "application/json"
        data = await response.json()
        assert len(data) == 5
        assert data[0]["id"] == 1
        assert data[0]["name"] == "Шу Пуэр «Лун Чжу»"
        assert data[0]["price"] == 500.0
        assert set(data[0]) == {"id", "name", "description", "price", "image_url"}


@pytest.mark.asyncio
async def test_products_database_error(monkeypatch, seeded_database):
    def broken_loader():
        raise SQLAlchemyError("нет связи")

    monkeypatch.setattr(server, "_load_products", broken_loader)
    async with make_client() as client:
        response = await client.get("/api/products")
        assert response.status == 500
        assert (await response.json())["error"] == "Не удалось загрузить каталог"


@pytest.mark.asyncio
async def test_health_ok(seeded_database):
    async with make_client() as client:
        response = await client.get("/healthz")
        assert response.status == 200
        assert await response.json() == {
            "status": "ok",
            "database": "sqlite",
            "mode": "polling",
        }


@pytest.mark.asyncio
async def test_health_database_error(monkeypatch):
    monkeypatch.setattr(server, "check_connection", lambda verbose=False: False)
    async with make_client() as client:
        response = await client.get("/healthz")
        assert response.status == 503
        assert (await response.json())["status"] == "error"


@pytest.mark.asyncio
async def test_favicon_and_unknown_path():
    async with make_client() as client:
        favicon = await client.get("/favicon.ico")
        assert favicon.status == 204
        missing = await client.get("/no-such-page")
        assert missing.status == 404


@pytest.mark.asyncio
async def test_method_not_allowed():
    async with make_client() as client:
        response = await client.post("/api/products")
        assert response.status == 405
        response = await client.get("/webhook")
        assert response.status == 405


@pytest.mark.asyncio
async def test_polling_mode_disables_webhook():
    async with make_client() as client:
        response = await client.post("/webhook", json=unknown_update())
        assert response.status == 409
        assert "polling" in (await response.json())["error"].lower()


@pytest.mark.asyncio
async def test_webhook_requires_secret(monkeypatch):
    monkeypatch.setattr(server, "MODE", "webhook")
    monkeypatch.setattr(server, "WEBHOOK_SECRET", "secret-for-test")
    async with make_client() as client:
        response = await client.post("/webhook", json=unknown_update())
        assert response.status == 401

        response = await client.post(
            "/webhook",
            json=unknown_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert response.status == 401


@pytest.mark.asyncio
async def test_webhook_accepts_valid_secret(monkeypatch):
    monkeypatch.setattr(server, "MODE", "webhook")
    monkeypatch.setattr(server, "WEBHOOK_SECRET", "secret-for-test")
    async with make_client() as client:
        response = await client.post(
            "/webhook",
            json=unknown_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret-for-test"},
        )
        assert response.status == 200


@pytest.mark.asyncio
async def test_application_can_be_built_twice():
    first = server.build_application()
    second = server.build_application()
    try:
        assert first is not second
        assert len(first.router.routes()) == len(second.router.routes())
    finally:
        await first[server.BOT_KEY].session.close()
        await second[server.BOT_KEY].session.close()


@pytest.mark.asyncio
async def test_offline_application_builds_without_token(monkeypatch):
    # build_application не должен требовать BOT_TOKEN при SKIP_TELEGRAM=1.
    monkeypatch.setattr("bot.BOT_TOKEN", "")
    app = server.build_application()
    try:
        assert app[server.BOT_KEY] is not None
    finally:
        await app[server.BOT_KEY].session.close()
