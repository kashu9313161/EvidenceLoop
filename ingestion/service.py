"""Repository ingestion: GitHub, ZIP or folder input -> validated, immutable snapshot.

The repository is treated as untrusted input. Nothing in it is executed. For GitHub, a preflight
lists every file and decides what to analyse before anything is downloaded, then only those
files are fetched (`ingestion.preflight`, `ingestion.fetch`). Uploaded ZIPs and folders are
filtered with the same rules (`ingestion.rules`) and only eligible files are written.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

import httpx

from core import config
from core.config import REPOSITORIES_DIR
from ingestion import fetch, preflight, rules
from ingestion.archive import safe_extract_archive
from ingestion.preflight import PRIORITY, Entry, NeedsScope, build_plan
from ingestion.rules import (
    WINDOWS_RESERVED_NAMES,
    IngestionError,
    classify_file,
    is_ignored_path,
    safe_relative_path,
)
from storage.database import get_connection, get_snapshot, initialize_database

# Re-exported: callers and tests import these from here.
__all__ = [
    "PRIORITY", "WINDOWS_RESERVED_NAMES", "IngestionError", "NeedsScope", "atomic_extract_archive",
    "classify_file", "ingest_folder", "ingest_github_repository", "ingest_zip_bytes",
    "ingest_zip_file", "ingest_zip_path", "is_ignored_path", "parse_github_url",
    "safe_extract_archive", "safe_relative_path",
]

COPY_CHUNK_SIZE = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# GitHub input
# ---------------------------------------------------------------------------


GITHUB_SUBPAGES = {"tree", "blob", "commit", "commits", "issues", "pulls", "pull", "actions",
                   "releases", "tags", "branches", "wiki", "discussions"}


def parse_github_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())

    if parsed.scheme != "https":
        raise IngestionError("Only HTTPS GitHub URLs are accepted.")

    if parsed.hostname not in {"github.com", "www.github.com"}:
        raise IngestionError("Only public GitHub repositories are supported.")

    if parsed.port is not None:
        raise IngestionError("Custom ports are not allowed.")

    # GitHub's own repository links end in `?tab=readme-ov-file` or `#readme`; nothing else.
    if parsed.query and not re.fullmatch(r"tab=[\w-]+", parsed.query):
        raise IngestionError("Query parameters are not allowed.")
    if parsed.fragment and not re.fullmatch(r"readme[\w-]*", parsed.fragment):
        raise IngestionError("Fragments are not allowed.")

    parts = parsed.path.strip("/").split("/")

    # A link copied from inside the repository (a folder, a file, its issues) names the
    # repository in its first two segments; the whole repository is analysed.
    if len(parts) > 2 and parts[2] in GITHUB_SUBPAGES and ".." not in parts:
        parts = parts[:2]

    if len(parts) != 2:
        raise IngestionError("Expected exactly: https://github.com/owner/repository")

    owner, repository = parts
    repository = re.sub(r"\.git$", "", repository)

    valid = re.compile(r"^[A-Za-z0-9_.-]+$")

    if not valid.fullmatch(owner) or not valid.fullmatch(repository):
        raise IngestionError("Invalid GitHub owner or repository name.")

    if owner in {".", ".."} or repository in {".", ".."}:
        raise IngestionError("Invalid repository path.")

    return owner, repository


def scope_suffix(scope: str | None) -> str:
    """What a scoped snapshot adds to its id: `#path/to/folder`, or `#top` for the most
    important files of a repository that is over the limits."""
    if scope is None:
        return ""
    return "#top" if scope == PRIORITY else f"#{scope}"


def _scope_folder(scope: str | None) -> str:
    if scope is None:
        return ""
    return "-" + hashlib.sha256(scope.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Extraction (ZIP)
# ---------------------------------------------------------------------------


def atomic_extract_archive(source: bytes | Path, final_destination: Path, **options) -> list[dict]:
    """Extract into a sibling temp dir, then publish with a single rename.

    A failure at any point leaves no partial snapshot behind. The temp dir is created next to
    the destination so os.replace stays on one filesystem and is atomic.
    """
    final_destination.parent.mkdir(parents=True, exist_ok=True)
    if final_destination.exists():
        raise IngestionError("Snapshot destination already exists.")
    temporary_directory = Path(
        tempfile.mkdtemp(prefix=".evidenceloop-", dir=final_destination.parent))
    try:
        records = safe_extract_archive(source, temporary_directory, **options)
        os.replace(temporary_directory, final_destination)
        return records
    except Exception:
        shutil.rmtree(temporary_directory, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_snapshot(
    snapshot_id: str,
    repository_id: str,
    source_type: str,
    source_url: str | None,
    owner: str | None,
    name: str,
    commit_sha: str | None,
    archive_sha256: str,
    records: list[dict],
    destination: Path,
    scope: str | None = None,
) -> str:
    eligible = sum(record["status"] == "pending" for record in records)
    skipped = sum(record["status"] == "skipped" for record in records)

    initialize_database()

    # One transaction: repository, snapshot and file rows land together.
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO repositories
            (repository_id, source_type, source_url, owner, name)
            VALUES (?, ?, ?, ?, ?)
            """,
            (repository_id, source_type, source_url, owner, name),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO snapshots
            (
                snapshot_id, repository_id, commit_sha,
                archive_sha256, root_path, total_files,
                eligible_files, skipped_files, scope
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_id, repository_id, commit_sha, archive_sha256,
             str(destination.resolve()), len(records), eligible, skipped, scope),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO files
            (
                snapshot_id, relative_path, extension,
                size_bytes, content_sha256, status, skip_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (snapshot_id, record["relative_path"], record["extension"],
                 record["size_bytes"], record["content_sha256"], record["status"],
                 record["skip_reason"])
                for record in records
            ],
        )

    return snapshot_id


