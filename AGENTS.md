# AGENTS.md

## What this is
Telegram tea-shop demo: Aiogram 3 bot + Telegram WebApp storefront + aiohttp
webhook server + Streamlit analytics. Python 3.13 is mandatory (`config.py`
exits on older interpreters). Code comments, README, and all user-facing
strings are Russian; keep new text Russian.

## Workspace layout
The project lives directly in `/home/horus/Документы/studiren/prog`.
Old `workspace.tar(*)` directories were deleted to Trash and are **not**
part of the project; do not restore or edit them.

Key files:
- `config.py` — `.env`, SQLite/Postgres engine, public URL detection.
- `models.py` — `User`, `Product`, `Order`, `OrderItem`, `init_db()`.
- `bot.py` — Aiogram handlers and polling entrypoint.
- `server.py` — aiohttp: `/`, `/api/products`, `/webhook`, `/healthz`.
- `index.html` — WebApp storefront; fetches catalog from `/api/products`.
- `dashboard.py` — Streamlit sales analytics.
- `run.sh` — `check|web|polling|bot|dashboard|db|test|ngrok`.
- `deploy/pythonanywhere_wsgi.py` — optional WSGI adapter.
- `tests/` — pytest suite (config/models/bot/server/WSGI/dashboard/static).

## Run / verify
From the project root:
```bash
python3.13 -m py_compile config.py models.py bot.py server.py dashboard.py deploy/pythonanywhere_wsgi.py
./run.sh check
./run.sh db
./run.sh web        # webhook + storefront on :8000 (needs ngrok/HTTPS)
./run.sh polling    # polling + storefront
./run.sh bot        # polling only
./run.sh dashboard  # Streamlit on :8501
./run.sh test       # pytest, isolated temp SQLite DB, no Telegram calls
```
- pytest + pytest-asyncio are the only test tooling; no linter, typechecker, or CI configured. Install with `python3.13 -m pip install -r requirements-dev.txt`.
- System `python3.13` has aiogram/SQLAlchemy/Streamlit/pandas/psycopg2/altair/aiohttp/dotenv; `.venv` is now synced with `requirements.txt` and dev dependencies.
- `run.sh` checks core imports and falls back to system `python3.13`.
- `SKIP_TELEGRAM=1 ./run.sh web` serves the storefront/API without calling Telegram and works without `BOT_TOKEN` (uses an offline dummy token).
- `./run.sh db` is the manual way to create tables/seed; `server.py` and `bot.py` also call `init_db()` at startup.
- `tests/conftest.py` sets `DATABASE_URL` to a temporary SQLite file, `SKIP_TELEGRAM=1`, a dummy token, and `MODE=polling` **before** importing project modules. Never let tests touch `tea_shop.db` or Telegram.

## Environment / DB gotchas
- `.env` contains live secrets and is gitignored; never print, document, or commit it. Do not log `DATABASE_URL` passwords; `config.mask_database_url()` exists for this.
- Direct access to `api.telegram.org` is blocked on this machine (IPv6 has no global route, IPv4 times out). `Киберщит` runs a local Xray SOCKS5 on `127.0.0.1:10808`; `config.get_telegram_proxy()` uses explicit `TELEGRAM_PROXY` or auto-detects that port. Auto-detection is off when `SKIP_TELEGRAM=1` or `TELEGRAM_PROXY_AUTODETECT=0`. `./run.sh check` should show `Telegram API: OK`.
- Default local DB is SQLite at `tea_shop.db` (path resolved relative to the project dir). WAL, foreign keys, and busy timeout are enabled.
- `DATABASE_URL` for Supabase must use the **Session pooler** (`aws-0-<region>.pooler.supabase.com:5432`, user `postgres.<ref>`). The direct `db.<ref>.supabase.co` host is IPv6-only; `config.py` rejects it with a clear error.
- Driver prefix must be `postgresql+psycopg2://`; `config.py` normalizes common malformed Postgres URLs.
- The previously configured Supabase project answered `tenant/user not found` even though TCP 5432 was open. Do not assume live Supabase works; verify with `./run.sh check`. A new/unpaused Supabase project may be needed.
- `init_db()` creates tables and seeds 5 teas only when `products` is empty. `DEMO_PRODUCTS` must not contain explicit `id` values: on PostgreSQL explicit IDs do not advance the sequence. Product IDs are stable in a fresh DB; the storefront fetches the catalog dynamically, so it does not hard-code IDs.

## Architecture and invariants
- `config.py`: single `engine`, `SessionLocal`, `Base`; every module imports them from here. `.env` is loaded at import time from the project directory.
- `bot.py`: `/start` upserts the user and sends a WebApp button; `F.web_app_data` parses JSON, **re-fetches prices from the DB**, and writes `orders` + `order_items`. The client `total` is advisory only — never trust it. `create_router()` returns a fresh Router for every `create_dispatcher()`; do not attach the module-level router directly (Aiogram forbids reusing one Router in several Dispatchers).
- `server.py`: one aiohttp app on `HOST:PORT` (default `0.0.0.0:8000`) serves both the storefront and the webhook, so one ngrok tunnel is enough. `MODE=webhook` is default; `MODE=polling` starts polling plus the storefront. The webhook handler is synchronous (`handle_in_background=False`) so Telegram retries failed order processing.
- `config.get_public_base_url()` checks `BASE_URL`/`WEBAPP_URL`, then `REPLIT_DEV_DOMAIN`, then the local ngrok API at `127.0.0.1:4040` (5-second cache). `BASE_URL` must be HTTPS for Telegram.
- `WEBHOOK_SECRET` is sent via `X-Telegram-Bot-Api-Secret-Token`. If empty, `server.py` generates an ephemeral one; PythonAnywhere requires a fixed value in `.env`.
- `index.html` gets products from `/api/products`; do not reintroduce a hard-coded catalog.
- `dashboard.py`: raw SQL via `pandas.read_sql`. `@st.cache_data(ttl=30)` means new orders can take up to 30 seconds unless the "Обновить данные" button is pressed. Launch through Streamlit, not `python dashboard.py`. Use Altair for charts (not `st.pie_chart`).
- Synchronous SQLAlchemy inside async Aiogram handlers is intentional; keep it. aiohttp handlers use `asyncio.to_thread` only for DB reads/health.
- `deploy/pythonanywhere_wsgi.py` is a WSGI adapter for PythonAnywhere: it uses one persistent event loop and `Update.model_validate(...)` + `dispatcher.feed_update(...)`. Its routes and webhook secret checks are covered locally by `tests/test_wsgi.py`; a real PythonAnywhere account is still needed for deployment testing.

## Deployment notes
- Local: ngrok must run before `./run.sh web` for auto-detection; otherwise set `BASE_URL` in `.env`.
- Replit: `.python-version` + `replit.nix`; set secrets in Replit, expose port 8000, run `python3 server.py`. `REPLIT_DEV_DOMAIN` is auto-detected.
- PythonAnywhere: free tier is WSGI-only; use `deploy/pythonanywhere_wsgi.py`, set a fixed `WEBHOOK_SECRET` and `BASE_URL=https://<user>.pythonanywhere.com`. Outbound `api.telegram.org` may need whitelisting; Streamlit is not supported on the free tier.
- Telegram WebApp works only inside Telegram and only over HTTPS.
- Habr references: https://habr.com/ru/companies/amvera/articles/836032/ (aiogram 3 + aiohttp + ngrok) and https://habr.com/ru/articles/819955/ (FastAPI alternative).
