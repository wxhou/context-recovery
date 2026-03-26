import os
import tempfile
from pathlib import Path


def safe_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path atomically via temp file + rename.

    Safe against partial-write corruption on crash/kill.
    The temp file is created in the same directory as the target
    so rename() is always on the same filesystem.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file in the same directory (guarantees same filesystem)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(parent),
        prefix=".tmp_",
        suffix="_" + path.name,
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        # Atomic rename
        os.replace(tmp_path_str, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
