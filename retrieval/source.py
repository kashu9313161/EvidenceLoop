"""Reading a snapshot's files by line, and hashing line ranges so citations can be checked."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core.textio import decode_text
from storage.database import get_connection, get_snapshot, snapshot_root

MAX_READ_BYTES = 2 * 1024 * 1024


def range_sha256(lines: list[str], start: int, end: int) -> str:
    """The hash of lines start..end (1-based, inclusive), joined with newlines."""
    return hashlib.sha256("\n".join(lines[start - 1:end]).encode("utf-8")).hexdigest()


class SnapshotFiles:
    """The files of one snapshot: which paths exist, and their lines (read once, then kept)."""

    def __init__(self, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id
        snapshot = get_snapshot(snapshot_id)
        self.root: Path | None = snapshot_root(snapshot) if snapshot is not None else None
        with get_connection() as connection:
            self.paths = {
                row["relative_path"] for row in connection.execute(
                    "SELECT relative_path FROM files WHERE snapshot_id = ? AND status != 'skipped'",
                    (snapshot_id,))
            }
        self._lines: dict[str, list[str] | None] = {}

    def has(self, path: str) -> bool:
        return path in self.paths

    def lines(self, path: str) -> list[str] | None:
        """The file's lines, or None when it isn't in the snapshot or can't be read as text."""
        if path not in self._lines:
            self._lines[path] = self._read(path)
        return self._lines[path]

    def _read(self, path: str) -> list[str] | None:
        if self.root is None or path not in self.paths:
            return None
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            return None
        data = target.read_bytes()
        if len(data) > MAX_READ_BYTES:
            return None
        text = decode_text(data)
        return text.splitlines() if text is not None else None
