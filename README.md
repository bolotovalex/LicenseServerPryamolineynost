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
```

---

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
}
```

---

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
```

---

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
