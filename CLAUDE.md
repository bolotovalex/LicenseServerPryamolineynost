# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

**Development (local):**
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Docker:**
```bash
docker-compose up --build
```

**Environment variables** (`.env` file or docker-compose `environment`):
- `DATABASE_URL` — default: `sqlite+aiosqlite:///./licserver.db`
- `JWT_SECRET` — must be changed from default `CHANGE_ME`
- `ACCESS_TOKEN_EXPIRES_MIN` — default: 480 (8 hours)

**First-run admin setup:** navigate to `/init-admin` to create the first admin account (only works when no admin users exist).

## Architecture

The app is a FastAPI async web service split into two main concerns:

### Routers
- **`app/routers/public_api.py`** — JSON API consumed by licensed client software, mounted at `/api`. No authentication. Two endpoints:
  - `POST /api/activate` — activates a license key for a device (idempotent for same device)
  - `POST /api/transfer` — deactivates the current key and generates a new one, allowing re-activation on a new device
- **`app/routers/admin_web.py`** — Server-side HTML admin panel using Jinja2 templates. Auth via JWT stored in an HttpOnly cookie (`access_token`). Manages clients, license issuance, reset, block/unblock, and QR code generation.

### Data Models (`app/models.py`)
- `AdminUser` — admin panel users
- `Client` — organizations that own licenses (`org_name`, `notes`)
- `License` — a single license record; holds the **current active key** in `License.key` for fast lookup. Has `activated_at`, `device_id`, `expires_at`, `is_blocked`.
- `LicenseKey` — full history of all keys ever generated for a license; only one has `is_active=True` at a time. When a reset or transfer occurs, the old key is deactivated here.
- `LicenseAction` — audit log of actions: `issue`, `reset`, `block`, `unblock`, `activate`

### License Key Lifecycle
1. **Issue** (`/licenses/issue`): generates a key, stores it on `License.key`, adds active `LicenseKey` history record.
2. **Activate** (`POST /api/activate`): client sends key + device_id; sets `License.activated_at` and `device_id`.
3. **Reset** (`/licenses/{id}/reset`) or **Transfer** (`POST /api/transfer`): deactivates old `LicenseKey`, generates a new key, increments `License.version`, clears activation fields.
4. **Block/Unblock**: sets `License.is_blocked` flag; blocked licenses are rejected at activation.

### Key Files
- `app/settings.py` — тонкая обёртка над `app/config.py` для обратной совместимости
- `app/config.py` — читает `config/*.cfg` через `configparser`; объекты `app_config`, `db_config`, `smtp_config`, `security_config`, `logging_config`
- `app/db.py` — async SQLAlchemy engine и `get_session()` dependency; URL берётся из `db_config.url`
- `app/security.py` — bcrypt password hashing, JWT creation/validation
- `app/utils.py` — `generate_license_key()` (base32 UUID, формат `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`) и `make_qr_png()`
- `app/services/backup.py` — `create_backup(session)` → `bytes`, `restore_backup(engine, bytes)` → stats
- `app/services/settings_db.py` — `sync_from_config()`, `get_setting()`, `set_setting()`
- `templates/` — Jinja2 HTML; `base.html` — родительский layout
- `static/styles.css` — единый CSS
- `cli.py` — CLI на `click`: `backup`, `restore`, `create-admin`, `list-clients`, `db-init`, `sync-settings`

### Правило хранения данных
**Все персистентные данные (включая бинарные — логотипы, и конфигурационные — настройки) хранятся исключительно в базе данных.** Файловая система для хранения данных приложения не используется. Это гарантирует, что единственный файл резервной копии (JSON-дамп БД) полностью восстанавливает систему.

- Логотипы клиентов: `Client.logo_data` (BLOB) + `Client.logo_mime`; отдаются через `GET /clients/{id}/logo`
- Настройки: таблица `app_settings` (key/value); синхронизируются из `config/*.cfg` при старте
- Резервная копия: `GET /backup/download` (admin UI) или `python cli.py backup`
- Восстановление: `POST /backup/restore` (admin UI) или `python cli.py restore FILE`

### CLI
```bash
python cli.py backup -o backup.json        # создать резервную копию
python cli.py restore backup.json          # восстановить (с подтверждением)
python cli.py restore backup.json --yes    # восстановить без подтверждения
python cli.py create-admin email@example.com
python cli.py list-clients
python cli.py db-init
python cli.py sync-settings
```

### Data Models (`app/models.py`)
- `AdminUser` — пользователи админ-панели
- `Client` — организации; `logo_data`/`logo_mime` — логотип в BLOB
- `License` — лицензия; `License.key` — текущий активный ключ для быстрого поиска
- `LicenseKey` — история всех ключей лицензии; только один `is_active=True`
- `LicenseAction` — журнал: `issue`, `reset`, `block`, `unblock`, `activate`
- `AppSetting` — настройки приложения (key/value); участвуют в backup/restore
