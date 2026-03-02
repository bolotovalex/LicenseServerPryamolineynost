"""
Настройка логирования приложения.
Читает параметры из logging_config (config/logging.cfg).
Вызывать setup_logging() один раз при старте.
"""
import logging
import logging.handlers
from pathlib import Path

from app.config import logging_config


def setup_logging() -> None:
    logs_dir = Path(logging_config.file).parent
    logs_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, logging_config.level.upper(), logging.INFO)
    fmt = logging.Formatter(logging_config.format)

    # ── корневой логгер ───────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)

    # консоль
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    # ── logs/app.log (rotating) ───────────────────────────────────────────────
    app_handler = logging.handlers.RotatingFileHandler(
        filename=logging_config.file,
        maxBytes=logging_config.max_bytes,
        backupCount=logging_config.backup_count,
        encoding="utf-8",
    )
    app_handler.setFormatter(fmt)
    root.addHandler(app_handler)

    # ── logs/audit.log (отдельный логгер) ────────────────────────────────────
    if logging_config.audit_enabled:
        audit_path = Path(logging_config.audit_file)
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        audit_logger = logging.getLogger("audit")
        audit_logger.setLevel(logging.INFO)
        audit_logger.propagate = False  # не дублировать в корневой

        if not audit_logger.handlers:
            ah = logging.FileHandler(audit_path, encoding="utf-8")
            ah.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
            audit_logger.addHandler(ah)
