# Промпт для Claude Code: создание клиентского приложения

Используйте этот промпт как стартовую точку для Claude Code при разработке клиентского приложения, использующего License Server API.

---

```md
Ты работаешь в репозитории Python-клиента для License Server API.
Создай клиентскую библиотеку (пакет `liclient`) и демо-приложение.

## Что такое License Server

REST API сервер лицензирования ПО. Базовый URL: задаётся через конфиг.
Все запросы к /api/* требуют HMAC-SHA256 подписи.

## Формат ключа

`XXXX-XXXX-XXXX` — 12 символов A-Z0-9, три блока по 4 символа, разделённых дефисом.

## Статусы лицензии

- `not_activated` — ключ выпущен, не использован
- `activated`     — активирован на устройстве (device_id привязан)
- `released`      — был активирован, затем деактивирован (доступен для повторной активации)
- `blocked`       — заблокирован владельцем (активация невозможна)
- `expired`       — истёк срок действия (вычисляется на лету)

## HMAC-подпись запроса

Каждый запрос к /api/* должен содержать заголовки:
- `X-Timestamp`: Unix timestamp (int, UTC секунды)
- `X-Nonce`: UUID4 строка (каждый раз уникальная)
- `X-Signature`: HMAC-SHA256 hex-дайджест строки подписи

Строка для подписи (точный формат, без пробелов вокруг \n):
```
{METHOD}\n{PATH}\n{TIMESTAMP}\n{NONCE}\n{SHA256(BODY_BYTES)}
```

Пример (Python):
```python
import hashlib, hmac, time, uuid, json

def make_headers(method: str, path: str, body: bytes, secret: str) -> dict:
    ts    = str(int(time.time()))
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    sts = f"{method}\n{path}\n{ts}\n{nonce}\n{body_hash}"
    sig = hmac.new(secret.encode(), sts.encode(), hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig}
```

## API Endpoints

### POST /api/activate
Активация ключа. Идемпотентна (повторный вызов с тем же device_id → 200).

Запрос:
```json
{
  "key":          "ABCD-1234-EFGH",
  "device_id":    "уникальный-id-устройства",
  "device_name":  "Имя компьютера (необязательно)",
  "comment":      "Произвольный комментарий (необязательно)",
  "key_version":  1
}
```

Ответ 200 — лицензия активирована:
```json
{
  "status":       "activated",
  "license_id":   42,
  "organization": "ООО Пример",
  "description":  "Описание лицензии",
  "activated_at": "2026-01-15T10:30:00",
  "expires_at":   "2027-01-15T00:00:00",
  "version":      1,
  "device_id":    "уникальный-id-устройства",
  "device_name":  "Имя компьютера"
}
```

Коды ошибок (поле `code` в теле ответа):
- `LICENSE_NOT_FOUND` (404) — ключ не найден
- `LICENSE_BLOCKED` (403) — лицензия заблокирована
- `LICENSE_EXPIRED` (403) — истёк срок действия
- `DEVICE_MISMATCH` (409) — уже активирована на другом устройстве
- `VERSION_MISMATCH` (409) — устаревшая версия ключа

### POST /api/deactivate
Освобождение лицензии с устройства. После — статус `released`.

```json
{ "key": "ABCD-1234-EFGH", "device_id": "уникальный-id-устройства" }
```

Коды ошибок: `LICENSE_NOT_FOUND`, `LICENSE_BLOCKED`, `NOT_ACTIVATED`, `DEVICE_MISMATCH`

### POST /api/transfer
Перенос лицензии: текущий ключ деактивируется, возвращается новый.
Вызывать с device_id того устройства, с которого переносим.

```json
{ "key": "ABCD-1234-EFGH", "device_id": "текущий-device-id" }
```

Ответ содержит поле `new_key` — новый ключ для активации на другом устройстве.

### GET /api/status?key=ABCD-1234-EFGH
Текущее состояние лицензии (только чтение). Не требует device_id.

### GET /api/history?key=ABCD-1234-EFGH
История ключей и событий лицензии.

## Что нужно реализовать

### 1. Пакет `liclient/`

```
liclient/
  __init__.py
  client.py      — основной класс LicenseClient
  device.py      — получение уникального device_id
  storage.py     — хранение ключа и статуса локально (JSON-файл)
  exceptions.py  — иерархия исключений
  signing.py     — HMAC-подпись (скопировать пример выше)
