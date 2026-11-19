"""Decode repository text files, including UTF-16 ones (e.g. `pip freeze > requirements.txt`
in Windows PowerShell writes UTF-16 with a byte-order mark)."""

from __future__ import annotations

import codecs

_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def detect_encoding(data: bytes) -> str | None:
    """The encoding named by a byte-order mark, or None."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    return None


def decode_text(data: bytes) -> str | None:
    """Text content, or None for binary data. Never raises."""
    encoding = detect_encoding(data)
    if encoding is not None:
        return data.decode(encoding, errors="replace")
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")
