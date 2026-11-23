#!/usr/bin/env bash
# run.sh — быстрый запуск компонентов проекта.
set -euo pipefail
cd "$(dirname "$0")"

usage() {
    cat <<'EOF'
Использование: ./run.sh КОМАНДА

Команды:
  check      — проверить Python, зависимости и подключение к БД
  web        — webhook-бот + витрина на одном порту (нужен HTTPS/ngrok)
  polling    — polling-бот + витрина (если webhook недоступен)
  bot        — только polling-бот, без витрины (отладка)
  dashboard  — Streamlit-дашборд
  db         — создать таблицы и демо-каталог
  test       — запустить автотесты (pytest)
  ngrok      — запустить ngrok на порту из .env

Пример локального запуска:
  ./run.sh check
  ./run.sh web          # терминал 1
  ./run.sh ngrok        # терминал 2
  ./run.sh dashboard    # терминал 3
EOF
}

choose_python() {
    local candidates=("python3.13" "python3")
    # venv используем только если он реально содержит зависимости
    if [ -x ".venv/bin/python" ]; then
        candidates=(".venv/bin/python" "${candidates[@]}")
    fi

    for py in "${candidates[@]}"; do
        if ! command -v "$py" >/dev/null 2>&1 && [ ! -x "$py" ]; then
            continue
        fi
        if ! "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' >/dev/null 2>&1; then
            continue
        fi
        if "$py" -c 'import aiogram, sqlalchemy, aiohttp, dotenv' >/dev/null 2>&1; then
            printf '%s\n' "$py"
            return 0
        fi
    done
    return 1
}

PY="$(choose_python)" || {
    cat >&2 <<'EOF'
Не найден Python 3.13 с зависимостями.

Вариант 1 (системный Python 3.13):
  python3.13 -m pip install -r requirements.txt

Вариант 2 (venv):
  python3.13 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
EOF
    exit 1
}

case "${1:-}" in
    check)
        "$PY" - <<'PY'
import sys
import aiogram
import aiohttp
import pandas
import sqlalchemy
import streamlit

import config

print(f"Python:    {sys.version.split()[0]}")
print(f"aiogram:   {aiogram.__version__}")
print(f"SQLAlchemy:{sqlalchemy.__version__}")
print(f"aiohttp:   {aiohttp.__version__}")
print(f"pandas:    {pandas.__version__}")
print(f"streamlit: {streamlit.__version__}")
print(f"БД:        {config.describe_database()}")
print(f"BOT_TOKEN: {'задан' if config.BOT_TOKEN else 'НЕ ЗАДАН'}")
telegram_proxy = config.get_telegram_proxy()
print(f"Прокси:    {telegram_proxy or 'не задан'}")
print(f"BASE_URL:  {config.get_public_base_url() or 'не найден (нужен ngrok/Replit)'}")
print("Подключение к БД:", "OK" if config.check_connection(verbose=False) else "ОШИБКА")

if not config.BOT_TOKEN:
    print("Telegram API: не проверялся (нет токена)")
else:
    import asyncio
    from bot import create_bot

    async def check_telegram() -> None:
        try:
            bot = create_bot()
        except Exception as exc:
            print(f"Telegram API: НЕ ПРОВЕРЕН ({type(exc).__name__}: {exc})")
            return
        try:
            me = await bot.get_me()
            print("Telegram API: OK, @" + str(me.username))
        except Exception as exc:
            print(f"Telegram API: НЕДОСТУПЕН ({type(exc).__name__})")
            if telegram_proxy:
                print("  Прокси задан, но запрос через него не прошёл")
            else:
                print("  Запустите Киберщит или задайте TELEGRAM_PROXY в .env")
        finally:
            await bot.session.close()

    try:
        asyncio.run(asyncio.wait_for(check_telegram(), timeout=12))
    except TimeoutError:
        print("Telegram API: НЕДОСТУПЕН (таймаут 12 секунд)")
PY
        ;;
    web)
        exec "$PY" server.py
        ;;
    polling)
        MODE=polling exec "$PY" server.py
        ;;
    bot)
        exec "$PY" bot.py
        ;;
    dashboard)
        exec "$PY" -m streamlit run dashboard.py
        ;;
    db)
        "$PY" - <<'PY'
import config
from models import init_db

if not config.check_connection():
    raise SystemExit(1)
init_db()
print("База готова:", config.describe_database())
PY
        ;;
    test)
        if ! "$PY" -c 'import pytest' >/dev/null 2>&1; then
            cat >&2 <<'EOF'
pytest не установлен.
Установите зависимости для тестов:
  .venv/bin/python -m pip install -r requirements-dev.txt
  # или для системного Python:
  python3.13 -m pip install -r requirements-dev.txt
EOF
            exit 1
        fi
        exec "$PY" -m pytest -q "${@:2}"
        ;;
    ngrok)
        if ! command -v ngrok >/dev/null 2>&1; then
            cat >&2 <<'EOF'
ngrok не установлен.

1. Скачайте ngrok: https://ngrok.com/download
2. Распакуйте и положите в PATH, например:
     sudo install -m 755 ngrok /usr/local/bin/ngrok
3. Привяжите токен (со страницы ngrok Dashboard → Your Authtoken):
     ngrok config add-authtoken <ВАШ_ТОКЕН>
4. Запустите:
     ./run.sh ngrok
EOF
            exit 1
        fi
        PORT_VALUE="$("$PY" -c 'from config import PORT; print(PORT)')"
        exec ngrok http "$PORT_VALUE"
        ;;
    ""|-h|--help|help)
        usage
        ;;
    *)
        echo "Неизвестная команда: $1" >&2
        echo >&2
        usage >&2
        exit 1
        ;;
esac
