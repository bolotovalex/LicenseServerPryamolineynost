# Подпись API-запросов (HMAC-SHA256)

Все запросы к публичному API (`/api/*`) защищены HMAC-SHA256 подписью.
Подпись предотвращает подделку запросов (MITM) и replay-атаки.

Функцию можно отключить в `config/security.cfg`:

```ini
[api_signing]
enabled = false
```

---

## Алгоритм шаг за шагом

### 1. Подготовьте компоненты

| Компонент   | Источник                                                  | Пример                             |
|-------------|-----------------------------------------------------------|------------------------------------|
| `METHOD`    | HTTP-метод в верхнем регистре                             | `POST`                             |
| `PATH`      | Путь URL без query string                                 | `/api/activate`                    |
| `TIMESTAMP` | Unix timestamp (целое, UTC, секунды)                      | `1710000000`                       |
| `NONCE`     | Случайная уникальная строка (UUID4 или hex, длина ≤ 128)  | `a1b2c3d4e5f6`                     |
| `BODY_HASH` | SHA-256 от тела запроса в hex (для GET — SHA-256 пустых байт) | `e3b0c44298fc1c149afb...`      |
| `SECRET`    | `API_SECRET` из `config/app.cfg` или env `API_SECRET`     | `my-super-secret`                  |

SHA-256 пустой строки (для GET-запросов без тела):
```
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

### 2. Составьте строку для подписи

Соедините компоненты через символ перевода строки `\n`:

```
METHOD\nPATH\nTIMESTAMP\nNONCE\nBODY_HASH
```

Пример для `POST /api/activate`:

```
POST
/api/activate
1710000000
a1b2c3d4e5f6
9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
```

### 3. Вычислите подпись

```
SIGNATURE = HMAC-SHA256(string_to_sign, API_SECRET).hexdigest()
```

Ключ и сообщение кодируются в UTF-8 перед вычислением.

### 4. Добавьте заголовки к запросу

```
X-Timestamp: 1710000000
X-Nonce:     a1b2c3d4e5f6
X-Signature: <hex-строка HMAC-SHA256>
```

---

## Заголовки запроса

| Заголовок     | Тип    | Обязателен | Описание                                              | Пример                     |
|---------------|--------|------------|-------------------------------------------------------|----------------------------|
| `X-Timestamp` | int    | да         | Unix timestamp UTC в секундах. Допуск ±30 сек.        | `1710000000`               |
| `X-Nonce`     | string | да         | Уникальная строка, не более 128 символов              | `a1b2c3d4e5f6`             |
| `X-Signature` | string | да         | HMAC-SHA256 hex lowercase                             | `3a7bd3e2360...`           |
| `Content-Type`| string | для POST   | Тип тела запроса                                      | `application/json`         |

---

## Коды ошибок

При неверной подписи сервер возвращает **HTTP 401** с телом:

```json
{
  "status": "error",
  "reason": "Временная метка устарела или некорректна",
  "code": "TIMESTAMP_EXPIRED"
}
```

| Код                  | HTTP | Причина                                                        |
|----------------------|------|----------------------------------------------------------------|
| `TIMESTAMP_EXPIRED`  | 401  | `X-Timestamp` отсутствует, не число, или отклонение > 30 сек. |
| `NONCE_REUSED`       | 401  | Этот `X-Nonce` уже использовался в течение окна допуска.       |
| `INVALID_SIGNATURE`  | 401  | Вычисленная подпись не совпадает с `X-Signature`.              |

Прочие коды ошибок бизнес-логики (`LICENSE_NOT_FOUND`, `LICENSE_BLOCKED` и др.) возвращают отдельные HTTP-коды (403, 404, 409).

---

## Пример кода на Dart

```dart
import 'dart:convert';
import 'dart:math';
import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

/// Вычисляет HMAC-SHA256 подпись запроса.
String computeSignature({
  required String method,
  required String path,
  required String timestamp,
  required String nonce,
  required List<int> bodyBytes,
  required String secret,
}) {
  final bodyHash = sha256.convert(bodyBytes).toString();
  final stringToSign = '$method\n$path\n$timestamp\n$nonce\n$bodyHash';
  final key = utf8.encode(secret);
  final msg = utf8.encode(stringToSign);
  return Hmac(sha256, key).convert(msg).toString();
}

