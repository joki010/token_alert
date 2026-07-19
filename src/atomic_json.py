"""Small, durable JSON persistence helpers used by the watcher runtime."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from *path*, returning ``default`` for missing/invalid files."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:
    """Atomically replace *path* with UTF-8 JSON mode 0600.

    The temporary file is created beside the destination so ``os.replace`` is
    atomic on the same filesystem.  Both the file contents and the containing
    directory are flushed before returning.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    # Directory fsync is not available on every supported OS;
                    # the file itself was already flushed before replacement.
                    pass
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


atomic_write_json = write_json
load_json = read_json
