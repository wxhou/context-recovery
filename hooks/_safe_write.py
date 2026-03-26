#!/usr/bin/env python3
"""
Shared utilities for ContextRecoveryHook.
"""
import os
import tempfile
from pathlib import Path


def safe_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename.

    Safe against partial-write corruption on crash/kill.
    The temp file is created in the same directory as the target
    so rename() is always on the same filesystem.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".tmp_",
        suffix="_" + path.name,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path_str, str(path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
