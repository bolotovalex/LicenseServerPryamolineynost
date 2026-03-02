"""
Загрузка конфигурации из config/*.cfg через configparser.
Переменные окружения имеют приоритет над значениями в файлах.
"""
import logging
import os
from configparser import ConfigParser
from pathlib import Path

logger = logging.getLogger(__name__)

# Корень проекта — директория, где лежит папка config/
_BASE_DIR = Path(__file__).parent.parent
_CONFIG_DIR = _BASE_DIR / "config"


def _load(filename: str, defaults: dict) -> ConfigParser:
    """Загружает .cfg файл; при отсутствии возвращает дефолты с предупреждением."""
    # interpolation=None — отключаем подстановку %(var)s, чтобы значения вроде
    # special_chars = !@#$%^... не вызывали ValueError
    cfg = ConfigParser(interpolation=None)
    # Заполняем дефолты — ConfigParser использует их как fallback для любой секции
    for section, values in defaults.items():
        cfg[section] = values

    path = _CONFIG_DIR / filename
    if not path.exists():
        logger.warning("Конфигурационный файл не найден: %s — используются дефолты", path)
        return cfg

    cfg.read(path, encoding="utf-8")
    return cfg


# ------------------------------------------------------------------ app.cfg --
_app_cfg = _load(
    "app.cfg",
    {
        "app": {
            "name": "License Server",
            "secret_key": "CHANGE_ME_PLEASE",
            "api_secret": "CHANGE_ME_API_SECRET",
            "jwt_algorithm": "HS256",
            "token_expires_hours": "8",
            "debug": "false",
        }
    },
)


class _AppConfig:
    @property
    def name(self) -> str:
        return _app_cfg.get("app", "name")

    @property
    def secret_key(self) -> str:
        return os.environ.get("SECRET_KEY") or _app_cfg.get("app", "secret_key")

    @property
    def api_secret(self) -> str:
        return os.environ.get("API_SECRET") or _app_cfg.get("app", "api_secret")

    @property
    def jwt_algorithm(self) -> str:
        return _app_cfg.get("app", "jwt_algorithm")

    @property
    def token_expires_minutes(self) -> int:
        hours = _app_cfg.getint("app", "token_expires_hours", fallback=8)
        return hours * 60

    @property
    def debug(self) -> bool:
        return _app_cfg.getboolean("app", "debug", fallback=False)


app_config = _AppConfig()


# -------------------------------------------------------------- database.cfg --
_db_cfg = _load(
    "database.cfg",
    {
        "database": {
            "url": "sqlite+aiosqlite:///./data/licserver.db",
            "echo_sql": "false",
            "pool_size": "5",
            "max_overflow": "10",
        }
    },
)


class _DbConfig:
    @property
    def url(self) -> str:
        return os.environ.get("DATABASE_URL") or _db_cfg.get("database", "url")

    @property
    def echo_sql(self) -> bool:
        return _db_cfg.getboolean("database", "echo_sql", fallback=False)

    @property
    def pool_size(self) -> int:
        return _db_cfg.getint("database", "pool_size", fallback=5)

    @property
    def max_overflow(self) -> int:
        return _db_cfg.getint("database", "max_overflow", fallback=10)


db_config = _DbConfig()


# ----------------------------------------------------------------- smtp.cfg --
_smtp_cfg = _load(
    "smtp.cfg",
    {
        "smtp": {
            "host": "smtp.gmail.com",
            "port": "587",
            "user": "",
            "password": "",
            "from": "noreply@example.com",
            "tls": "true",
            "enabled": "false",
        }
    },
)


class _SmtpConfig:
    @property
    def host(self) -> str:
        return _smtp_cfg.get("smtp", "host")

    @property
    def port(self) -> int:
        return _smtp_cfg.getint("smtp", "port", fallback=587)

    @property
    def user(self) -> str:
        return os.environ.get("SMTP_USER") or _smtp_cfg.get("smtp", "user")

    @property
    def password(self) -> str:
        return os.environ.get("SMTP_PASSWORD") or _smtp_cfg.get("smtp", "password")

    @property
    def from_addr(self) -> str:
        return _smtp_cfg.get("smtp", "from")

    @property
    def tls(self) -> bool:
        return _smtp_cfg.getboolean("smtp", "tls", fallback=True)

    @property
    def enabled(self) -> bool:
        return _smtp_cfg.getboolean("smtp", "enabled", fallback=False)


