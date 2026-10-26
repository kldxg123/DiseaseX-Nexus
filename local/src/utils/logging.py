from __future__ import annotations

import logging
from pathlib import Path

from src.remote.sanitizer import sanitize_text


class SanitizingFormatter(logging.Formatter):
    def __init__(self, *args, secrets: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.secrets = secrets or []

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_text(super().format(record), self.secrets)


def get_logger(name: str = "ebola_step", log_file: str | Path | None = None, secrets: list[str] | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    formatter = SanitizingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", secrets=secrets)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger
