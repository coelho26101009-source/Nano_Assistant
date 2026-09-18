"""Small durable replacement for Nano-owned configuration files."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_bytes(path: Path, payload: bytes) -> None:
    """Keep the previous file intact unless a complete replacement is ready."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