```

**`LicenseClient`** должен предоставлять:
```python
class LicenseClient:
    def __init__(self, base_url: str, api_secret: str, key: str): ...

    def activate(self, device_name: str | None = None) -> dict:
        """Активировать лицензию. Возвращает info-словарь или raises."""

    def deactivate(self) -> dict:
        """Деактивировать лицензию (освободить для переноса)."""

    def transfer(self) -> str:
        """Перенести лицензию, вернуть новый ключ."""

    def status(self) -> dict:
        """Получить текущий статус (только чтение)."""

    def is_valid(self) -> bool:
        """True если лицензия activated и не expired."""
```

**Обработка ошибок:**
- `LicenseNotFound` — ключ не найден
- `LicenseBlocked` — заблокирован (показать сообщение с причиной)
- `LicenseExpired` — истёк срок
- `DeviceMismatch` — активирован на другом устройстве
- `VersionMismatch` — устаревшая версия, нужно получить актуальный ключ
- `LicenseServerError` — прочие ошибки сервера

**`device.py`** — получение стабильного device_id:
- Windows: `winreg` MachineGuid или `wmi` UUID
- Linux: `/etc/machine-id` или `dmidecode`
- macOS: `IOPlatformUUID` через `system_profiler`
- Fallback: SHA256 от hostname + MAC-адреса

**`storage.py`** — локальный JSON-файл `~/.liclient/{app_name}.json`:
```json
{
  "key": "ABCD-1234-EFGH",
  "activated": true,
  "device_id": "...",
  "last_check": "2026-01-15T10:30:00",
  "expires_at": "2027-01-15T00:00:00",
  "license_id": 42
}
```

### 2. Демо-приложение `demo.py`

CLI на `click` или `argparse`:
```
python demo.py activate ABCD-1234-EFGH --server http://localhost:8000 --secret MY_SECRET
python demo.py status
python demo.py deactivate
python demo.py transfer
```

### 3. Конфигурация

`liclient/config.py` — читает из:
1. Переменных окружения: `LICENSE_SERVER_URL`, `LICENSE_API_SECRET`
2. Файла `~/.liclient/config.ini` (секция `[server]`)
3. Параметров конструктора `LicenseClient(base_url=..., api_secret=...)`

### 4. Требования к коду

- Python 3.10+
- HTTP-клиент: `httpx` (sync) или `requests`
- Тайм-аут запроса: 10 секунд
- Retry: 3 попытки с экспоненциальной задержкой для сетевых ошибок (не для 4xx)
- Логирование через `logging` (не `print`)
- Type hints везде
- `pyproject.toml` или `setup.py`

### 5. Тесты (`tests/`)

- `test_signing.py` — проверка HMAC-подписи
- `test_client.py` — тесты через `httpx.MockTransport` или `responses`
- `test_device.py` — тест получения device_id (мокировать платформо-зависимые вызовы)

### 6. README.md

- Установка (`pip install -e .`)
- Быстрый старт (3 команды)
- Пример использования в коде Python
- Формат конфигурации
- Обработка ошибок (таблица исключений)

## Ограничения

- Не храни API_SECRET в коде — только через переменные окружения или конфиг-файл
- device_id должен быть стабильным (одинаковым при повторных запусках)
- При `DeviceMismatch` сообщить пользователю, что для переноса нужно использовать `transfer` с оригинального устройства
- Не делай автоматических retry при 409/403 — это логические ошибки, не сетевые

## Что НЕ нужно реализовывать

- Веб-интерфейс
- База данных на стороне клиента (только JSON-файл)
- Offline-режим (лицензия требует подключения к серверу)
```
