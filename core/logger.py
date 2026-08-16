"""Nano Assistant Logger — estruturado, colorido no terminal, ficheiro nano.log."""

import logging
import logging.handlers
from pathlib import Path

LOG_PATH = Path(__file__).parent.parent / "nano.log"

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
