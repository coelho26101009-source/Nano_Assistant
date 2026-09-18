"""Nano Assistant Logger — structured, colored in the terminal, rotating file.

The log lives in the durable user-data directory, so installed builds never
need to write into their application resources. Formatters redact credentials
after formatting, including exception traceback text.
"""

import logging
import logging.handlers
import copy

from core.app_paths import DATA_DIR
from core.log_safety import redact_log_text

LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "nano.log"

COLORS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
    "RESET":    "\033[0m",
}


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


class ColorFormatter(SafeFormatter):
    FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        record = copy.copy(record)
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]
        record.levelname = f"{color}{record.levelname}{reset}"
        return SafeFormatter(self.FMT, datefmt="%H:%M:%S").format(record)


def setup_logger(level: int = logging.INFO):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # "helios" is a legacy logger namespace still used by core.config,
    # core.memory, core.wake_word and the plugins. It is configured here so
    # those modules keep logging to the same file while the rename proceeds.
    for log_name in ("nano", "helios"):
        root = logging.getLogger(log_name)
        root.setLevel(level)

        if root.handlers:
            continue

        sh = logging.StreamHandler()
        sh.setFormatter(ColorFormatter())
        root.addHandler(sh)

        fh = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(SafeFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(fh)
