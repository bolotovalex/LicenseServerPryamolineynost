# License Server — Руководство по API

Публичный REST API для клиентских приложений. Базовый URL задаётся при развёртывании сервера.

---

## Защита канала связи

Используется двухуровневая защита:

| Уровень | Механизм | Защищает от |
|---------|----------|-------------|
| Транспорт | **HTTPS / TLS** | Прослушивания, MITM |
| Запрос | **HMAC-SHA256 подпись** | Подделки и replay запросов |
| Ответ | **AES-256-GCM шифрование** | Replay ответа, реверс-инжиниринга протокола |

Ответ зашифрован ключом, производным от `API_SECRET` + `X-Nonce` конкретного запроса.
Воспроизвести старый ответ невозможно — он привязан к одноразовому nonce.

---

## Аутентификация

Все запросы к `/api/*` защищены **HMAC-SHA256 подписью**. Без корректной подписи сервер вернёт HTTP 401.

### Обязательные заголовки

| Заголовок     | Тип    | Описание                                                        |
|---------------|--------|-----------------------------------------------------------------|
| `X-Timestamp` | int    | Unix timestamp UTC в секундах. Допуск ±30 сек от серверного времени. |
| `X-Nonce`     | string | Уникальная строка ≤ 128 символов. Рекомендуется UUID4. Нельзя повторять в течение 60 сек. |
| `X-Signature` | string | HMAC-SHA256 hex-дайджест строки подписи (см. ниже).            |
| `Content-Type`| string | `application/json` — для POST-запросов с телом.                |

### Строка для подписи

```
METHOD\nPATH\nTIMESTAMP\nNONCE\nSHA256(BODY)
```

- `METHOD` — HTTP-метод в верхнем регистре: `POST`, `GET`
- `PATH` — путь без query string: `/api/activate`
- `TIMESTAMP` — строка из заголовка `X-Timestamp`
- `NONCE` — строка из заголовка `X-Nonce`
- `SHA256(BODY)` — SHA-256 hex тела запроса в байтах; для GET — SHA-256 пустых байт:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

Подпись: `HMAC-SHA256(string_to_sign, API_SECRET).hexdigest()`

### Пример подписи (Python)

```python
import hashlib, hmac, time, uuid

def sign_request(method: str, path: str, body: bytes, secret: str) -> dict:
    ts    = str(int(time.time()))
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    sts = f"{method}\n{path}\n{ts}\n{nonce}\n{body_hash}"
    sig = hmac.new(secret.encode(), sts.encode(), hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig,
            "Content-Type": "application/json"}
```

### Ошибки подписи (HTTP 401)

```json
{ "status": "error", "code": "INVALID_SIGNATURE", "reason": "Подпись запроса неверна" }
```

| Код                 | Причина                                                         |
|---------------------|-----------------------------------------------------------------|
| `TIMESTAMP_EXPIRED` | `X-Timestamp` отсутствует, не число или отклонение > 30 сек    |
| `NONCE_REUSED`      | Этот `X-Nonce` уже использовался в течение последних 60 сек    |
| `INVALID_SIGNATURE` | Вычисленная подпись не совпадает с `X-Signature`               |

---

## Формат ответа

Успешный ответ всегда содержит `"status": "ok"`. Ошибка — `"status": "error"` + поле `"code"`.

### Общий блок данных лицензии

Большинство эндпоинтов возвращают следующий набор полей:

| Поле           | Тип            | Описание                                          |
|----------------|----------------|---------------------------------------------------|
| `license_id`   | int            | Идентификатор лицензии в системе                  |
| `organization` | string \| null | Название организации-владельца                    |
| `description`  | string \| null | Описание лицензии                                 |
| `status`       | string         | Текущий статус (см. раздел «Статусы»)             |
| `activated_at` | string \| null | ISO datetime активации или `null`                 |
| `expires_at`   | string         | ISO datetime истечения или `"permanent"`          |
| `version`      | int            | Версия ключа (растёт при каждом `transfer`)       |
| `device_id`    | string \| null | ID устройства, на котором активирована лицензия   |
| `device_name`  | string \| null | Человекочитаемое имя устройства                   |
| `logo_url`     | string \| null | URL логотипа организации (если загружен)          |

