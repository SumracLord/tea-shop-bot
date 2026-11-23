from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def test_run_sh_syntax():
    result = subprocess.run(
        ["bash", "-n", "run.sh"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_run_sh_help():
    result = subprocess.run(
        ["./run.sh", "help"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert "Использование" in result.stdout
    assert "test" in result.stdout


def test_run_sh_unknown_command():
    result = subprocess.run(
        ["./run.sh", "unknown-command"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert "Неизвестная команда" in result.stderr


def test_index_uses_api_and_telegram_webapp():
    html = (PROJECT_DIR / "index.html").read_text(encoding="utf-8")
    assert 'fetch("/api/products"' in html
    assert 'fetch("/api/order"' in html
    assert "Telegram.WebApp" in html
    assert "вне Telegram" not in html
    assert "escapeHtml" in html
    assert "localStorage" in html


def test_index_has_no_hardcoded_catalog():
    html = (PROJECT_DIR / "index.html").read_text(encoding="utf-8")
    assert "Шу Пуэр" not in html
    assert "Те Гуань Инь" not in html


def test_env_example_has_required_keys():
    text = (PROJECT_DIR / ".env.example").read_text(encoding="utf-8")
    for key in (
        "BOT_TOKEN",
        "DATABASE_URL",
        "BASE_URL",
        "WEBHOOK_PATH",
        "WEBHOOK_SECRET",
        "HOST",
        "PORT",
        "MODE",
    ):
        assert f"{key}=" in text


def test_requirements_include_streamlit_with_width_support():
    text = (PROJECT_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert "streamlit>=1.49" in text
