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
```

---

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
}
```

---

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
```

---

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