/// Генерирует случайный nonce (hex-строка из 16 байт).
String generateNonce() {
  final rng = Random.secure();
  final bytes = List<int>.generate(16, (_) => rng.nextInt(256));
  return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
}

/// Выполняет POST-запрос с подписью.
Future<http.Response> signedPost({
  required String url,
  required Map<String, dynamic> body,
  required String apiSecret,
}) async {
  final uri = Uri.parse(url);
  final bodyBytes = utf8.encode(jsonEncode(body));
  final timestamp = (DateTime.now().millisecondsSinceEpoch ~/ 1000).toString();
  final nonce = generateNonce();

  final signature = computeSignature(
    method: 'POST',
    path: uri.path,
    timestamp: timestamp,
    nonce: nonce,
    bodyBytes: bodyBytes,
    secret: apiSecret,
  );

  return http.post(
    uri,
    headers: {
      'Content-Type': 'application/json',
      'X-Timestamp': timestamp,
      'X-Nonce': nonce,
      'X-Signature': signature,
    },
    body: bodyBytes,
  );
}

/// Выполняет GET-запрос с подписью (тело пустое).
Future<http.Response> signedGet({
  required String url,
  required String apiSecret,
}) async {
  final uri = Uri.parse(url);
  final timestamp = (DateTime.now().millisecondsSinceEpoch ~/ 1000).toString();
  final nonce = generateNonce();

  final signature = computeSignature(
    method: 'GET',
    path: uri.path,       // только путь, без query string
    timestamp: timestamp,
    nonce: nonce,
    bodyBytes: [],        // пустое тело
    secret: apiSecret,
  );

  return http.get(
    uri,
    headers: {
      'X-Timestamp': timestamp,
      'X-Nonce': nonce,
      'X-Signature': signature,
    },
  );
}

// ── Использование ──────────────────────────────────────────────────────────

Future<void> main() async {
  const baseUrl = 'https://your-server.example.com';
  const apiSecret = 'my-super-secret'; // храните в flutter_secure_storage!

  // Активация лицензии
  final activateResp = await signedPost(
    url: '$baseUrl/api/activate',
    body: {'key': 'XXXXX-XXXXX-XXXXX-XXXXX-XXXXX', 'device_id': 'device-uuid'},
    apiSecret: apiSecret,
  );
  print('Activate: ${activateResp.statusCode} ${activateResp.body}');

  // Проверка статуса
  final statusResp = await signedGet(
    url: '$baseUrl/api/status?key=XXXXX-XXXXX-XXXXX-XXXXX-XXXXX',
    apiSecret: apiSecret,
  );
  print('Status: ${statusResp.statusCode} ${statusResp.body}');
}
```

> **Зависимости Flutter** (добавить в `pubspec.yaml`):
> ```yaml
> dependencies:
>   crypto: ^3.0.3
>   http: ^1.2.0
> ```

---

## Примеры запросов (curl)

### POST /api/activate

```bash
TIMESTAMP=$(date +%s)
NONCE=$(openssl rand -hex 16)
BODY='{"key":"XXXXX-XXXXX-XXXXX-XXXXX-XXXXX","device_id":"my-device"}'
BODY_HASH=$(echo -n "$BODY" | sha256sum | cut -d' ' -f1)
STRING_TO_SIGN="POST\n/api/activate\n${TIMESTAMP}\n${NONCE}\n${BODY_HASH}"
SIGNATURE=$(echo -ne "$STRING_TO_SIGN" | openssl dgst -sha256 -hmac "my-super-secret" | cut -d' ' -f2)

curl -X POST https://your-server.example.com/api/activate \
  -H "Content-Type: application/json" \
  -H "X-Timestamp: $TIMESTAMP" \
  -H "X-Nonce: $NONCE" \
  -H "X-Signature: $SIGNATURE" \
  -d "$BODY"