def _summary(records: list[dict]) -> dict:
    return {
        "total_files": len(records),
        "eligible_files": sum(r["status"] == "pending" for r in records),
        "skipped_files": sum(r["status"] == "skipped" for r in records),
    }


def _prepare_destination(snapshot_id: str, destination: Path) -> bool:
    """Return True if this snapshot is already fully ingested.

    A directory without a database row is left over from a crash between extraction and
    persistence; it is removed so ingestion can start clean.
    """
    if not destination.exists():
        return False
    initialize_database()
    if get_snapshot(snapshot_id) is not None:
        return True
    shutil.rmtree(destination)
    return False


def _persist_or_remove(destination: Path, **fields) -> None:
    try:
        persist_snapshot(destination=destination, **fields)
    except Exception:
        # Do not leave a published snapshot dir with no metadata.
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _fill_hashes(records: list[dict], hashes: dict[str, tuple[int, str]]) -> list[dict]:
    for record in records:
        if record["relative_path"] in hashes:
            record["size_bytes"], record["content_sha256"] = hashes[record["relative_path"]]
    return records


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def ingest_github_repository(
    url: str,
    transport: httpx.BaseTransport | None = None,
    scope: str | None = None,
    *,
    on_plan: Callable[[dict], None] | None = None,
    run: fetch.Runner = subprocess.run,
    git: str | None | bool = True,
) -> dict:
    """Preflight, then fetch only the files to analyse.

    `scope` is a folder path, PRIORITY ("*": over the limits, take the most important files)
    or None. Raises NeedsScope (with the plan) when the source is over the limits and no scope
    says what to do. `on_plan` receives the plan as soon as it is known. `run` and `git` let
    tests replace the git subprocess.
    """
    owner, name = parse_github_url(url)
    scope = preflight.normalise_scope(scope)
    repository_id = f"github:{owner}/{name}"

    def place(sha: str, plan_scope: str | None) -> tuple[str, Path]:
        return (f"{repository_id}@{sha}{scope_suffix(plan_scope)}",
                REPOSITORIES_DIR / f"{owner}_{name}" / f"{sha}{_scope_folder(plan_scope)}")

    def stored(snapshot_id: str, destination: Path, sha: str) -> dict:
        return {"repository_id": repository_id, "snapshot_id": snapshot_id, "commit_sha": sha,
                "root_path": str(destination), "already_downloaded": True}

    with preflight.open_client(transport) as client:
        _metadata, sha = preflight.resolve_commit(client, owner, name)
        snapshot_id, destination = place(sha, scope)
        # Asking again for an unchanged repository costs two small API calls, nothing more.
        if _prepare_destination(snapshot_id, destination):
            return stored(snapshot_id, destination, sha)
        plan = preflight.preflight(client, owner, name, sha, scope)

    if on_plan is not None:
        on_plan(plan.to_dict())
    if plan.status == "needs_scope":
        raise NeedsScope(plan)

    # "Analyse anyway" on a repository that fits is the same snapshot as no scope at all.
    effective = scope
    if scope == PRIORITY and len(plan.selected) == len(plan.eligible):
        effective = None
        snapshot_id, destination = place(sha, None)
        if _prepare_destination(snapshot_id, destination):
            return stored(snapshot_id, destination, sha)

    records, method, digest = fetch.fetch_plan(plan, destination, transport=transport,
                                               run=run, git=git)
    _persist_or_remove(destination, snapshot_id=snapshot_id, repository_id=repository_id,
                       source_type="github", source_url=url, owner=owner, name=name,
                       commit_sha=sha, archive_sha256=digest, records=records, scope=effective)
    return {"repository_id": repository_id, "snapshot_id": snapshot_id, "commit_sha": sha,
            "root_path": str(destination), **_summary(records), "already_downloaded": False,
            "fetched_with": method, "plan": plan.to_dict()}


def _upload_limit_message() -> str:
    return f"The ZIP is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024):,} MB."


def ingest_zip_file(zip_path: Path) -> dict:
    """Ingest a ZIP on disk without reading it into memory."""
    zip_path = Path(zip_path).resolve()
    if not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
        raise IngestionError("Please provide an existing ZIP file.")
    return ingest_zip_path(zip_path, zip_path.stem)


