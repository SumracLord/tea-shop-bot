# 🍵 Telegram-бот «Чайный магазин»

Демонстрационный интернет-магазин «цифровых» китайских чаёв:

* **Aiogram 3** — Telegram-бот;
* **Telegram WebApp** — витрина `index.html`, открывается кнопкой в боте;
* **webhook + ngrok** — основной режим приёма обновлений (есть резервный polling);
* **SQLAlchemy 2** — ORM;
* **SQLite** — локальное демо без внешней БД;
* **Supabase PostgreSQL** — облачный вариант через Session pooler;
* **Streamlit** — статистика продаж.

Стек рассчитан на **Python 3.13**.

---

## Как это работает

```
Telegram
   │  /start, WebAppData
   ▼
server.py (aiohttp, порт 8000)
   ├── GET  /             → index.html (витрина)
   ├── GET  /api/products → каталог из БД
   ├── POST /webhook      → обновления Telegram
   └── GET  /healthz      → проверка БД
   │
   ├── bot.py — хэндлеры Aiogram
   ├── models.py — users/products/orders/order_items
   └── config.py — SQLite или Supabase
   │
   ▼
dashboard.py (Streamlit, порт 8501)
```

1. Пользователь нажимает `/start` — бот сохраняет его в `users` и присылает
   кнопку WebApp с публичным HTTPS-адресом.
2. В витрине покупатель добавляет чай в корзину. Каталог и цены приходят
   из `/api/products`, поэтому данные не расходятся с БД.
3. Кнопка «Оформить заказ» вызывает `Telegram.WebApp.sendData(JSON)`.
4. Бот получает `web_app_data`, **заново читает цены из БД**, пересчитывает
   сумму и записывает `orders` + `order_items`.
5. Streamlit читает те же таблицы и строит графики.

Клиентская сумма `total` — только для сравнения. Бот ей не доверяет.

---

## Файлы проекта

| Файл | Назначение |
|---|---|
| `config.py` | `.env`, выбор SQLite/Postgres, движок SQLAlchemy, публичный URL |
| `models.py` | модели таблиц, создание таблиц и демо-каталога |
| `bot.py` | хэндлеры `/start`, `/help`, `web_app_data`; запуск polling |
| `server.py` | aiohttp: витрина, API, webhook, healthz; webhook/polling режимы |
| `index.html` | Telegram WebApp: каталог, корзина, отправка заказа |
| `dashboard.py` | Streamlit-аналитика продаж |
| `run.sh` | команды запуска |
| `tests/` | автотесты pytest (БД, бот, HTTP, WSGI, дашборд, статика) |
| `requirements.txt` / `requirements-dev.txt` / `pyproject.toml` | зависимости и настройки pytest |
| `.env.example` | шаблон настроек; реальный `.env` не коммитится |
| `replit.nix`, `.python-version`, `.replit`, `start_cloud.py` | окружение и запуск в Replit, фиксация Python 3.13 |
| `deploy/pythonanywhere_wsgi.py` | WSGI-адаптер для PythonAnywhere |

---

## Быстрый старт (локально, SQLite)

### 1. Python и зависимости

Проверьте версию:

```bash
python3.13 --version
```

Если зависимости ещё не установлены:

```bash
python3.13 -m pip install -r requirements.txt
```

Для автотестов установите dev-зависимости:

```bash
python3.13 -m pip install -r requirements-dev.txt
```

Можно использовать venv:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`run.sh` сам выберет `.venv`, если в нём есть нужные пакеты, иначе
возьмёт системный `python3.13`.

### 2. Настройки

Скопируйте шаблон и вставьте токен:

```bash
cp .env.example .env
```

Минимально в `.env` нужно заполнить:

```ini
BOT_TOKEN=123456789:ВАШ_ТОКЕН_ОТ_BOTFATHER
DATABASE_URL=sqlite:///tea_shop.db
BASE_URL=
```

`BASE_URL` можно оставить пустым — при запуске с ngrok адрес определится
автоматически. Файл `.env` уже в `.gitignore`.

