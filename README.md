<<<<<<< HEAD
# LicenseServerPryamolineynost

FastAPI-сервер лицензирования программного обеспечения. Поддерживает управление организациями, выдачу лицензионных ключей, их активацию и деактивацию клиентскими приложениями.

---

## Быстрый старт

### Локально

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker-compose up --build
```

Перейдите на `http://localhost:8000/init-admin` для создания первого администратора (доступно только если администраторов нет).

---

## Переменные окружения / `config/*.cfg`

| Параметр | По умолчанию | Описание |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/licserver.db` | DSN базы данных |
| `DB_TYPE` | `sqlite` | Тип БД: `sqlite`, `postgres`, `mariadb` |
| `JWT_SECRET` | `CHANGE_ME` | Секрет JWT (обязательно сменить) |
| `API_SECRET` | `CHANGE_ME_API_SECRET` | Секрет HMAC для API (обязательно сменить) |
| `ACCESS_TOKEN_EXPIRES_MIN` | `480` | Время жизни JWT-токена (минуты) |

### Поддерживаемые базы данных (`config/database.cfg`)

```ini
[database]
db_type = sqlite
url     = sqlite+aiosqlite:///./data/licserver.db

# PostgreSQL:
# db_type = postgres
# url     = postgresql+asyncpg://user:pass@host:5432/dbname

# MariaDB/MySQL:
# db_type = mariadb
# url     = mysql+aiomysql://user:pass@host:3306/dbname
=======
# License Server

Сервер управления программными лицензиями на FastAPI + SQLAlchemy (async).

---

## Возможности

- **Публичный JSON API** для активации и переноса лицензий (HMAC-SHA256 подпись)
- **Панель администратора** (`/owner`) — управление клиентами, лицензиями, пользователями
- **Личный кабинет организации** (`/org`) — просмотр и деактивация лицензий
- **Обратная связь** — форма с капчей для анонимных, rate limiting
- **Email-уведомления** — создание лицензий, сброс пароля, обратная связь
- **Резервное копирование** — JSON-дамп всей БД (включая логотипы в BLOB)
- **Аудит** — все действия пишутся в `AuditLog` и `logs/audit.log`
- **CLI** — интерактивный менеджер (`scripts/manage.py`) и сервисные команды (`cli.py`)

---

## Быстрый старт (локально)

### 1. Зависимости

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Конфигурация

Все настройки хранятся в `config/*.cfg`. Скопируйте шаблоны и отредактируйте:

```bash
# config/app.cfg — секретные ключи (обязательно смените!)
[app]
secret_key = CHANGE_ME_PLEASE      # JWT-секрет
api_secret = CHANGE_ME_API_SECRET  # Секрет для подписи API-запросов

# config/smtp.cfg — SMTP для уведомлений (по умолчанию отключён)
[smtp]
enabled = false
host = smtp.gmail.com
port = 587
user = your@email.com
password = your-app-password
from = noreply@yourdomain.com
```

> `config/smtp.cfg` добавлен в `.gitignore` — не коммитьте пароли.

### 3. Первый запуск

```bash
# Создать директории для данных и логов
mkdir -p data logs

# Запустить сервер
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

При первом запуске база данных создаётся автоматически.

Откройте **http://localhost:8000/setup** для создания первого администратора.

После создания панель доступна по адресу **http://localhost:8000/owner/dashboard**.

---

## Запуск через Docker

```bash
# 1. Создать нужные директории
mkdir -p data logs

# 2. Настроить config/app.cfg и config/smtp.cfg

# 3. Собрать и запустить
docker-compose up --build -d

# Просмотр логов
docker-compose logs -f
```

Приложение доступно на **http://localhost:8000**.

---

## CLI-инструменты

### Интерактивный менеджер

```bash
python scripts/manage.py
```

Позволяет управлять администраторами и организациями, просматривать логи
и обслуживать БД прямо из консоли с ANSI-раскраской и ASCII-меню.

### Сервисные команды (cli.py)

```bash
# Резервная копия БД → JSON-файл
python cli.py backup -o backup.json

# Восстановление из файла
python cli.py restore backup.json

# Создать администратора из командной строки
python cli.py create-admin admin@example.com

