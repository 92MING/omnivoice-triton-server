from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Any


def _level_name(log_level: str) -> str:
    return log_level.upper() if log_level else "INFO"


def build_logging_config(
    log_level: str,
    log_file: str,
    retention_days: int = 7,
) -> dict[str, Any]:
    path = Path(log_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_count = max(0, int(retention_days))
    level = _level_name(log_level)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)s [%(process)d] [%(name)s] %(message)s",
            },
            "access": {
                "format": "%(asctime)s %(levelname)s [%(process)d] [%(name)s] %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "concurrent_log_handler.ConcurrentTimedRotatingFileHandler",
                "formatter": "default",
                "filename": str(path),
                "when": "midnight",
                "interval": 1,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "use_gzip": False,
            },
        },
        "root": {"level": level, "handlers": ["console", "file"]},
        "loggers": {
            "uvicorn": {"level": level, "handlers": ["console", "file"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["console", "file"], "propagate": False},
            "uvicorn.access": {"level": level, "handlers": ["console", "file"], "propagate": False},
        },
    }


def configure_logging(log_level: str, log_file: str, retention_days: int = 7) -> None:
    logging.config.dictConfig(build_logging_config(log_level, log_file, retention_days))