smtp_config = _SmtpConfig()


# ------------------------------------------------------------- security.cfg --
_sec_cfg = _load(
    "security.cfg",
    {
        "brute_force": {
            "max_attempts": "5",
            "lockout_minutes": "15",
            "attempt_window_minutes": "10",
        },
        "password": {
            "min_length": "8",
            "require_uppercase": "true",
            "require_lowercase": "true",
            "require_digits": "true",
            "require_special": "true",
            "special_chars": "!@#$%^&*()_+-=[]{}|;:,.<>?",
        },
        "api_signing": {
            "enabled": "true",
            "timestamp_tolerance_seconds": "30",
        },
    },
)


class _SecurityConfig:
    # brute_force
    @property
    def max_attempts(self) -> int:
        return _sec_cfg.getint("brute_force", "max_attempts", fallback=5)

    @property
    def lockout_minutes(self) -> int:
        return _sec_cfg.getint("brute_force", "lockout_minutes", fallback=15)

    @property
    def attempt_window_minutes(self) -> int:
        return _sec_cfg.getint("brute_force", "attempt_window_minutes", fallback=10)

    # password
    @property
    def password_min_length(self) -> int:
        return _sec_cfg.getint("password", "min_length", fallback=8)

    @property
    def password_require_uppercase(self) -> bool:
        return _sec_cfg.getboolean("password", "require_uppercase", fallback=True)

    @property
    def password_require_lowercase(self) -> bool:
        return _sec_cfg.getboolean("password", "require_lowercase", fallback=True)

    @property
    def password_require_digits(self) -> bool:
        return _sec_cfg.getboolean("password", "require_digits", fallback=True)

    @property
    def password_require_special(self) -> bool:
        return _sec_cfg.getboolean("password", "require_special", fallback=True)

    @property
    def password_special_chars(self) -> str:
        return _sec_cfg.get("password", "special_chars", fallback="!@#$%^&*()_+-=[]{}|;:,.<>?")

    # api_signing
    @property
    def api_signing_enabled(self) -> bool:
        return _sec_cfg.getboolean("api_signing", "enabled", fallback=True)

    @property
    def timestamp_tolerance_seconds(self) -> int:
        return _sec_cfg.getint("api_signing", "timestamp_tolerance_seconds", fallback=30)


security_config = _SecurityConfig()


# ------------------------------------------------------------- logging.cfg --
_log_cfg = _load(
    "logging.cfg",
    {
        "logging": {
            "level": "INFO",
            "file": "logs/app.log",
            "max_bytes": "10485760",
            "backup_count": "5",
            "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        },
        "audit": {
            "enabled": "true",
            "file": "logs/audit.log",
            "db_enabled": "true",
        },
    },
)


class _LoggingConfig:
    @property
    def level(self) -> str:
        return _log_cfg.get("logging", "level", fallback="INFO")

    @property
    def file(self) -> str:
        return _log_cfg.get("logging", "file", fallback="logs/app.log")

    @property
    def max_bytes(self) -> int:
        return _log_cfg.getint("logging", "max_bytes", fallback=10485760)

    @property
    def backup_count(self) -> int:
        return _log_cfg.getint("logging", "backup_count", fallback=5)

    @property
    def format(self) -> str:
        return _log_cfg.get("logging", "format", fallback="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    @property
    def audit_enabled(self) -> bool:
        return _log_cfg.getboolean("audit", "enabled", fallback=True)

    @property
    def audit_file(self) -> str:
        return _log_cfg.get("audit", "file", fallback="logs/audit.log")

    @property
    def audit_db_enabled(self) -> bool:
        return _log_cfg.getboolean("audit", "db_enabled", fallback=True)


logging_config = _LoggingConfig()
