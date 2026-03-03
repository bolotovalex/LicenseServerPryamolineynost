"""
Подпись API-запросов: HMAC-SHA256.

Строка для подписи (клиент формирует):
    METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nSHA256(BODY)

Пример:
    POST\\n/api/activate\\n1710000000\\nabc123\\ne3b0c44...

Подпись:
    HMAC-SHA256(string_to_sign, API_SECRET).hexdigest()

Заголовки запроса:
    X-Timestamp  — Unix timestamp (int, секунды UTC)
    X-Nonce      — уникальная строка (UUID4 или random hex, длина ≤ 128)
    X-Signature  — hex-строка HMAC-SHA256

Настройки читаются из config/security.cfg:
    [api_signing]
    enabled = true
    timestamp_tolerance_seconds = 30

API-секрет читается из config/app.cfg (или env API_SECRET):
    [app]
    api_secret = CHANGE_ME_API_SECRET
"""
import asyncio
import hashlib
import hmac
import time
from typing import Optional

from fastapi import Request


# ── Коды ошибок ───────────────────────────────────────────────────────────────

ERRORS = {
    "TIMESTAMP_EXPIRED": "Временная метка устарела или некорректна",
    "NONCE_REUSED":      "Nonce уже использован (повторный запрос)",
    "INVALID_SIGNATURE": "Подпись запроса неверна",
}


# ── Исключение — обрабатывается хендлером в main.py ──────────────────────────

class APISignatureError(Exception):
    """Возникает при неудачной проверке подписи. Обрабатывается в main.py."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.reason = ERRORS.get(code, "Ошибка подписи")
        super().__init__(code)


# ── NonceStore — защита от replay-атак ───────────────────────────────────────

class NonceStore:
    """
    Хранит использованные nonce до истечения их TTL.

    check_and_store() возвращает True (нonce новый, запрос разрешён)
    или False (nonce уже встречался — повторный запрос, отклонить).

    Фоновая задача _cleanup_loop() каждые 30 секунд удаляет просроченные
    записи, чтобы словарь не рос неограниченно.
    """

    def __init__(self) -> None:
        self._store: dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None

    def check_and_store(self, nonce: str, ttl: int = 60) -> bool:
        """
        Проверяет nonce и сохраняет его.

        :param nonce: уникальная строка из заголовка X-Nonce
        :param ttl:   время жизни записи в секундах
        :returns:     True  — nonce новый (продолжить обработку)
                      False — nonce уже использован (отклонить)
        """
        now = time.time()
        if nonce in self._store:
            return False
        self._store[nonce] = now + ttl
        return True

    async def _cleanup_loop(self) -> None:
        """Фоновая задача: удаляет просроченные nonce каждые 30 секунд."""
        while True:
            await asyncio.sleep(30)
            now = time.time()
            expired = [k for k, exp in list(self._store.items()) if exp <= now]
            for k in expired:
                del self._store[k]

    def start_cleanup(self) -> None:
        """Запускает фоновую задачу очистки (вызывается в startup приложения)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._cleanup_loop())


nonce_store = NonceStore()  # синглтон


# ── Вычисление подписи ────────────────────────────────────────────────────────

def compute_signature(
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body_bytes: bytes,
    secret: str,
) -> str:
    """
    Вычисляет HMAC-SHA256 подпись запроса.

    Строка для подписи::

        METHOD\\nPATH\\nTIMESTAMP\\nNONCE\\nSHA256(BODY)

    :param method:     HTTP-метод в верхнем регистре, например "POST"
    :param path:       путь без query string, например "/api/activate"
    :param timestamp:  строка из заголовка X-Timestamp (Unix секунды)
    :param nonce:      строка из заголовка X-Nonce
    :param body_bytes: тело запроса в байтах (для GET — b"")
    :param secret:     API_SECRET в виде строки
    :returns:          hex-строка HMAC-SHA256
    """
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    string_to_sign = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
    return hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ── Проверка входящего запроса ────────────────────────────────────────────────

def verify_request(
    request: Request,
    body_bytes: bytes,
    secret: str,
    tolerance: int = 30,
) -> Optional[str]:
    """
    Проверяет подпись входящего запроса.

    Последовательность проверок:
    1. X-Timestamp: |now - timestamp| <= tolerance
    2. X-Nonce: не использовался ранее (через NonceStore)
    3. X-Signature: совпадает с вычисленной подписью

    :returns: None если всё OK, иначе код ошибки из ERRORS
    """
    timestamp_str = request.headers.get("X-Timestamp", "")
    nonce         = request.headers.get("X-Nonce", "")
    signature     = request.headers.get("X-Signature", "")

    # 1. Временна́я метка
    try:
        timestamp = int(timestamp_str)
    except (ValueError, TypeError):
        return "TIMESTAMP_EXPIRED"

    if abs(time.time() - timestamp) > tolerance:
        return "TIMESTAMP_EXPIRED"

    # 2. Nonce (TTL = tolerance * 2, чтобы nonce жил чуть дольше окна)
    if not nonce_store.check_and_store(nonce, ttl=tolerance * 2):
        return "NONCE_REUSED"

    # 3. Подпись
    expected = compute_signature(
        method=request.method,
        path=request.url.path,
        timestamp=timestamp_str,
        nonce=nonce,
        body_bytes=body_bytes,
        secret=secret,
    )
    if not hmac.compare_digest(expected, signature):
        return "INVALID_SIGNATURE"

    return None


# ── FastAPI Dependency ────────────────────────────────────────────────────────

async def verify_api_signature(request: Request) -> None:
    """
    FastAPI Dependency для роутера публичного API.

    - Если api_signing_enabled = False в config/security.cfg — пропускает проверку.
    - При ошибке raises APISignatureError (обрабатывается хендлером в main.py).
    - Тело запроса читается через request.body() и кешируется Starlette,
      поэтому Pydantic-модель в эндпоинте получает те же байты без повторного чтения.
    """
    from app.config import app_config, security_config

    if not security_config.api_signing_enabled:
        return

    body_bytes = await request.body()
    error_code = verify_request(
        request=request,
        body_bytes=body_bytes,
        secret=app_config.api_secret,
        tolerance=security_config.timestamp_tolerance_seconds,
    )
    if error_code:
        raise APISignatureError(error_code)