---

## Статусы лицензии

| Статус          | Описание                                                        |
|-----------------|-----------------------------------------------------------------|
| `not_activated` | Ключ выпущен, ни разу не активировался                         |
| `activated`     | Активирован, устройство привязано                              |
| `released`      | Был активирован, затем деактивирован — доступен снова          |
| `blocked`       | Заблокирован администратором — активация невозможна            |
| `expired`       | Срок истёк (`expires_at` в прошлом) — вычисляется на сервере  |

> `blocked` имеет приоритет над `expired`.

---

## Формат ключа

```
XXXX-XXXX-XXXX
```

12 символов (A–Z, 0–9), три блока по 4, разделённые дефисом.

---

## Эндпоинты

### POST /api/activate

Активация лицензионного ключа на устройстве.

**Идемпотентен**: повторный вызов с тем же `device_id` возвращает 200 и обновляет `device_name`/`comment`.

**Сценарий смены ключа**: если этот `device_id` ранее был привязан к другой лицензии, та лицензия автоматически освобождается.

**Запрос:**

```json
{
  "key":         "ABCD-1234-EFGH",
  "device_id":   "550e8400-e29b-41d4-a716-446655440000",
  "device_name": "Ноутбук бухгалтера",
  "comment":     "Бухгалтерия, отдел 3",
  "key_version": 1
}
```

| Поле          | Обязателен | Описание                                              |
|---------------|------------|-------------------------------------------------------|
| `key`         | да         | Лицензионный ключ                                     |
| `device_id`   | да         | Уникальный ID устройства (стабильный между запусками) |
| `device_name` | нет        | Человекочитаемое имя устройства                       |
| `comment`     | нет        | Произвольный комментарий                              |
| `key_version` | нет        | Версия ключа, известная клиенту — защита от устаревших ключей |

**Ответ 200:**

```json
{
  "status":       "ok",
  "license_id":   42,
  "organization": "ООО Пример",
  "description":  "Основная лицензия",
  "status":       "activated",
  "activated_at": "2026-03-01T09:00:00",
  "expires_at":   "2027-03-01T00:00:00",
  "version":      1,
  "device_id":    "550e8400-e29b-41d4-a716-446655440000",
  "device_name":  "Ноутбук бухгалтера",
  "logo_url":     "/owner/clients/5/logo"
}
```

**Коды ошибок:**

| HTTP | Код                | Описание                                              |
|------|--------------------|-------------------------------------------------------|
| 404  | `LICENSE_NOT_FOUND`| Ключ не найден                                        |
| 403  | `LICENSE_BLOCKED`  | Лицензия заблокирована (поле `reason` — причина)      |
| 403  | `LICENSE_EXPIRED`  | Срок действия истёк                                   |
| 409  | `VERSION_MISMATCH` | `key_version` не совпадает с текущей версией ключа    |
| 409  | `DEVICE_MISMATCH`  | Лицензия уже активирована на другом устройстве        |

---

### POST /api/verify

Проверка лицензии на конкретном устройстве. **Не изменяет состояние** — только читает.

Используется для периодической валидации: приложение проверяет, что лицензия всё ещё активна и привязана именно к этому устройству.

**Запрос:**

