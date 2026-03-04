# License Server

Сервер управления программными лицензиями на FastAPI + SQLAlchemy (async).

---

## Возможности

- **Публичный JSON API** — активация и перенос лицензий (HMAC-SHA256)
- **Панель администратора** (`/owner`) — управление клиентами, лицензиями, пользователями, резервные копии
- **Личный кабинет организации** (`/org`) — просмотр, сброс, история лицензий
- **Email-уведомления** — создание лицензий, сброс пароля, обратная связь
- **Аудит** — все действия пишутся в `AuditLog` и `logs/audit.log`
- **Резервное копирование** — JSON-дамп всей БД (включая логотипы в BLOB)
- **CLI** — интерактивный менеджер (`scripts/manage.py`) и сервисные команды (`cli.py`)

---

## Быстрый старт (локально)

### 1. Создать виртуальное окружение

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate
```

### 2. Установить зависимости

```bash
pip install -r requirements.txt
```

### 3. Настроить конфигурацию

Отредактируйте `config/app.cfg` — обязательно смените секреты:

```ini
[app]
secret_key = ЗАМЕНИТЕ_НА_СЛУЧАЙНУЮ_СТРОКУ
api_secret = ЗАМЕНИТЕ_НА_ДРУГУЮ_СЛУЧАЙНУЮ_СТРОКУ
debug      = true
```

Для SMTP (опционально) — `config/smtp.cfg`:

```ini
[smtp]
enabled  = true
host     = smtp.gmail.com
port     = 587
user     = your@gmail.com
password = your-app-password
from     = noreply@yourdomain.com
tls      = true
```

> `config/smtp.cfg` добавлен в `.gitignore` — не коммитьте пароли.

### 4. Создать директории

```bash
mkdir -p data logs
```

### 5. Запустить сервер

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

База данных создаётся автоматически при первом запуске.

Откройте **http://localhost:8000/setup** для создания первого администратора.

---

## Запуск через Docker

### SQLite (по умолчанию)

Минимальная конфигурация — всё хранится в `./data/licserver.db`.

```bash
mkdir -p data logs

# Смените секреты в config/app.cfg, затем:
docker-compose up --build -d

# Логи
docker-compose logs -f

# Остановить
docker-compose down
```

### PostgreSQL

`docker-compose.yml`:

```yaml
version: "3.9"

services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB:       licserver
      POSTGRES_USER:     licuser
      POSTGRES_PASSWORD: ЗАМЕНИТЕ
    volumes:
      - pgdata:/var/lib/postgresql/data

  licserver:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    depends_on:
      - db
    environment:
      DATABASE_URL: "postgresql+asyncpg://licuser:ЗАМЕНИТЕ@db:5432/licserver"
      DB_TYPE:      "postgres"
      SECRET_KEY:   "ЗАМЕНИТЕ"
      API_SECRET:   "ЗАМЕНИТЕ"
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs

volumes:
  pgdata:
```

> Для PostgreSQL требуется дополнительный драйвер: `asyncpg` уже включён в `requirements.txt`.

### MariaDB / MySQL

```yaml
version: "3.9"

services:
  db:
    image: mariadb:11
    restart: unless-stopped
    environment:
      MYSQL_DATABASE:      licserver
      MYSQL_USER:          licuser
      MYSQL_PASSWORD:      ЗАМЕНИТЕ
      MYSQL_ROOT_PASSWORD: ЗАМЕНИТЕ_ROOT
    volumes:
      - mariadbdata:/var/lib/mysql

  licserver:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    depends_on:
      - db
    environment:
      DATABASE_URL: "mysql+aiomysql://licuser:ЗАМЕНИТЕ@db:3306/licserver"
      DB_TYPE:      "mariadb"
      SECRET_KEY:   "ЗАМЕНИТЕ"
      API_SECRET:   "ЗАМЕНИТЕ"
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs

volumes:
  mariadbdata:
