"""Nano Assistant Logger — structured, colored in the terminal, rotating file.

The log lives in logs/nano.log (a folder ignored by Git) instead of the repo
root, so that files generated at runtime do not pollute the project tree or
show up as uncommitted changes.
"""

import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_PATH = LOG_DIR / "nano.log"

COLORS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
    "RESET":    "\033[0m",
}


class ColorFormatter(logging.Formatter):
    FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]
        record.levelname = f"{color}{record.levelname}{reset}"
        return logging.Formatter(self.FMT, datefmt="%H:%M:%S").format(record)


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
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(fh)