def ingest_zip_path(zip_path: Path, name: str) -> dict:
    if Path(zip_path).stat().st_size > config.MAX_UPLOAD_BYTES:
        raise IngestionError(_upload_limit_message())
    return _ingest_zip(Path(zip_path), sha256_file(zip_path), name)


def ingest_zip_bytes(archive_bytes: bytes, name: str) -> dict:
    """Ingest a ZIP held in memory (tests, small uploads)."""
    if len(archive_bytes) > config.MAX_UPLOAD_BYTES:
        raise IngestionError(_upload_limit_message())
    return _ingest_zip(archive_bytes, sha256_bytes(archive_bytes), name)


def _ingest_zip(source: bytes | Path, archive_sha: str, name: str) -> dict:
    archive_id = archive_sha[:24]
    snapshot_id = f"zip-{archive_id}"
    destination = REPOSITORIES_DIR / "uploaded_zips" / snapshot_id
    result = {"repository_id": f"zip:{archive_id}", "snapshot_id": snapshot_id,
              "commit_sha": None, "root_path": str(destination)}
    if _prepare_destination(snapshot_id, destination):
        return {**result, "already_downloaded": True}
    records = atomic_extract_archive(source, destination, name=name)
    left_out = any(r["skip_reason"] == rules.LEFT_OUT for r in records)
    _persist_or_remove(destination, snapshot_id=snapshot_id,
                       repository_id=result["repository_id"], source_type="zip",
                       source_url=None, owner=None, name=name or "upload", commit_sha=None,
                       archive_sha256=archive_sha, records=records,
                       scope=PRIORITY if left_out else None)
    return {**result, **_summary(records), "already_downloaded": False}


def _size_of(handle: BinaryIO) -> int:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(0)
    return size


def ingest_folder(files: list[tuple[str, BinaryIO]], name: str = "") -> dict:
    """Ingest a folder sent from the browser: (relative path, readable file) pairs.

    Paths go through the same checks as archive entries (traversal, reserved names, case
    clashes); files through the same rules. Only eligible files are written, into a temp
    folder published with one os.replace. Over the source limits, the most important files are
    kept (asking again would mean uploading again).
    """
    if not files:
        raise IngestionError("The folder is empty.")
    if len(files) > config.MAX_LISTED_ENTRIES:
        raise IngestionError("The folder has too many files.")
    raw_paths = [path for path, _ in files]
    first = {p.split("/")[0] for p in raw_paths}
    root = first.pop() if len(first) == 1 and all("/" in p for p in raw_paths) else None
    entries: list[Entry] = []
    handles: dict[str, BinaryIO] = {}
    seen: set[str] = set()
    for raw, handle in files:
        path = safe_relative_path(raw, strip_root=root)
        if not path:
            continue
        if path.casefold() in seen:
            raise IngestionError(f"The folder has two files named {path} (apart from case).")
        seen.add(path.casefold())
        handles[path] = handle
        entries.append(Entry(path, _size_of(handle)))
    label = name or root or "folder"
    plan = build_plan(entries, name=label, auto_priority=True)
    if not plan.selected:
        raise IngestionError("None of the folder's files can be analysed (no source code).")

    parent = REPOSITORIES_DIR / "uploaded_folders"
    parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=".evidenceloop-", dir=parent))
    resolved = workdir.resolve()
    try:
        hashes: dict[str, tuple[int, str]] = {}
        for entry in plan.selected:
            target = workdir.joinpath(*entry.path.split("/"))
            if not target.resolve().is_relative_to(resolved):
                raise IngestionError(f"Unsafe path: {entry.path!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, written = hashlib.sha256(), 0
            with target.open("xb") as out:
                while chunk := handles[entry.path].read(COPY_CHUNK_SIZE):
                    written += len(chunk)
                    if written > entry.size:
                        raise IngestionError(f"{entry.path} changed while it was read.")
                    digest.update(chunk)
                    out.write(chunk)
            hashes[entry.path] = (written, digest.hexdigest())
        folder_id = fetch.manifest_digest(hashes)[:24]
        snapshot_id = f"folder-{folder_id}"
        destination = parent / snapshot_id
        result = {"repository_id": f"folder:{folder_id}", "snapshot_id": snapshot_id,
                  "commit_sha": None, "root_path": str(destination)}
        if _prepare_destination(snapshot_id, destination):
            fetch.remove_tree(workdir)
            return {**result, "already_downloaded": True}
        os.replace(workdir, destination)
    except Exception:
        fetch.remove_tree(workdir)
        raise

    records = _fill_hashes(plan.records(), hashes)
    left_out = len(plan.selected) < len(plan.eligible)
    _persist_or_remove(destination, snapshot_id=snapshot_id,
                       repository_id=result["repository_id"], source_type="folder",
                       source_url=None, owner=None, name=label, commit_sha=None,
                       archive_sha256=fetch.manifest_digest(hashes), records=records,
                       scope=PRIORITY if left_out else None)
    return {**result, **_summary(records), "already_downloaded": False}