```json
{
  "key":       "ABCD-1234-EFGH",
  "device_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Ответ 200 — лицензия валидна:**

```json
{
  "status":    "ok",
  "valid":     true,
  "license_id": 42,
  "organization": "ООО Пример",
  "status":    "activated",
  "expires_at": "2027-03-01T00:00:00",
  "device_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Ответ при ошибке** всегда содержит `"valid": false` и весь блок данных лицензии (для отображения пользователю):

```json
{
  "status": "error",
  "code":   "DEVICE_MISMATCH",
  "reason": "Лицензия активирована на другом устройстве",
  "valid":  false,
  "license_id": 42,
  ...
}
```

**Коды ошибок:**

| HTTP | Код                | Описание                                              |
|------|--------------------|-------------------------------------------------------|
| 404  | `LICENSE_NOT_FOUND`| Ключ не найден                                        |
| 403  | `LICENSE_BLOCKED`  | Лицензия заблокирована                                |
| 403  | `LICENSE_EXPIRED`  | Срок действия истёк                                   |
| 409  | `NOT_ACTIVATED`    | Лицензия не активирована ни на одном устройстве       |
| 409  | `DEVICE_MISMATCH`  | Лицензия активирована на другом устройстве            |

---

### POST /api/deactivate

Освобождение лицензии с устройства. После вызова статус становится `released`, и лицензию можно активировать на другом устройстве.

**Запрос:**

```json
{
  "key":       "ABCD-1234-EFGH",
  "device_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Ответ 200:**

```json
{
  "status":  "ok",
  "code":    "DEACTIVATED",
  "message": "Лицензия освобождена"
}
```

**Коды ошибок:**

| HTTP | Код                | Описание                                                    |
|------|--------------------|-------------------------------------------------------------|
| 404  | `LICENSE_NOT_FOUND`| Ключ не найден                                              |
| 403  | `LICENSE_BLOCKED`  | Лицензия заблокирована                                      |
| 409  | `NOT_ACTIVATED`    | Лицензия не активирована                                    |
| 409  | `DEVICE_MISMATCH`  | `device_id` не совпадает с тем, на котором активирована     |

---

### POST /api/transfer

Перенос лицензии на новое устройство. Текущий ключ аннулируется, генерируется новый.

Вызывается **с устройства, на котором лицензия сейчас активирована**. После переноса старый ключ становится недействительным — сохраните `new_key`.

**Запрос:**

```json
{
  "key":       "ABCD-1234-EFGH",
  "device_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Ответ 200:**

```json
{
  "status":   "ok",
  "new_key":  "XXXX-YYYY-ZZZZ",
  "license_id": 42,
  "organization": "ООО Пример",
  "status":   "not_activated",
  "version":  2,
  "expires_at": "2027-03-01T00:00:00"
}
```

**Коды ошибок:**

| HTTP | Код                | Описание                                              |
|------|--------------------|-------------------------------------------------------|
| 404  | `LICENSE_NOT_FOUND`| Ключ не найден                                        |
| 403  | `LICENSE_BLOCKED`  | Лицензия заблокирована                                |
| 403  | `LICENSE_EXPIRED`  | Срок действия истёк                                   |
| 409  | `NOT_ACTIVATED`    | Лицензия не активирована                              |
| 409  | `DEVICE_MISMATCH`  | `device_id` не совпадает                             |

---

### GET /api/status

Текущее состояние лицензии. Не изменяет данные. Не требует `device_id`.

**Запрос:** `GET /api/status?key=ABCD-1234-EFGH`

**Ответ 200:**

```json
{
  "status":       "ok",
  "license_id":   42,
  "organization": "ООО Пример",
  "description":  "Основная лицензия",
  "status":       "activated",
  "activated_at": "2026-03-01T09:00:00",
  "expires_at":   "permanent",
  "version":      1,
  "device_id":    "550e8400-e29b-41d4-a716-446655440000",
  "device_name":  "Ноутбук бухгалтера",
  "logo_url":     null
}
```

---

### GET /api/history

История всех ключей и действий с лицензией.

**Запрос:** `GET /api/history?key=ABCD-1234-EFGH`

**Ответ 200:**

```json
{
  "status":     "ok",
  "license_id": 42,
  "keys": [
    {
      "key":            "ABCD-1234-EFGH",
      "is_active":      true,
      "issued_at":      "2026-01-01T00:00:00",
      "deactivated_at": null,
      "reason":         null
    }
  ],
  "actions": [
    {
      "action": "activate",
      "at":     "2026-03-01T09:00:00",
      "reason": null,
      "actor":  "550e8400-e29b-41d4-a716-446655440000"
    }
  ]
}
```

---

## Шифрование ответов (AES-256-GCM)

Когда шифрование включено (`api_encryption.enabled = true` в `config/security.cfg`), все ответы `/api/*` возвращаются в зашифрованном виде.

### Формат зашифрованного ответа

```
Content-Type: application/vnd.licserver.encrypted+json

{
  "iv":    "<base64, 12 байт — случайный IV>",
  "ct":    "<base64, ciphertext + 16-байтный GCM auth tag>",
  "nonce": "<эхо X-Nonce из запроса>"
}
```

### Алгоритм расшифровки (клиентская сторона)

```
key   = HMAC-SHA256(API_SECRET, "enc:" + nonce)   # первые 32 байта = ключ AES-256
plain = AES-256-GCM.decrypt(key, iv, ct, aad=nonce.encode())
data  = JSON.parse(plain)
```

`nonce` используется как **AAD (Additional Authenticated Data)** — GCM гарантирует,
что ciphertext нельзя использовать с другим nonce. Попытка replay провалится
с ошибкой аутентификации тега.

### Пример на Python

```python
import base64, hashlib, hmac, json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def decrypt_api_response(payload: dict, api_secret: str) -> dict:
    nonce = payload["nonce"]
    key   = hmac.new(api_secret.encode(), f"enc:{nonce}".encode(), hashlib.sha256).digest()
    iv    = base64.b64decode(payload["iv"])
    ct    = base64.b64decode(payload["ct"])
    plain = AESGCM(key).decrypt(iv, ct, nonce.encode())
    return json.loads(plain)
```

### Определение режима по Content-Type

| Content-Type                                | Режим             |
|---------------------------------------------|-------------------|
| `application/json`                          | Plain JSON        |
| `application/vnd.licserver.encrypted+json`  | AES-256-GCM       |

### Что не шифруется

- 401-ответы при отсутствии `X-Nonce` (ошибки подписи до обработки запроса)

### Отключение (только для разработки)

```ini
# config/security.cfg
[api_encryption]
enabled = false
```

---

## Полная таблица кодов ошибок

| HTTP | Код                 | Возникает в                      | Описание                                           |
|------|---------------------|----------------------------------|----------------------------------------------------|
| 401  | `TIMESTAMP_EXPIRED` | все запросы                      | Временная метка устарела или некорректна           |
| 401  | `NONCE_REUSED`      | все запросы                      | Nonce уже использовался                            |
| 401  | `INVALID_SIGNATURE` | все запросы                      | Подпись не совпадает                               |
| 404  | `LICENSE_NOT_FOUND` | все эндпоинты                    | Ключ не найден                                     |
| 403  | `LICENSE_BLOCKED`   | activate, verify, deactivate, transfer | Лицензия заблокирована администратором       |
| 403  | `LICENSE_EXPIRED`   | activate, verify, transfer       | Срок действия истёк                                |
| 409  | `VERSION_MISMATCH`  | activate                         | `key_version` устарел — нужно получить новый ключ  |
| 409  | `DEVICE_MISMATCH`   | activate, verify, deactivate, transfer | Другое устройство                            |
| 409  | `NOT_ACTIVATED`     | verify, deactivate, transfer     | Лицензия не активирована                           |

---

## Примеры интеграции

### Python

```python
import hashlib, hmac, time, uuid, json
import httpx

BASE_URL   = "https://your-server.example.com"
API_SECRET = "замените-на-ваш-секрет"


def sign(method: str, path: str, body: bytes) -> dict:
    ts    = str(int(time.time()))
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    sts = f"{method}\n{path}\n{ts}\n{nonce}\n{body_hash}"
    sig = hmac.new(API_SECRET.encode(), sts.encode(), hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": sig,
            "Content-Type": "application/json"}


def api_post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    resp = httpx.post(BASE_URL + path, content=body, headers=sign("POST", path, body))
    return resp.json()


def api_get(path: str, params: dict = None) -> dict:
    body = b""
    resp = httpx.get(BASE_URL + path, params=params,
                     headers=sign("GET", path, body))
    return resp.json()


# Активация
result = api_post("/api/activate", {
    "key":       "ABCD-1234-EFGH",
    "device_id": "my-device-uuid",
})
print(result)

# Периодическая проверка
check = api_post("/api/verify", {
    "key":       "ABCD-1234-EFGH",
    "device_id": "my-device-uuid",
})
if check.get("valid"):
    print("Лицензия активна")
else:
    print("Ошибка:", check.get("code"))
```

### Dart / Flutter

```dart
import 'dart:convert';
import 'dart:math';
import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

// pubspec.yaml: crypto: ^3.0.3, http: ^1.2.0

const baseUrl   = 'https://your-server.example.com';
const apiSecret = 'замените-на-ваш-секрет'; // храните в flutter_secure_storage!

Map<String, String> _sign(String method, String path, List<int> bodyBytes) {
  final ts    = (DateTime.now().millisecondsSinceEpoch ~/ 1000).toString();
  final nonce = List.generate(16, (_) => Random.secure().nextInt(256))
      .map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  final bodyHash  = sha256.convert(bodyBytes).toString();
  final sts       = '$method\n$path\n$ts\n$nonce\n$bodyHash';
  final signature = Hmac(sha256, utf8.encode(apiSecret))
      .convert(utf8.encode(sts)).toString();
  return {
    'X-Timestamp': ts, 'X-Nonce': nonce, 'X-Signature': signature,
    'Content-Type': 'application/json',
  };
}

Future<Map<String, dynamic>> apiPost(String path, Map body) async {
  final bodyBytes = utf8.encode(jsonEncode(body));
  final resp = await http.post(Uri.parse('$baseUrl$path'),
      headers: _sign('POST', path, bodyBytes), body: bodyBytes);
  return jsonDecode(resp.body);
}

// Проверка при запуске приложения
Future<bool> checkLicense(String key, String deviceId) async {
  final result = await apiPost('/api/verify', {'key': key, 'device_id': deviceId});
  return result['valid'] == true;
}
```

### curl (bash)

```bash
#!/bin/bash
BASE_URL="https://your-server.example.com"
SECRET="замените-на-ваш-секрет"
KEY="ABCD-1234-EFGH"
DEVICE="my-device-uuid"

BODY=$(echo -n "{\"key\":\"$KEY\",\"device_id\":\"$DEVICE\"}")
TIMESTAMP=$(date +%s)
NONCE=$(openssl rand -hex 16)
BODY_HASH=$(echo -n "$BODY" | sha256sum | cut -d' ' -f1)
STS="POST\n/api/verify\n${TIMESTAMP}\n${NONCE}\n${BODY_HASH}"
SIG=$(printf "$STS" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)

curl -s -X POST "$BASE_URL/api/verify" \
  -H "Content-Type: application/json" \
  -H "X-Timestamp: $TIMESTAMP" \
  -H "X-Nonce: $NONCE" \
  -H "X-Signature: $SIG" \
  -d "$BODY"
```

---

## Рекомендации по безопасности

- **HTTPS** — обязателен в production. Без TLS подпись не защищает от прослушивания.
- **API_SECRET** — минимум 32 байт случайных данных. Генерация: `openssl rand -hex 32`.
  Храните в переменной окружения (`API_SECRET=...`) или в `config/app.cfg`. В коде приложения — только через `flutter_secure_storage` / Keychain / Keystore.
- **Nonce** — UUID4 или `Random.secure()`. Никогда не повторяйте одно значение.
- **Timestamp** — берите серверное UTC-время. Допуск ±30 сек позволяет небольшое расхождение часов устройства.
- **Ротация секрета** — при компрометации смените `api_secret` в конфиге, перезапустите сервер. Все клиенты должны получить новый секрет через защищённый канал.

---

## Отключение проверки подписи (только для разработки)

```ini
# config/security.cfg
[api_signing]
enabled = false
```

> **Никогда не отключайте в production.**
