"""
Шифрование ответов API: AES-256-GCM.

Каждый ответ зашифрован ключом, производным от API_SECRET + X-Nonce запроса.
Без нужного nonce расшифровать ответ невозможно — replay полностью исключён.

Формат зашифрованного ответа:
    {
        "iv":    "<base64, 12 байт>",
        "ct":    "<base64, ciphertext + 16-байтный GCM auth tag>",
        "nonce": "<эхо X-Nonce из запроса>"
    }

Content-Type ответа: application/vnd.licserver.encrypted+json

Алгоритм:
    key = HMAC-SHA256(API_SECRET, "enc:" + nonce)   # 32 байта
    iv  = os.urandom(12)                             # 96-битный IV для GCM
    ct  = AES-256-GCM.encrypt(key, iv, plaintext, aad=nonce.encode())

aad (Additional Authenticated Data) = nonce — привязывает ciphertext к
конкретному запросу: попытка использовать ct с другим nonce упадёт
с DecryptionError при проверке GCM auth tag.
"""
import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENCRYPTED_CONTENT_TYPE = "application/vnd.licserver.encrypted+json"


def derive_key(api_secret: str, nonce: str) -> bytes:
    """
    Производит 32-байтный AES-ключ из API_SECRET и nonce запроса.
    Разные nonce → разные ключи → каждый ответ уникален.
    """
    return hmac.new(
        api_secret.encode("utf-8"),
        f"enc:{nonce}".encode("utf-8"),
        hashlib.sha256,
    ).digest()


def encrypt_response(plaintext: bytes, api_secret: str, nonce: str) -> dict:
    """
    Шифрует тело ответа.

    :param plaintext:   исходный JSON-ответ в байтах
    :param api_secret:  API_SECRET сервера
    :param nonce:       X-Nonce из заголовка запроса
    :returns:           словарь {"iv": ..., "ct": ..., "nonce": ...}
    """
    key = derive_key(api_secret, nonce)
    iv  = os.urandom(12)                              # 96-бит — оптимально для GCM
    ct  = AESGCM(key).encrypt(iv, plaintext, nonce.encode("utf-8"))
    return {
        "iv":    base64.b64encode(iv).decode("ascii"),
        "ct":    base64.b64encode(ct).decode("ascii"),
        "nonce": nonce,                               # эхо для верификации клиентом
    }


def decrypt_response(payload: dict, api_secret: str) -> bytes:
    """
    Расшифровывает ответ сервера (вспомогательная функция для клиентов на Python).

    :param payload:    словарь {"iv": ..., "ct": ..., "nonce": ...}
    :param api_secret: тот же API_SECRET, что у сервера
    :returns:          исходный JSON в байтах
    :raises:           cryptography.exceptions.InvalidTag если данные подделаны
    """
    nonce = payload["nonce"]
    key   = derive_key(api_secret, nonce)
    iv    = base64.b64decode(payload["iv"])
    ct    = base64.b64decode(payload["ct"])
    return AESGCM(key).decrypt(iv, ct, nonce.encode("utf-8"))
