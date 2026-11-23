from __future__ import annotations

import io
import json

from deploy import pythonanywhere_wsgi as wsgi
from models import init_db


def unknown_update(update_id: int = 1) -> bytes:
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 1, "is_bot": False, "first_name": "Тест"},
                "text": "не команда",
            },
        }
    ).encode()


def call_wsgi(method: str, path: str, body: bytes = b"", headers: dict | None = None):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    environ.update(headers or {})

    result: dict = {}

    def start_response(status: str, response_headers):
        result["status"] = status
        result["headers"] = dict(response_headers)

    payload = b"".join(wsgi.application(environ, start_response))
    return result["status"], result["headers"], payload


def test_wsgi_index():
    status, headers, body = call_wsgi("GET", "/")
    assert status == "200 OK"
    assert headers["Content-Type"].startswith("text/html")
    assert "Китайский чай".encode() in body


def test_wsgi_index_alias():
    status, _, body = call_wsgi("GET", "/index.html")
    assert status == "200 OK"
    assert b"Telegram.WebApp" in body


def test_wsgi_products(seeded_database):
    status, headers, body = call_wsgi("GET", "/api/products")
    assert status == "200 OK"
    assert headers["Content-Type"].startswith("application/json")
    data = json.loads(body)
    assert len(data) == 5
    assert data[0]["id"] == 1


def test_wsgi_health(seeded_database):
    status, _, body = call_wsgi("GET", "/healthz")
    assert status == "200 OK"
    assert json.loads(body)["status"] == "ok"


def test_wsgi_health_error(monkeypatch):
    monkeypatch.setattr(wsgi, "check_connection", lambda verbose=False: False)
    status, _, body = call_wsgi("GET", "/healthz")
    assert status == "503 Service Unavailable"
    assert json.loads(body)["status"] == "error"


def test_wsgi_not_found():
    status, _, body = call_wsgi("GET", "/missing")
    assert status == "404 Not Found"
    assert json.loads(body)["error"] == "not found"


def test_wsgi_method_not_allowed():
    status, _, _ = call_wsgi("POST", "/api/products")
    assert status == "405 Method Not Allowed"
    status, _, _ = call_wsgi("GET", "/webhook")
    assert status == "405 Method Not Allowed"


def test_wsgi_webhook_checks_secret():
    payload = unknown_update()
    status, _, body = call_wsgi(
        "POST",
        "/webhook",
        payload,
        {"CONTENT_TYPE": "application/json"},
    )
    assert status == "403 Forbidden"
    assert json.loads(body)["error"] == "secret"

    status, _, _ = call_wsgi(
        "POST",
        "/webhook",
        payload,
        {
            "CONTENT_TYPE": "application/json",
            "HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": "wrong",
        },
    )
    assert status == "403 Forbidden"


def test_wsgi_webhook_accepts_valid_secret():
    payload = unknown_update()
    status, _, body = call_wsgi(
        "POST",
        "/webhook",
        payload,
        {
            "CONTENT_TYPE": "application/json",
            "HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": "test-webhook-secret",
        },
    )
    assert status == "200 OK"
    assert body == b"ok"


def test_read_body_without_content_length():
    assert wsgi._read_body({"wsgi.input": io.BytesIO(b"data")}) == b""


def test_wsgi_bootstrap_is_idempotent(seeded_database):
    init_db()
    wsgi._bootstrap()
    wsgi._bootstrap()
    assert wsgi._bootstrapped is True