Если провайдер блокирует `api.telegram.org` (ошибка `Network is unreachable`
или таймаут), запустите Киберщит: он поднимает локальный SOCKS5 на
`127.0.0.1:10808`, и бот подхватит его автоматически. Явно задать прокси
можно так:

```ini
TELEGRAM_PROXY=socks5://127.0.0.1:10808
```

Автоподхват отключается через `TELEGRAM_PROXY_AUTODETECT=0`. Для
`socks5://` используется `aiohttp-socks` — он уже входит в зависимости.
Проверить доступность Telegram можно командой `./run.sh check`.

### 3. Проверка

```bash
./run.sh check
```

Команда покажет версии, тип БД и доступность базы, не выводя пароль.

Создать таблицы и демо-каталог вручную:

```bash
./run.sh db
```

Запустить автотесты (изолированная временная SQLite-БД, без Telegram):

```bash
./run.sh test
```

### 4. ngrok и webhook

Установите [ngrok](https://ngrok.com/download), затем один раз привяжите токен:

```bash
ngrok config add-authtoken <ВАШ_AUTHTOKEN>
```

Запустите туннель на порт `8000`:

```bash
./run.sh ngrok
```

В другом терминале:

```bash
./run.sh web
```

Порядок важен: ngrok должен быть запущен до `web`, чтобы сервер нашёл
HTTPS-адрес. Если адрес не подхватился, скопируйте его из ngrok в `.env`:

```ini
BASE_URL=https://xxxx-xx-xx-xx-xx.ngrok-free.app
```

и перезапустите `./run.sh web`.

В логе должно появиться:

```
Webhook установлен: https://....ngrok-free.app/webhook
```

Проверить состояние webhook можно в браузере:

```
https://api.telegram.org/bot<ТОКЕН>/getWebhookInfo
```

### 5. Заказ

1. Напишите боту `/start`.
2. Нажмите «🍵 Открыть магазин чая».
3. Добавьте товары и нажмите «Оформить заказ».
4. Бот ответит номером заказа и составом.
5. Запустите дашборд:

```bash
./run.sh dashboard
```

Откройте `http://localhost:8501`.

> WebApp открывается только внутри Telegram и только по HTTPS.
> В обычном браузере витрина тоже откроется, но оформить заказ нельзя —
> `Telegram.WebApp.sendData` работает только в Telegram.

### Быстрая проверка витрины без Telegram

Если нужно посмотреть интерфейс без бота и ngrok:

```bash
SKIP_TELEGRAM=1 ./run.sh web
```

`BOT_TOKEN` в этом режиме не обязателен: сервер подставит офлайн-заглушку.
Откройте `http://localhost:8000`. Каталог и корзина работают, оформление
заказа отключено.

---

## Режимы запуска

| Команда | Что делает |
|---|---|
| `./run.sh web` | webhook + витрина на одном порту (основной режим) |
| `./run.sh polling` | polling + витрина; нужен HTTPS для кнопки WebApp |
| `./run.sh bot` | только polling-бот, без витрины (отладка) |
| `./run.sh dashboard` | Streamlit |
| `./run.sh db` | создать таблицы и демо-каталог |
| `./run.sh test` | запустить pytest на временной БД |
| `./run.sh check` | проверить окружение и БД |
| `./run.sh ngrok` | запустить ngrok на порту из `.env` |

### Webhook или polling

**Webhook** — Telegram сам отправляет обновления на `/webhook`. Нужен
публичный HTTPS. В проекте один туннель обслуживает и витрину, и webhook.

**Polling** — бот сам опрашивает Telegram. Удобно для отладки, но для
кнопки WebApp всё равно нужен HTTPS-адрес витрины (ngrok).

Подробнее о подходе:

* [Telegram Боты на Aiogram 3.x: Простой бот на вебхуках с локальным запуском и деплоем](https://habr.com/ru/companies/amvera/articles/836032/) — основа текущей реализации (aiogram + aiohttp + ngrok);
* [Как просто создать aiogram 3.x бота на вебхуках (webhook)?](https://habr.com/ru/articles/819955/) — альтернативный вариант с FastAPI.

---

## Supabase (PostgreSQL)

Локально проект работает на SQLite. Чтобы перейти на Supabase:

1. Создайте проект на [supabase.com](https://supabase.com/) и дождитесь
   его запуска (приостановленный проект нужно разбудить).
2. Откройте **Project Settings → Database → Connection string → Session pooler**.
3. Возьмите URI вида:

   ```
   postgresql://postgres.<ref>:<пароль>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

4. Замените драйвер на `psycopg2` и запишите в `.env`:

   ```ini
   DATABASE_URL=postgresql+psycopg2://postgres.<ref>:<пароль>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

5. Создайте таблицы и демо-каталог:

   ```bash
   ./run.sh db
   ```

6. Перезапустите `web` и `dashboard`.

Почему именно **Session pooler**:

* прямой хост `db.<ref>.supabase.co` обычно имеет только IPv6 и из многих
  сетей недоступен;
* имя пользователя pooler — `postgres.<ref>`, а не просто `postgres`;
* порт — `5432`.

`config.py` специально **запрещает** прямой `db.<ref>.supabase.co` и
подсказывает правильный формат. Если появилась ошибка
`tenant/user ... not found`, проверьте ref проекта, регион и то, что
проект не приостановлен.

---

## Streamlit-дашборд

```bash
./run.sh dashboard
```

Что показывает:

* общую выручку, число заказов и средний чек;
* выручку и количество заказов по дням;
* популярность сортов (донат + горизонтальные бары);
* таблицу последних заказов.

Кэш — 30 секунд. Кнопка «🔄 Обновить данные» в боковой панели сбрасывает
кэш сразу. Бот и дашборд должны смотреть в одну и ту же БД.

---

## Автотесты

```bash
./run.sh test
```

Тесты используют pytest и pytest-asyncio. Перед импортом проекта
`tests/conftest.py` переключает `DATABASE_URL` на временный SQLite-файл,
включает `SKIP_TELEGRAM=1` и подставляет тестовый токен, поэтому рабочая
`tea_shop.db` и Telegram API не затрагиваются.

Что покрыто:

* нормализация URL и маскировка паролей (`config.py`);
* модели, seed каталога и каскадное удаление;
* парсинг корзины, пересчёт цен, откат при ошибках и обработчики бота;
* HTTP-маршруты aiohttp, health-check и проверка webhook-секрета;
* WSGI-адаптер PythonAnywhere;
* smoke-тест Streamlit-дашборда через `AppTest`;
* статические проверки `index.html` и `run.sh`.

---

## Replit

1. Импортируйте проект в Replit (Python Repl).
2. Файлы `.python-version` и `replit.nix` уже фиксируют Python 3.13.
3. Добавьте секреты в **Secrets**: `BOT_TOKEN`, `DATABASE_URL`,
   при необходимости `WEBHOOK_SECRET`. `BASE_URL` на Replit можно не
   задавать: адрес определится из `REPLIT_DEV_DOMAIN`.
4. В Shell установите зависимости:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

5. Нажмите **Run**: `.replit` запустит `start_cloud.py`, который поднимет
   `server.py` в режиме `webhook` на порту из переменной `PORT` (или `8000`)
   и Streamlit-дашборд на порту `8501` с адресом `0.0.0.0`.

6. В настройках Replit сделайте публичным порт из переменной `PORT`
   (обычно `8080`; локально по умолчанию `8000`). `BASE_URL` подхватится
   автоматически из `REPLIT_DEV_DOMAIN`; если нет — задайте его в Secrets.

На Replit webview обычно один, но оба процесса запускаются одновременно:
витрина и webhook доступны на основном порту, дашборд — на `8501`.

---

## PythonAnywhere

Бесплатный тариф PythonAnywhere не запускает aiohttp-сервер на произвольном
порту. Для него есть WSGI-адаптер `deploy/pythonanywhere_wsgi.py`:
он отдаёт витрину, API и принимает `/webhook` через WSGI.

1. Загрузите проект (git clone или Files).
2. Установите зависимости:

   ```bash
   python3.13 -m pip install --user -r requirements.txt
   ```

3. В `.env` задайте:

   ```ini
   BASE_URL=https://<логин>.pythonanywhere.com
   WEBHOOK_SECRET=<постоянная случайная строка>
   MODE=webhook
   ```

   `WEBHOOK_SECRET` обязателен: у PythonAnywhere может быть несколько
   WSGI-процессов, и секрет должен быть одинаковым.

4. В Web-приложении укажите WSGI-файл:

   ```
   /home/<логин>/tea-shop/deploy/pythonanywhere_wsgi.py
   ```

   Либо оставьте стандартный WSGI-файл и добавьте в него:

   ```python
   import sys
   sys.path.insert(0, "/home/<логин>/tea-shop")
   from deploy.pythonanywhere_wsgi import application
   ```

5. Нажмите **Reload** и проверьте:

   ```
   https://<логин>.pythonanywhere.com/healthz
   ```

Ограничения:

* PythonAnywhere может блокировать исходящие запросы к `api.telegram.org`;
  в этом случае добавьте домен в whitelist или используйте Replit/платный
  always-on task;
* Streamlit на бесплатном PythonAnywhere не запускается (только WSGI);
* SQLite-файл должен лежать в постоянной папке проекта.

---

## Безопасность

* `.env` содержит токен бота и пароль БД — он в `.gitignore`.
* В логах и ошибках пароль БД маскируется.
* `WEBHOOK_SECRET` проверяется по заголовку
  `X-Telegram-Bot-Api-Secret-Token`.
* Бот никогда не доверяет сумме из браузера: цена и итог пересчитываются
  по `products` в БД.
* Не публикуйте `.env`, `tea_shop.db` и дампы БД в репозитории.

---

## Частые проблемы

**`НЕТ ПУБЛИЧНОГО HTTPS-АДРЕСА`**

Запустите ngrok до `web`:

```bash
./run.sh ngrok
./run.sh web
```

Либо впишите `BASE_URL=https://...` в `.env`.

**ngrok выдал новый адрес**

Бесплатный ngrok меняет адрес после перезапуска. Перезапустите `web`
(адрес подхватится автоматически) или обновите `BASE_URL`.

**`tenant/user ... not found`**

Проверьте ref Supabase, регион и формат пользователя
`postgres.<ref>`. Убедитесь, что проект не приостановлен.

**`base_url` определился как HTTP**

Telegram принимает только HTTPS. Используйте ngrok/Replit либо
HTTPS-домен PythonAnywhere.

**`api.telegram.org` недоступен / `Network is unreachable`**

Провайдер или сеть блокирует Telegram. `./run.sh check` покажет
`Telegram API: НЕДОСТУПЕН`. Решения:

* запустите Киберщит — его локальный SOCKS5 `127.0.0.1:10808` бот
  подхватит автоматически;
* либо задайте прокси явно в `.env`:

  ```ini
  TELEGRAM_PROXY=socks5://127.0.0.1:10808
  ```

* автоподхват отключается через `TELEGRAM_PROXY_AUTODETECT=0`;
* для SOCKS-прокси используется `aiohttp-socks` (уже в зависимостях).

Если блокируется и `telegram.org`, скрипт WebApp в `index.html` не
загрузится — витрина не откроется даже внутри Telegram. VPN/прокси должен
покрывать и браузер Telegram.

**Заказ не появляется в дашборде**

Кэш Streamlit — 30 секунд. Нажмите «🔄 Обновить данные». Убедитесь, что
бот и дашборд используют один и тот же `DATABASE_URL`.

**SQLite: `database is locked`**

Не запускайте несколько ботов одновременно на одном файле. Для демо
достаточно одного процесса `web`. WAL и `busy_timeout` уже включены.

**Картинки не загружаются**

Витрина использует внешние изображения. Если сеть их блокирует, карточки
показывают зелёный плейсхолдер — на заказ это не влияет.

---

## Проверка проекта

```bash
python3.13 -m py_compile config.py models.py bot.py server.py dashboard.py deploy/pythonanywhere_wsgi.py
./run.sh test
./run.sh check
./run.sh db
SKIP_TELEGRAM=1 ./run.sh web   # затем: curl http://localhost:8000/healthz
```

Полная проверка с Telegram требует действующего `BOT_TOKEN`, доступа к
`api.telegram.org` и HTTPS-туннеля (ngrok или Replit).
