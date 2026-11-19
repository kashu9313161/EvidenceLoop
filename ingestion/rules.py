"""Which files EvidenceLoop reads, and the path checks every written file passes.

Shared by the GitHub preflight (before anything is downloaded), ZIP and folder uploads, and the
web app (GET /api/config sends `source_rules()`, so a folder upload is filtered in the browser
with the same rules).
"""

import re
from pathlib import PurePosixPath

from core.config import (
    IGNORED_DIRECTORIES,
    IGNORED_FILE_NAMES,
    MAX_FILE_ANALYSIS_BYTES,
    SKIPPED_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    SVG_SKIP_BYTES,
    VENDOR_DIRECTORY,
    VENDOR_EXTENSIONS,
)

# Names that refer to devices on Windows, regardless of extension.
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

UNSUPPORTED = "Unsupported file type"
IGNORED_FILE = "Ignored file"
SYMLINK = "Symbolic links are not extracted"
SUBMODULE = "Submodule (another repository, not included)"
UNSAFE_NAME = "Unsafe file name"
CASE_DUPLICATE = "Same path as another file apart from letter case"
OUTSIDE_SCOPE = "Outside the chosen folder"
LEFT_OUT = "Left out: over the source-file limit"
# Source files that were eligible but not analysed because of the scope or the limit.
LEFT_OUT_REASONS = {OUTSIDE_SCOPE, LEFT_OUT}

_DIRECTORY_REASON = {name: reason for reason, names in IGNORED_DIRECTORIES.items()
                     for name in names}
_EXTENSION_REASON = {ext: reason for reason, exts in SKIPPED_EXTENSIONS.items() for ext in exts}


class IngestionError(Exception):
    """Raised when a repository cannot be safely ingested."""


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "bytes":
                return f"{int(value)} bytes"
            return f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover


def too_large_reason(limit: int | None = None) -> str:
    return f"Larger than {human_bytes(limit or MAX_FILE_ANALYSIS_BYTES)}"


def safe_relative_path(name: str, strip_root: str | None = None) -> str:
    """Validate an archive entry or tree path and return it as a relative POSIX path.

    Rejects (raises) anything that could escape the destination or that Windows would
    interpret specially. Returns "" if nothing is left after stripping the archive root.
    """

    if "\\" in name or "\x00" in name or "\n" in name or "\r" in name:
        raise IngestionError(f"Unsafe archive path: {name!r}")

    if re.match(r"^[A-Za-z]:", name):
        raise IngestionError(f"Windows absolute path is not allowed: {name!r}")

    path = PurePosixPath(name)

    if path.is_absolute() or not path.parts:
        raise IngestionError(f"Unsafe archive path: {name!r}")

    # PurePosixPath collapses "a//b" and "a/./b"; check the raw segments too.
    raw_parts = name.rstrip("/").split("/")

    if any(part in {"", ".", ".."} for part in raw_parts):
        raise IngestionError(f"Unsafe archive path: {name!r}")

    for part in raw_parts:
        if ":" in part:
            raise IngestionError(f"Colon in archive path is not allowed: {name!r}")

        if part != part.rstrip(" ."):
            raise IngestionError(f"Trailing dot or space is not allowed: {name!r}")

        if part.split(".")[0].lower() in WINDOWS_RESERVED_NAMES:
            raise IngestionError(f"Reserved device name in path: {name!r}")

    parts = tuple(raw_parts)

    if strip_root and parts[0] == strip_root:
        parts = parts[1:]

    return "/".join(parts)


def is_ignored_path(relative_path: str) -> bool:
    return _folder_reason(relative_path) is not None or \
        PurePosixPath(relative_path).name in IGNORED_FILE_NAMES


def _folder_reason(relative_path: str) -> str | None:
    parts = PurePosixPath(relative_path).parts
    for part in parts[:-1]:
        if part in _DIRECTORY_REASON:
            return _DIRECTORY_REASON[part]
    if VENDOR_DIRECTORY in parts[:-1] and \
            PurePosixPath(relative_path).suffix.lower() in VENDOR_EXTENSIONS:
        return "Installed dependencies"
    return None


def ignored_folder_reason(folder: str) -> str | None:
    """Why a whole folder is skipped (its last segment is an ignored name), or None. Used to
    avoid listing inside node_modules and the like when a tree has to be walked."""
    return _DIRECTORY_REASON.get(PurePosixPath(folder).name)


def skip_reason(relative_path: str, size: int | None = None,
                max_file_bytes: int | None = None) -> str | None:
    """Why this file isn't analysed, or None if it is. `size` (bytes) is checked when known."""
    folder = _folder_reason(relative_path)
    if folder:
        return folder
    name = PurePosixPath(relative_path).name
    if name in IGNORED_FILE_NAMES:
        return IGNORED_FILE
    extension = PurePosixPath(relative_path).suffix.lower()
    if extension in _EXTENSION_REASON:
        return _EXTENSION_REASON[extension]
    if extension == ".svg" and size is not None and size > SVG_SKIP_BYTES:
        return "Media or document"
    if extension not in SUPPORTED_EXTENSIONS:
        return UNSUPPORTED
    limit = max_file_bytes or MAX_FILE_ANALYSIS_BYTES
    if size is not None and size > limit:
        return too_large_reason(limit)
    return None


def classify_file(relative_path: str, size: int | None = None) -> tuple[str, str | None]:
    reason = skip_reason(relative_path, size)
    return ("skipped", reason) if reason else ("pending", None)


def source_rules() -> dict:
    """The rules above as data, for the web app's folder-upload filter."""
    return {
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "ignored_directories": {reason: sorted(names)
                                for reason, names in IGNORED_DIRECTORIES.items()},
        "vendor_directory": VENDOR_DIRECTORY,
        "vendor_extensions": sorted(VENDOR_EXTENSIONS),
        "skipped_extensions": {reason: sorted(exts)
                               for reason, exts in SKIPPED_EXTENSIONS.items()},
        "ignored_file_names": sorted(IGNORED_FILE_NAMES),
        "svg_skip_bytes": SVG_SKIP_BYTES,
        "max_file_bytes": MAX_FILE_ANALYSIS_BYTES,
    }