```

> Для MariaDB/MySQL требуется: `aiomysql` — уже включён в `requirements.txt`.

---

## Переменные окружения

Переменные окружения имеют приоритет над значениями в `config/*.cfg`.

| Переменная      | Описание                                          | Пример                                      |
|-----------------|---------------------------------------------------|---------------------------------------------|
| `DATABASE_URL`  | DSN подключения к БД                              | `sqlite+aiosqlite:///./data/licserver.db`   |
| `DB_TYPE`       | Тип БД: `sqlite`, `postgres`, `mariadb`           | `postgres`                                  |
| `SECRET_KEY`    | JWT-секрет (обязательно сменить)                  | `случайная-длинная-строка`                  |
| `API_SECRET`    | Секрет HMAC для подписи API-запросов              | `другая-случайная-строка`                   |
| `COOKIE_SECURE` | `true` — cookie только по HTTPS (production)      | `true`                                      |
| `SMTP_USER`     | Логин SMTP (переопределяет `config/smtp.cfg`)     | `user@gmail.com`                            |
| `SMTP_PASSWORD` | Пароль SMTP                                       | `app-password`                              |

---

## Конфигурация (`config/*.cfg`)

### config/app.cfg

```ini
[app]
secret_key           = CHANGE_ME_PLEASE     # JWT-секрет
api_secret           = CHANGE_ME_API_SECRET # Секрет HMAC
jwt_algorithm        = HS256
token_expires_hours  = 8
debug                = false                # true — отключает secure cookie
```

### config/database.cfg

```ini
[database]
url      = sqlite+aiosqlite:///./data/licserver.db
db_type  = sqlite     # sqlite | postgres | mariadb
echo_sql = false
```

### config/security.cfg

```ini
[brute_force]
max_attempts             = 5
lockout_minutes          = 15
attempt_window_minutes   = 10

[password]
min_length        = 8
require_uppercase = true
require_digits    = true
require_special   = true

[api_signing]
enabled                     = true   # false — отключить проверку подписи (только dev!)
timestamp_tolerance_seconds = 30
```

### config/smtp.cfg

```ini
[smtp]
enabled  = false
host     = smtp.gmail.com
port     = 587
user     =
password =
from     = noreply@example.com
tls      = true
```

---

## Архитектура

```
app/
  main.py              — FastAPI app, middleware, startup migrations
  config.py            — config/*.cfg + env overrides
  db.py                — async SQLAlchemy engine + get_session()
  models.py            — ORM: AdminUser, Client, License, LicenseKey, LicenseAction, …
  security.py          — bcrypt, JWT create/verify, require_owner/require_org
  api_signing.py       — HMAC-SHA256 signature verification
  audit.py             — log_action() → AuditLog table
  email.py             — async email via aiosmtplib
  utils.py             — generate_license_key(), make_qr_png()
  routers/
    public_api.py      — /api/* (JSON, HMAC-protected)
    auth.py            — /login, /logout, /forgot-password, /reset-password
    owner_web.py       — /owner/* (панель администратора, JWT cookie)
    org_web.py         — /org/*   (личный кабинет, JWT cookie)
    feedback.py        — /feedback (публичная форма с капчей)
templates/             — Jinja2 HTML (base_owner.html, base_org.html)
static/styles.css      — единый CSS
config/                — *.cfg конфигурационные файлы
scripts/manage.py      — интерактивный TUI-менеджер
cli.py                 — Click CLI
```

---

## Роли и доступ

| Роль         | Доступ                                                        |
|--------------|---------------------------------------------------------------|
| `superadmin` | Всё: управление администраторами, журнал аудита, экспорт CSV  |
| `admin`      | Управление клиентами и лицензиями                             |
| `org`        | Только свои лицензии (сброс, обратная связь)                  |

---

## Веб-интерфейс

| Раздел              | URL                   | Доступ       |
|---------------------|-----------------------|--------------|
| Первый запуск       | `/setup`              | без auth     |
| Вход                | `/login`              | все          |
| Забыли пароль       | `/forgot-password`    | все          |
| Дашборд владельца   | `/owner/dashboard`    | AdminUser    |
| Клиенты             | `/owner/clients`      | AdminUser    |
| Карточка клиента    | `/owner/clients/{id}` | AdminUser    |
| Резервная копия     | `/owner/backup`       | AdminUser    |
| Журнал аудита       | `/owner/logs`         | superadmin   |
| Кабинет организации | `/org/dashboard`      | Client (org) |
| Обратная связь org  | `/org/feedback`       | Client (org) |
| Профиль org         | `/org/profile`        | Client (org) |

Вход для организации: по `login` **или** `contact_email`.

---

## Публичный API

Все запросы к `/api/*` защищены HMAC-SHA256.

**Обязательные заголовки:**

| Заголовок     | Описание                                           |
|---------------|----------------------------------------------------|
| `X-Timestamp` | Unix timestamp UTC (допуск ±30 с)                  |
| `X-Nonce`     | Уникальная строка ≤128 символов (рекомендуется UUID4) |
| `X-Signature` | HMAC-SHA256 hex-дайджест строки подписи            |

**Строка для подписи:**
```
METHOD\nPATH\nTIMESTAMP\nNONCE\nSHA256(BODY)
```

Подробное описание: [`docs/api_signing.md`](docs/api_signing.md)

> Отключить проверку (только dev): `api_signing.enabled = false` в `config/security.cfg`

### POST /api/activate

Активация лицензии на устройстве. Идемпотентна для того же `device_id`.

```json
// Запрос
{
  "key":         "ABCD-1234-EFGH",
  "device_id":   "уникальный-id-устройства",
  "device_name": "Ноутбук бухгалтера",
  "key_version": 1
}

// Успех 200
{
  "status":       "activated",
  "license_id":   42,
  "organization": "ООО Пример",
  "expires_at":   "2027-01-15T00:00:00",
  "version":      1,
  "device_id":    "уникальный-id-устройства"
}
```

**Коды ошибок:** `LICENSE_NOT_FOUND` (404), `LICENSE_BLOCKED` (403), `LICENSE_EXPIRED` (403), `DEVICE_MISMATCH` (409), `VERSION_MISMATCH` (409)

### POST /api/deactivate

Освобождение лицензии с устройства. Статус → `released`.

```json
{ "key": "ABCD-1234-EFGH", "device_id": "уникальный-id-устройства" }
```

### POST /api/transfer

Перенос лицензии: деактивирует текущий ключ, генерирует новый.

```json
// Запрос
{ "key": "ABCD-1234-EFGH", "device_id": "текущий-device-id" }

// Успех 200
{ "status": "ok", "new_key": "XXXX-YYYY-ZZZZ" }
```

### GET /api/status?key=ABCD-1234-EFGH

Текущее состояние лицензии (без активации).

### GET /api/history?key=ABCD-1234-EFGH

История ключей и событий лицензии.

---

## Статусы лицензии

| Статус          | Хранится в БД | Описание                                          |
|-----------------|:-------------:|---------------------------------------------------|
| `not_activated` | ✓             | Выпущена, ни разу не активировалась               |
| `activated`     | ✓             | Активна на устройстве                             |
| `released`      | ✓             | Была активирована, затем деактивирована           |
| `blocked`       | ✓             | Заблокирована владельцем                          |
| `expired`       | —             | Вычисляется по `expires_at`; `blocked` — приоритет |

`License.computed_status(now)` возвращает итоговый статус.

---

## Формат ключа

```
XXXX-XXXX-XXXX
```

12 символов (A–Z, 0–9), три блока по 4. Генерируется через `secrets.choice()`.

---

## CLI

```bash
# Интерактивный TUI-менеджер (администраторы, организации, логи)
python scripts/manage.py

# Резервная копия БД → JSON-файл
python cli.py backup -o backup.json

# Восстановление из файла
python cli.py restore backup.json
python cli.py restore backup.json --yes   # без подтверждения

# Создать администратора из консоли
python cli.py create-admin admin@example.com

# Список организаций
python cli.py list-clients

# Инициализировать БД (если не запускать через uvicorn)
python cli.py db-init

# Синхронизировать настройки из config/ в БД
python cli.py sync-settings
```

### CLI в Docker

```bash
docker-compose exec licserver python cli.py backup -o /app/data/backup.json
docker-compose exec licserver python cli.py list-clients
```

---

## Хранение данных

Все персистентные данные хранятся в БД (включая логотипы как BLOB).
Файловая система для данных приложения не используется — полное восстановление из одного JSON-файла.

```
GET  /owner/backup/download  — скачать дамп
POST /owner/backup/restore   — восстановить из файла
```

---

## Безопасность

- **Пароли** — bcrypt, настраиваемые требования сложности (`config/security.cfg`)
- **JWT** — HttpOnly cookie, отдельные токены для admin и org
- **Secure cookie** — включается через `COOKIE_SECURE=true` (для HTTPS/production)
- **API-подпись** — HMAC-SHA256, nonce против replay-атак
- **Brute force** — блокировка по IP и аккаунту
- **Rate limiting** — вход, сброс пароля, обратная связь

---

## Тесты

```bash
pip install pytest pytest-asyncio httpx aiosqlite
pytest tests/ -v
```

Тесты используют in-memory SQLite, отключают HMAC-проверку через `dependency_overrides`.

- `tests/test_api.py` — `/api/activate`, `/api/deactivate`, `/api/transfer`, `/api/status`, `/api/history`
- `tests/test_statuses.py` — переходы статусов лицензии
- `tests/test_auth.py` — вход по login и по contact_email
