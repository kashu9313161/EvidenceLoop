"""Validated ZIP extraction: list every entry, decide what to keep, then extract only that.

Used for GitHub zipballs of small repositories (only the files the preflight selected) and for
uploaded ZIPs (classified here with the same rules; over the source limits the most important
files are kept, because asking again would mean uploading again).
"""

from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path

from core import config
from core.config import MAX_COMPRESSION_RATIO, MAX_LISTED_ENTRIES
from ingestion import rules
from ingestion.preflight import Entry, Plan, build_plan
from ingestion.rules import IngestionError, safe_relative_path

EXTRACT_CHUNK_SIZE = 1024 * 1024


def _detect_archive_root(entries: list[zipfile.ZipInfo]) -> str | None:
    """Return the single top-level folder every entry lives under, if any.

    GitHub zipballs wrap everything in `<owner>-<repo>-<sha>/`.
    """
    first_parts = set()
    for entry in entries:
        parts = entry.filename.split("/")
        if len(parts) < 2 or not parts[0]:
            return None
        first_parts.add(parts[0])
    return first_parts.pop() if len(first_parts) == 1 else None


def open_zip(source: bytes | Path) -> zipfile.ZipFile:
    import io

    try:
        return zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError) as exc:
        raise IngestionError("The uploaded file is not a valid ZIP archive.") from exc


def safe_extract_archive(source: bytes | Path, destination: Path, *,
                         only: set[str] | None = None, scope: str | None = None,
                         name: str = "") -> list[dict]:
    """List and validate every entry, then extract only the eligible files.

    Phase 1 (no disk writes) rejects the whole archive on: invalid ZIP, too many entries,
    unsafe paths, duplicate paths (case-insensitive), and, for the files to extract,
    encryption, broken metadata and suspicious compression ratios. Symlinks and special files
    are recorded as skipped and never created. Datasets, models, dependency folders, big files
    and anything unsupported are never decompressed.

    Phase 2 streams each chosen file to disk while counting the real decompressed bytes, so a
    ZIP whose headers lie about sizes still can't exceed the limits.

    `only`: extract exactly these paths (a GitHub preflight already chose them); the returned
    records then cover just those. Otherwise every entry gets a coverage record.
    """
    destination = Path(destination)
    archive = open_zip(source)
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_LISTED_ENTRIES:
            raise IngestionError(f"The archive lists more than {MAX_LISTED_ENTRIES:,} entries.")
        strip_root = _detect_archive_root(infos)
        seen: set[str] = set()
        by_path: dict[str, zipfile.ZipInfo] = {}
        entries: list[Entry] = []
        special: list[dict] = []

        for info in infos:
            relative_path = safe_relative_path(info.filename, strip_root=strip_root)
            if info.is_dir() or not relative_path:
                continue
            # Case-insensitive: on Windows/macOS "App.py" and "app.py" collide.
            key = relative_path.casefold()
            if key in seen:
                raise IngestionError(f"Archive contains duplicate paths: {relative_path}")
            seen.add(key)
            # Unix file type lives in the upper 16 bits of external_attr. 0 = regular file.
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if file_type == stat.S_IFLNK:
                entries.append(Entry(relative_path, info.file_size, "symlink"))
                continue
            if file_type not in {0, stat.S_IFREG}:
                special.append(_special(relative_path, info.file_size))
                continue
            by_path[relative_path] = info
            entries.append(Entry(relative_path, info.file_size))

        if only is not None:
            missing = only - by_path.keys()
            if missing:
                raise IngestionError(f"The archive is missing {sorted(missing)[0]}.")
            chosen = sorted(only)
            plan: Plan | None = None
        else:
            plan = build_plan(entries, name=name, scope=scope, auto_priority=True)
            chosen = [e.path for e in plan.selected]

        declared_total = 0
        for path in chosen:
            info = by_path[path]
            if info.flag_bits & 0x1:
                raise IngestionError(f"Encrypted ZIP entries are not supported: {path}")
            if info.file_size > 0 and info.compress_size == 0:
                raise IngestionError(f"Invalid compression metadata: {path}")
            if info.compress_size > 0 and info.file_size / info.compress_size > \
                    MAX_COMPRESSION_RATIO:
                raise IngestionError(f"Compression ratio exceeds limit: {path}")
            declared_total += info.file_size
        if declared_total > config.HARD_MAX_SOURCE_BYTES:
            raise IngestionError("Archive exceeds total uncompressed size limit.")

        hashes = _extract(archive, by_path, chosen, destination)

    if plan is None:
        return [{**_pending(path, *hashes[path])} for path in chosen]
    records = plan.records() + special
    for record in records:
        if record["relative_path"] in hashes:
            record["size_bytes"], record["content_sha256"] = hashes[record["relative_path"]]
    return records


def _special(path: str, size: int) -> dict:
    return {"relative_path": path, "extension": Path(path).suffix.lower(), "size_bytes": size,
            "content_sha256": None, "status": "skipped",
            "skip_reason": "Non-regular archive entry"}


def _pending(path: str, size: int, digest: str) -> dict:
    return {"relative_path": path, "extension": Path(path).suffix.lower(), "size_bytes": size,
            "content_sha256": digest, "status": "pending", "skip_reason": None}


def _extract(archive: zipfile.ZipFile, by_path: dict[str, zipfile.ZipInfo], chosen: list[str],
             destination: Path) -> dict[str, tuple[int, str]]:
    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()
    actual_total = 0
    hashes: dict[str, tuple[int, str]] = {}
    per_file_cap = max(rules.MAX_FILE_ANALYSIS_BYTES, 1)
    try:
        for path in chosen:
            info = by_path[path]
            output_path = destination.joinpath(*path.split("/"))
            if not output_path.resolve().is_relative_to(resolved_destination):
                raise IngestionError(f"Unsafe archive path: {info.filename!r}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with archive.open(info) as source, output_path.open("xb") as target:
                while chunk := source.read(EXTRACT_CHUNK_SIZE):
                    written += len(chunk)
                    actual_total += len(chunk)
                    if written > min(info.file_size, per_file_cap):
                        raise IngestionError(
                            f"Decompressed size exceeds declared size: {path}")
                    if actual_total > config.HARD_MAX_SOURCE_BYTES:
                        raise IngestionError("Archive exceeds total uncompressed size limit.")
                    digest.update(chunk)
                    target.write(chunk)
            if written != info.file_size:
                raise IngestionError(f"Archive size mismatch for {path}")
            hashes[path] = (written, digest.hexdigest())
    except IngestionError:
        raise
    except (zipfile.BadZipFile, EOFError) as exc:
        raise IngestionError(f"Corrupted ZIP archive: {exc}") from exc
    except (OSError, RuntimeError, NotImplementedError) as exc:
        raise IngestionError(f"Failed to extract ZIP archive: {exc}") from exc
    return hashes
