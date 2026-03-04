# Промпт для Claude Code: создание клиентского приложения

Используйте этот промпт как стартовую точку при разработке клиентского приложения поверх License Server API.

---

```md
Ты работаешь над клиентским приложением, которое использует License Server API для управления лицензиями ПО.

## License Server API — быстрый справочник

**Base URL:** задаётся через конфигурацию (например, `https://licenses.example.com`)
**Все запросы к /api/*** требуют HMAC-SHA256 подписи (см. раздел «Подпись»).

---

## Формат ключа

`ABCD-1234-EFGH` — 12 символов A–Z, 0–9, три блока по 4 через дефис.

---

## Статусы лицензии

| Статус          | Описание                                                       |
|-----------------|----------------------------------------------------------------|
| `not_activated` | Ключ выпущен, ещё не активирован                               |
| `activated`     | Активирован — `device_id` и `activated_at` заполнены          |
| `released`      | Был активирован, затем деактивирован — доступен повторно       |
| `blocked`       | Заблокирован администратором — активация невозможна            |
| `expired`       | Срок истёк (`expires_at` в прошлом) — вычисляется на сервере  |

---

## HMAC-SHA256 подпись

Каждый запрос обязан содержать три заголовка:

```
X-Timestamp:  <unix timestamp UTC, целое число секунд>
X-Nonce:      <UUID4 или random hex, уникален для каждого запроса, ≤ 128 символов>
X-Signature:  <HMAC-SHA256 hex строки подписи>
```

**Строка для подписи** (компоненты соединяются символом `\n`):

```
{METHOD}\n{PATH}\n{TIMESTAMP}\n{NONCE}\n{SHA256_HEX(BODY_BYTES)}
```

- `METHOD` — HTTP-метод в верхнем регистре: `POST`, `GET`
- `PATH` — путь без query string: `/api/activate`
- Для GET-запросов (тело пустое) SHA256 = `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

**Реализация на Python:**

```python
import hashlib, hmac, time, uuid

def sign(method: str, path: str, body: bytes, secret: str) -> dict:
    ts    = str(int(time.time()))
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    sts = f"{method}\n{path}\n{ts}\n{nonce}\n{body_hash}"
    sig = hmac.new(secret.encode(), sts.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig,
        "Content-Type": "application/json",
    }
```

**Ошибки подписи → HTTP 401:**

| Код                 | Причина                                              |
|---------------------|------------------------------------------------------|
| `TIMESTAMP_EXPIRED` | Временная метка отсутствует или отклонение > 30 сек  |
| `NONCE_REUSED`      | Nonce уже использовался в течение 60 сек             |
| `INVALID_SIGNATURE` | Подпись не совпадает                                 |

---

## Эндпоинты

### POST /api/activate — активация лицензии

Идемпотентен: повторный вызов с тем же `device_id` → 200, обновляет имя/комментарий.
Если `device_id` ранее был привязан к другой лицензии — та освобождается автоматически.

**Запрос:**
```json
{
  "key":         "ABCD-1234-EFGH",
  "device_id":   "550e8400-e29b-41d4-a716-446655440000",
  "device_name": "Ноутбук Иванова",
  "comment":     "Бухгалтерия",
  "key_version": 1
}
```
`device_name`, `comment`, `key_version` — необязательны.

**Ответ 200:** общий блок данных лицензии (см. ниже) + `"status": "ok"`.

**Ошибки:**

| HTTP | Код                | Описание                                           |
|------|--------------------|----------------------------------------------------|
| 404  | `LICENSE_NOT_FOUND`| Ключ не существует                                 |
| 403  | `LICENSE_BLOCKED`  | Заблокирована (`reason` — причина в поле `reason`) |
| 403  | `LICENSE_EXPIRED`  | Срок истёк                                         |
| 409  | `VERSION_MISMATCH` | `key_version` устарел — нужен актуальный ключ      |
| 409  | `DEVICE_MISMATCH`  | Уже активирована на другом устройстве              |

---

### POST /api/verify — проверка лицензии (read-only)

Периодическая валидация: «эта лицензия активна именно на моём устройстве?»
Не меняет состояние. Используется при каждом запуске приложения или по расписанию.

**Запрос:**
```json
{ "key": "ABCD-1234-EFGH", "device_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Ответ 200 (валидна):**
```json
{ "status": "ok", "valid": true, "license_id": 42, "expires_at": "2027-03-01T00:00:00", ... }
```

**Ответ при ошибке** всегда содержит `"valid": false` и полный блок данных лицензии:
```json
{ "status": "error", "code": "DEVICE_MISMATCH", "valid": false, "license_id": 42, ... }
```

**Ошибки:**

| HTTP | Код                | Описание                                           |
|------|--------------------|----------------------------------------------------|
| 404  | `LICENSE_NOT_FOUND`| Ключ не существует                                 |
| 403  | `LICENSE_BLOCKED`  | Заблокирована                                      |
| 403  | `LICENSE_EXPIRED`  | Срок истёк                                         |
| 409  | `NOT_ACTIVATED`    | Лицензия не активирована ни на одном устройстве    |
| 409  | `DEVICE_MISMATCH`  | Активирована на другом устройстве                  |

---

### POST /api/deactivate — освобождение лицензии

После вызова статус → `released`. Лицензию можно активировать на другом устройстве.

**Запрос:**
```json
{ "key": "ABCD-1234-EFGH", "device_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Ответ 200:**
```json
{ "status": "ok", "code": "DEACTIVATED", "message": "Лицензия освобождена" }
```

**Ошибки:** `LICENSE_NOT_FOUND` (404), `LICENSE_BLOCKED` (403), `NOT_ACTIVATED` (409), `DEVICE_MISMATCH` (409)

---

### POST /api/transfer — перенос на новое устройство

Аннулирует текущий ключ, генерирует новый. Вызывать с того устройства, где сейчас активирована лицензия. После — сохранить `new_key`, старый ключ недействителен.

**Запрос:**
```json
{ "key": "ABCD-1234-EFGH", "device_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Ответ 200:**
```json
{ "status": "ok", "new_key": "XXXX-YYYY-ZZZZ", "license_id": 42, "version": 2, ... }
```

**Ошибки:** `LICENSE_NOT_FOUND` (404), `LICENSE_BLOCKED` (403), `LICENSE_EXPIRED` (403), `NOT_ACTIVATED` (409), `DEVICE_MISMATCH` (409)

---

### GET /api/status?key=ABCD-1234-EFGH — текущее состояние (read-only)

Не требует `device_id`. Для информационного отображения.

**Ответ 200:** общий блок данных лицензии.

---

### GET /api/history?key=ABCD-1234-EFGH — история

Возвращает массивы `keys` (история ключей) и `actions` (история событий).

---

## Общий блок данных лицензии

Большинство ответов содержат эти поля:

```json
{
  "license_id":   42,
  "organization": "ООО Пример",
  "description":  "Основная лицензия",
  "status":       "activated",
  "activated_at": "2026-03-01T09:00:00",
  "expires_at":   "2027-03-01T00:00:00",
  "version":      1,
  "device_id":    "550e8400-e29b-41d4-a716-446655440000",
  "device_name":  "Ноутбук Иванова",
  "logo_url":     "/owner/clients/5/logo"
}
```

`expires_at` = `"permanent"` если лицензия бессрочная.
`logo_url` = `null` если логотип не загружен.

---

## Иерархия исключений (рекомендуется реализовать)

```python
LicenseServerError          # базовое — все ошибки сервера
├── SignatureError           # 401: TIMESTAMP_EXPIRED, NONCE_REUSED, INVALID_SIGNATURE
├── LicenseNotFound         # 404: LICENSE_NOT_FOUND
├── LicenseBlocked          # 403: LICENSE_BLOCKED (атрибут .reason)
├── LicenseExpired          # 403: LICENSE_EXPIRED
├── NotActivated            # 409: NOT_ACTIVATED
├── DeviceMismatch          # 409: DEVICE_MISMATCH
└── VersionMismatch         # 409: VERSION_MISMATCH
```

Не делайте retry при 4xx — это логические ошибки, не сетевые.

---

## Device ID — требования

- **Стабильный** между запусками (одно устройство = один ID всегда).
- **Уникальный** между разными устройствами.
- Рекомендуемые источники:
  - Windows: `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` (winreg)
  - Linux: `/etc/machine-id`
  - macOS: `IOPlatformUUID` через `ioreg -rd1 -c IOPlatformExpertDevice`
  - Fallback: SHA256 от `hostname + MAC-адрес основного интерфейса`

---

## Типичный жизненный цикл в приложении

```
1. Первый запуск:
   - Пользователь вводит ключ
   - POST /api/activate → сохранить ключ и данные локально
   - При DEVICE_MISMATCH: предложить transfer с оригинального устройства

2. Каждый запуск:
   - POST /api/verify
   - valid=true → продолжить работу
   - DEVICE_MISMATCH / BLOCKED / EXPIRED → заблокировать функционал, показать причину

3. Смена устройства:
   - На старом устройстве: POST /api/transfer → получить new_key
   - Передать new_key пользователю (показать, скопировать, отправить)
   - На новом устройстве: POST /api/activate с new_key

4. Деактивация (опционально):
   - POST /api/deactivate — если пользователь явно уходит с устройства
```

---

## Хранение секрета API_SECRET

- Никогда не хардкодить в исходном коде.
- Python-приложение: переменная окружения `LICENSE_API_SECRET`.
- Flutter: `flutter_secure_storage` (iOS Keychain / Android Keystore).
- Desktop: системное хранилище секретов (keyring, Credential Manager).

---

## Отключение подписи (только dev/тесты)

Сервер поддерживает отключение проверки в `config/security.cfg`:
```ini
[api_signing]
enabled = false
```
В тестах переопределяйте dependency `verify_api_signature` через `dependency_overrides`.
```