```

### GET /api/status

```bash
TIMESTAMP=$(date +%s)
NONCE=$(openssl rand -hex 16)
BODY_HASH="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
STRING_TO_SIGN="GET\n/api/status\n${TIMESTAMP}\n${NONCE}\n${BODY_HASH}"
SIGNATURE=$(echo -ne "$STRING_TO_SIGN" | openssl dgst -sha256 -hmac "my-super-secret" | cut -d' ' -f2)

curl "https://your-server.example.com/api/status?key=XXXXX-XXXXX-XXXXX-XXXXX-XXXXX" \
  -H "X-Timestamp: $TIMESTAMP" \
  -H "X-Nonce: $NONCE" \
  -H "X-Signature: $SIGNATURE"
```

---

## Ответы эндпоинтов

### GET /api/status — успех (200)

```json
{
  "status": "ok",
  "is_blocked": false,
  "block_reason": null,
  "is_activated": true,
  "organization": "ООО Рога и Копыта",
  "description": "Основная лицензия",
  "activated_at": "2024-03-10T08:00:00",
  "expires_at": "permanent",
  "version": 1
}
```

### POST /api/activate — успех (200)

```json
{
  "status": "ok",
  "organization": "ООО Рога и Копыта",
  "description": "Основная лицензия",
  "activated_at": "2024-03-10T08:00:00",
  "expires_at": "permanent",
  "version": 1
}
```

### POST /api/transfer — успех (200)

```json
{
  "status": "ok",
  "new_key": "YYYYY-YYYYY-YYYYY-YYYYY-YYYYY",
  "organization": "ООО Рога и Копыта",
  "description": "Основная лицензия",
  "activated_at": null,
  "expires_at": "permanent",
  "version": 2
}
```

---

## Полная таблица кодов ошибок

| HTTP | code                  | Описание                                          |
|------|-----------------------|---------------------------------------------------|
| 401  | `TIMESTAMP_EXPIRED`   | Временная метка устарела (±30 сек) или не число   |
| 401  | `NONCE_REUSED`        | Nonce уже был использован                         |
| 401  | `INVALID_SIGNATURE`   | Подпись не совпадает                              |
| 404  | `LICENSE_NOT_FOUND`   | Лицензия с таким ключом не существует             |
| 403  | `LICENSE_BLOCKED`     | Лицензия заблокирована администратором            |
| 403  | `LICENSE_EXPIRED`     | Срок действия лицензии истёк                      |
| 409  | `DEVICE_MISMATCH`     | Лицензия активирована на другом устройстве        |
| 409  | `NOT_ACTIVATED`       | Лицензия ещё не активирована (для transfer)       |

---

## Рекомендации по безопасности

### Хранение API_SECRET в Flutter

Используйте пакет [`flutter_secure_storage`](https://pub.dev/packages/flutter_secure_storage) — он хранит секрет в iOS Keychain / Android Keystore:

```dart
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

final storage = FlutterSecureStorage();

// Сохранить при первом запуске (например, вшить в сборку через --dart-define)
await storage.write(key: 'api_secret', value: const String.fromEnvironment('API_SECRET'));

// Прочитать перед запросом
final secret = await storage.read(key: 'api_secret') ?? '';
```

Вшивать секрет в сборку через `--dart-define`:
```bash
flutter build apk --dart-define=API_SECRET=your-real-secret
```

### Генерация секрета

Для продакшена используйте криптографически случайный секрет длиной ≥ 32 байт:

```bash
openssl rand -hex 32
# или
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Запишите результат в `config/app.cfg`:
```ini
[app]
api_secret = <ваш-секрет>
```

Или в переменную окружения (приоритет над файлом):
```bash
export API_SECRET=<ваш-секрет>
```

### Дополнительно

- **Nonce** — используйте `Uuid().v4()` или `Random.secure()`. Никогда не повторяйте nonce в течение окна допуска (по умолчанию 60 сек).
- **Timestamp** — используйте UTC. На мобильных устройствах время может отставать; допуск ±30 сек позволяет небольшие расхождения.
- **HTTPS** — обязателен в продакшене. Без TLS подпись защищает от подделки, но не от прослушивания.
- **Ротация секрета** — при компрометации смените `api_secret` в конфиге и перезапустите сервер. Все клиенты должны получить новый секрет через защищённый канал.
