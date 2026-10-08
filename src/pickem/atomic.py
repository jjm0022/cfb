"""Atomic file writes: a reader never sees half a file."""

from __future__ import annotations

import tempfile
from pathlib import Path


def write_atomic(path: Path, data: str | bytes) -> None:
    """Write ``data`` to ``path`` by way of a unique temp file beside it, then rename.

    The temp file is unique so concurrent writers never collide on one name, and
    it sits in the same folder so the rename stays on one filesystem. Text is
    written as UTF-8; bytes as given. A missing parent folder is created. If the
    write or the rename fails, the temp file is removed and the error re-raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = isinstance(data, str)
    tmp = tempfile.NamedTemporaryFile(
        mode="w" if text else "wb", encoding="utf-8" if text else None,
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
    )
    try:
        with tmp:
            tmp.write(data)
        Path(tmp.name).replace(path)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
