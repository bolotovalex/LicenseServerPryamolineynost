import secrets
import string
from app.config import security_config


def validate_password(password: str) -> list[str]:
    """Проверяет пароль по настройкам security_config. Возвращает список ошибок."""
    errors: list[str] = []
    min_len = security_config.password_min_length

    if len(password) < min_len:
        errors.append(f"Минимальная длина пароля — {min_len} символов")

    if security_config.password_require_uppercase and not any(c.isupper() for c in password):
        errors.append("Пароль должен содержать хотя бы одну заглавную букву")

    if security_config.password_require_lowercase and not any(c.islower() for c in password):
        errors.append("Пароль должен содержать хотя бы одну строчную букву")

    if security_config.password_require_digits and not any(c.isdigit() for c in password):
        errors.append("Пароль должен содержать хотя бы одну цифру")

    if security_config.password_require_special:
        special = security_config.password_special_chars
        if not any(c in special for c in password):
            errors.append(f"Пароль должен содержать хотя бы один спецсимвол ({special})")

    return errors


def generate_password(length: int = 16) -> str:
    """Генерирует криптостойкий пароль, гарантированно проходящий validate_password()."""
    rng = secrets.SystemRandom()
    special = security_config.password_special_chars

    # Собираем обязательные символы по каждому активному требованию
    required: list[str] = []
    if security_config.password_require_uppercase:
        required.append(rng.choice(string.ascii_uppercase))
    if security_config.password_require_lowercase:
        required.append(rng.choice(string.ascii_lowercase))
    if security_config.password_require_digits:
        required.append(rng.choice(string.digits))
    if security_config.password_require_special:
        required.append(rng.choice(special))

    # Алфавит для оставшихся позиций
    alphabet = string.ascii_letters + string.digits
    if security_config.password_require_special:
        alphabet += special

    remaining = max(length - len(required), 0)
    filler = [rng.choice(alphabet) for _ in range(remaining)]

    chars = required + filler
    rng.shuffle(chars)
    password = "".join(chars)

    # Гарантия: если длина required > length — пересобрать с нужной длиной
    assert not validate_password(password), f"generate_password() не прошёл валидацию: {validate_password(password)}"
    return password
