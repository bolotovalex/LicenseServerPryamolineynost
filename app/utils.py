import io
import secrets
import string

import qrcode

# Алфавит ключа: заглавные буквы A-Z + цифры 0-9 (36 вариантов на символ)
_KEY_ALPHABET = string.ascii_uppercase + string.digits


def generate_license_key() -> str:
    """Генерирует ключ формата XXXX-XXXX-XXXX (12 символов, A-Z0-9).

    Использует secrets.choice() для криптографически безопасной случайности.
    Всего 36^12 ≈ 4.7 × 10^18 вариантов.
    """
    chars = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(12))
    return f"{chars[:4]}-{chars[4:8]}-{chars[8:12]}"


def make_qr_png(data: str) -> bytes:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