# Список организаций
python cli.py list-clients

# Инициализировать БД (если не запускать через uvicorn)
python cli.py db-init

# Синхронизировать настройки из config/ в БД
python cli.py sync-settings
>>>>>>> a844ab48249db67e2746f9bde3fc51fb6eff5c90
```

---

<<<<<<< HEAD
## Архитектура

```
app/
  main.py           — FastAPI app, lifespan (migrations, nonce cleanup)
  config.py         — config/*.cfg + env overrides
  db.py             — async SQLAlchemy engine + get_session()
  models.py         — ORM: AdminUser, Client, License, LicenseKey, LicenseAction, …
  security.py       — bcrypt, JWT create/verify, require_owner/require_org
  api_signing.py    — HMAC-SHA256 signature verification
  audit.py          — log_action() → AuditLog table
  email.py          — async email via aiosmtplib
  utils.py          — generate_license_key(), make_qr_png()
  routers/
    public_api.py   — /api/* (JSON, HMAC-protected)
    auth.py         — /login, /logout, /forgot-password, /reset-password/{token}
    owner_web.py    — /owner/* (admin panel, JWT cookie)
    org_web.py      — /org/*   (org cabinet, JWT cookie)
templates/          — Jinja2 HTML (base_owner.html, base_org.html)
static/styles.css   — единый CSS
```

---

## Статусы лицензии

| Статус | В БД | Описание |
|---|---|---|
| `not_activated` | ✓ | Только выпущена, ни разу не активировалась |
| `activated` | ✓ | Активна на устройстве |
| `released` | ✓ | Была активирована, затем деактивирована (свободна для повторной) |
| `blocked` | ✓ | Заблокирована владельцем |
| `expired` | — | Вычисляется по `expires_at`; `blocked` имеет приоритет |

`License.computed_status(now)` возвращает итоговый статус.

---

## Формат ключа

```
XXXX-XXXX-XXXX
```

12 символов (A–Z, 0–9), разделённых тремя блоками. Генерируется через `secrets.choice()`.

---

## Публичный API

Все запросы к `/api/*` защищены HMAC-SHA256. Заголовки:

| Заголовок | Описание |
|---|---|
| `X-Timestamp` | Unix timestamp (секунды UTC, допуск ±30 с) |
| `X-Nonce` | Уникальная строка ≤128 символов (UUID4) |
| `X-Signature` | HMAC-SHA256 hex-дайджест строки подписи |

Строка для подписи:
```
METHOD\nPATH\nTIMESTAMP\nNONCE\nSHA256(BODY)
```

> Отключить проверку подписи (разработка): `api_signing.enabled = false` в `config/security.cfg`

---

### `POST /api/activate`

Активация лицензии на устройстве. Идемпотентна для того же `device_id`.

**Тело:**
```json
{
  "key":          "ABCD-1234-EFGH",
  "device_id":    "уникальный-id-устройства",
  "device_name":  "Ноутбук бухгалтера (необязательно)",
  "comment":      "Произвольный комментарий (необязательно)",
  "activated_at": "2026-01-15T10:30:00 (ISO, необязательно)",
  "key_version":  1
}
```

**Успех 200:**
```json
{
  "status":       "activated",
  "license_id":   42,
  "organization": "ООО Пример",
  "description":  "Рабочая станция",
  "activated_at": "2026-01-15T10:30:00",
  "expires_at":   "2027-01-15T00:00:00",
  "version":      1,
  "device_id":    "уникальный-id-устройства",
  "device_name":  "Ноутбук бухгалтера"
}
```

**Коды ошибок:**

| HTTP | Код | Описание |
|---|---|---|
| 404 | `LICENSE_NOT_FOUND` | Ключ не найден |
| 403 | `LICENSE_BLOCKED` | Лицензия заблокирована |
| 403 | `LICENSE_EXPIRED` | Срок действия истёк |
| 409 | `DEVICE_MISMATCH` | Уже активирована на другом устройстве |
| 409 | `VERSION_MISMATCH` | Устаревшая версия ключа |

---

### `POST /api/deactivate`

Освобождение лицензии с устройства. Статус → `released`.

```json
{ "key": "ABCD-1234-EFGH", "device_id": "уникальный-id-устройства" }
```

**Коды ошибок:** `LICENSE_NOT_FOUND`, `LICENSE_BLOCKED`, `NOT_ACTIVATED`, `DEVICE_MISMATCH`

---

### `POST /api/transfer`

Перенос лицензии: деактивирует текущий ключ, генерирует новый. Вызывается с `device_id` текущего устройства.

```json
{ "key": "ABCD-1234-EFGH", "device_id": "текущий-device-id" }
```

**Успех:** возвращает `new_key` — новый ключ для активации на другом устройстве.

---

### `GET /api/status?key=XXXX-XXXX-XXXX`

Текущее состояние лицензии (только чтение).

---

### `GET /api/history?key=XXXX-XXXX-XXXX`

История ключей и событий лицензии.

```json
{
  "license_id": 42,
  "keys":    [{"key": "...", "is_active": true, "issued_at": "..."}],
  "actions": [{"action": "activate", "at": "...", "actor": "dev-001"}]
=======
## Конфигурация

### config/app.cfg

| Параметр            | По умолчанию           | Описание                         |
|---------------------|------------------------|----------------------------------|
| `secret_key`        | `CHANGE_ME_PLEASE`     | JWT-секрет (обязательно сменить) |
| `api_secret`        | `CHANGE_ME_API_SECRET` | Секрет для HMAC-подписи API      |
| `token_expires_hours` | `8`                  | Время жизни JWT-токена           |

### config/database.cfg

| Параметр | По умолчанию                             | Описание              |
|----------|------------------------------------------|-----------------------|
| `url`    | `sqlite+aiosqlite:///./data/licserver.db` | URL подключения к БД |

### config/security.cfg

| Параметр                    | По умолчанию | Описание                              |
|-----------------------------|--------------|---------------------------------------|
| `max_attempts`              | `5`          | Попыток входа до блокировки           |
| `lockout_minutes`           | `15`         | Время блокировки аккаунта             |
| `attempt_window_minutes`    | `10`         | Окно подсчёта попыток                 |
| `timestamp_tolerance_seconds` | `30`       | Допуск по времени для API-подписи     |

### config/smtp.cfg

| Параметр   | Описание                  |
|------------|---------------------------|
| `enabled`  | `true`/`false`            |
| `host`     | SMTP-хост                 |
| `port`     | Порт (587 для TLS)        |
| `user`     | Логин SMTP                |
| `password` | Пароль SMTP               |
| `from`     | Адрес отправителя         |

Переменные окружения `SMTP_USER` и `SMTP_PASSWORD` переопределяют cfg-файл.

---

## Описание API эндпоинтов

Все запросы к `/api/*` должны быть подписаны HMAC-SHA256.
Документация по алгоритму подписи: [`docs/api_signing.md`](docs/api_signing.md)

### POST /api/activate

Активация лицензионного ключа на устройстве.

```json
// Запрос
{ "key": "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX", "device_id": "unique-device-id" }

// Ответ — успех
{ "status": "ok", "license_id": 42, "expires_at": "2026-12-31T00:00:00" }

// Ответ — ошибка
{ "status": "error", "reason": "Лицензия не найдена", "code": "LICENSE_NOT_FOUND" }
```

**Коды ошибок:** `LICENSE_NOT_FOUND`, `LICENSE_BLOCKED`, `LICENSE_EXPIRED`, `DEVICE_MISMATCH`

### POST /api/transfer

Перенос лицензии на новое устройство (сброс + повторная активация).

```json
// Запрос
{ "key": "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX", "new_device_id": "new-device-id" }

// Ответ — успех
{ "status": "ok", "new_key": "YYYYY-YYYYY-YYYYY-YYYYY-YYYYY" }
```

### GET /api/status?key=XXXXX-...

Проверка статуса лицензии без активации.

```json
{
  "status": "ok",
  "is_blocked": false,
  "is_activated": true,
  "expires_at": "2026-12-31T00:00:00",
  "device_id": "device-123"
>>>>>>> a844ab48249db67e2746f9bde3fc51fb6eff5c90
}
```

---

<<<<<<< HEAD
## Веб-интерфейс

| Раздел | URL | Доступ |
|---|---|---|
| Вход | `/login` | Все |
| Забыли пароль | `/forgot-password` | Все |
| Дашборд владельца | `/owner/dashboard` | AdminUser |
| Клиенты | `/owner/clients` | AdminUser |
| Карточка клиента | `/owner/clients/{id}` | AdminUser |
| Кабинет организации | `/org/dashboard` | Client (org) |
| Feedback org | `/org/feedback` | Client (org) |
| Профиль org | `/org/profile` | Client (org) |
| Backup | `/owner/backup` | AdminUser |
| Аудит-лог | `/owner/logs` | superadmin |

Вход для org: по `login` **или** `contact_email`.

---

## CLI

```bash
python cli.py backup -o backup.json        # резервная копия (JSON)
python cli.py restore backup.json          # восстановление
python cli.py restore backup.json --yes    # без подтверждения
python cli.py create-admin admin@mail.com  # создать администратора
python cli.py list-clients                 # список организаций
python cli.py db-init                      # инициализация БД
python cli.py sync-settings               # синхронизировать настройки из cfg
=======
## Структура проекта

```
├── app/
│   ├── routers/
│   │   ├── auth.py          # /login, /logout, /setup, /forgot-password
│   │   ├── public_api.py    # /api/activate, /api/transfer, /api/status
│   │   ├── owner_web.py     # /owner/** (панель администратора)
│   │   ├── org_web.py       # /org/** (личный кабинет организации)
│   │   └── feedback.py      # /feedback (публичная форма)
│   ├── models.py            # SQLAlchemy ORM-модели
│   ├── config.py            # Загрузка config/*.cfg
│   ├── security.py          # JWT, bcrypt
│   ├── api_signing.py       # HMAC-SHA256 подпись API
│   ├── audit.py             # Аудит-лог
│   └── email.py             # Email-уведомления
├── templates/               # Jinja2 HTML-шаблоны
│   ├── owner/               # Шаблоны панели администратора
│   ├── org/                 # Шаблоны личного кабинета
│   └── email/               # Email-шаблоны
├── static/                  # CSS и прочие статические файлы
├── config/                  # Конфигурационные файлы (*.cfg)
├── scripts/
│   └── manage.py            # Интерактивный TUI-менеджер
├── cli.py                   # Click CLI (backup, restore, create-admin)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
>>>>>>> a844ab48249db67e2746f9bde3fc51fb6eff5c90
```

---

<<<<<<< HEAD
## Тесты

```bash
pip install pytest pytest-asyncio httpx aiosqlite
pytest tests/ -v
```

Тесты используют in-memory SQLite и отключают HMAC-проверку подписи через `dependency_overrides`.

Покрытие:
- `tests/test_api.py` — `/api/activate`, `/api/deactivate`, `/api/transfer`, `/api/status`, `/api/history`
- `tests/test_statuses.py` — переходы статусов лицензии
- `tests/test_auth.py` — вход по login и по contact_email

---

## Хранение данных

Все персистентные данные хранятся в БД (включая логотипы как BLOB). Файловая система не используется для данных приложения, что позволяет полностью восстановить систему из одного JSON-дампа.

```
GET  /owner/backup/download  — скачать дамп
POST /owner/backup/restore   — восстановить из файла
```
=======
## Роли и доступ

| Роль          | Доступ                                                       |
|---------------|--------------------------------------------------------------|
| `superadmin`  | Всё: управление администраторами, журнал аудита, экспорт CSV |
| `admin`       | Управление клиентами и лицензиями                            |
| `org`         | Только свои лицензии (деактивация, обратная связь)           |

---

## Безопасность

- **Пароли** — bcrypt, настраиваемые требования сложности
- **JWT** — HttpOnly cookie, отдельные токены для admin и org
- **API-подпись** — HMAC-SHA256, nonce против replay-атак
- **Brute force** — блокировка по IP и аккаунту
- **Rate limiting** — вход, сброс пароля, обратная связь
- **Все данные в БД** — резервная копия = один JSON-файл
>>>>>>> a844ab48249db67e2746f9bde3fc51fb6eff5c90
