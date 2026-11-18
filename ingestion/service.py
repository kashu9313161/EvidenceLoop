"""Repository ingestion: GitHub/ZIP input -> validated, immutable snapshot.

The repository is treated as untrusted input. Nothing in it is executed,
and every archive entry is validated before anything is written to disk.
"""

import hashlib
import io
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx

from core.config import (
    DOWNLOAD_CHUNK_SIZE,
    HTTP_TIMEOUT_SECONDS,
    IGNORED_DIRECTORY_NAMES,
    IGNORED_FILE_NAMES,
    MAX_ARCHIVE_DOWNLOAD_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_FILE_UNCOMPRESSED_BYTES,
    MAX_FILES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    REPOSITORIES_DIR,
    SUPPORTED_EXTENSIONS,
)
from storage.database import get_connection, get_snapshot, initialize_database

GITHUB_API = "https://api.github.com"

# Names that refer to devices on Windows, regardless of extension.
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

EXTRACT_CHUNK_SIZE = 1024 * 1024


class IngestionError(Exception):
    """Raised when a repository cannot be safely ingested."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _github_refusal(response: httpx.Response, has_token: bool) -> str:
    """Explain a 403/429 from GitHub: an exhausted hourly limit says when it resets."""
    if response.headers.get("x-ratelimit-remaining") == "0":
        reset = response.headers.get("x-ratelimit-reset", "")
        when = ""
        if reset.isdigit():
            minutes = max(1, round((int(reset) - time.time()) / 60))
            when = f" Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
        advice = "" if has_token else (
            " Setting a GITHUB_TOKEN environment variable raises the limit from 60 to "
            "5,000 requests an hour.")
        return f"GitHub's hourly request limit is used up.{when}{advice}"
    if response.status_code == 429 or response.headers.get("retry-after"):
        return "GitHub is limiting requests right now. Wait a minute and try again."
    return "GitHub refused access to this repository."


def github_download(
    owner: str,
    name: str,
    transport: httpx.BaseTransport | None = None,
    have_commit: Callable[[str], bool] | None = None,
) -> tuple[dict, str, bytes]:
    """Resolve the default branch to a commit SHA and download that zipball.

    The download is streamed and aborted as soon as it exceeds
    MAX_ARCHIVE_DOWNLOAD_BYTES, whatever Content-Length claims.
    `transport` exists so tests can run without network access. When `have_commit(sha)`
    says that commit is already stored, the archive isn't downloaded (empty bytes).
    """

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "EvidenceLoop-Local",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:  # optional: 5,000 API requests an hour instead of 60
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
            transport=transport,
        ) as client:
            metadata_response = client.get(f"{GITHUB_API}/repos/{owner}/{name}")
            metadata_response.raise_for_status()
            metadata = metadata_response.json()

            if metadata.get("private") is not False:
                raise IngestionError("Only public repositories are supported.")

            branch = metadata.get("default_branch")

            if not branch:
                raise IngestionError("Default branch not found.")

            commit_response = client.get(
                f"{GITHUB_API}/repos/{owner}/{name}/commits/{branch}"
            )
            commit_response.raise_for_status()
            commit_sha = commit_response.json().get("sha")

            if not commit_sha or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
                raise IngestionError("GitHub did not return a valid commit SHA.")

            if have_commit is not None and have_commit(commit_sha):
                return metadata, commit_sha, b""

            buffer = io.BytesIO()

            with client.stream(
                "GET",
                f"{GITHUB_API}/repos/{owner}/{name}/zipball/{commit_sha}",
            ) as response:
                response.raise_for_status()

                content_length = response.headers.get("Content-Length")

                if content_length and int(content_length) > MAX_ARCHIVE_DOWNLOAD_BYTES:
                    raise IngestionError("Archive exceeds download size limit.")

                for chunk in response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    buffer.write(chunk)

                    if buffer.tell() > MAX_ARCHIVE_DOWNLOAD_BYTES:
                        raise IngestionError("Archive exceeded download size limit.")

    except IngestionError:
        raise

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code

        if status == 404:
            raise IngestionError(
                "Repository not found or not publicly accessible."
            ) from exc

        if status in (403, 429):
            raise IngestionError(_github_refusal(exc.response, bool(token))) from exc

        raise IngestionError(
            f"GitHub returned an error (HTTP {status}). Try again in a moment."
        ) from exc

    except httpx.TimeoutException as exc:
        raise IngestionError("GitHub took too long to respond. Try again in a moment.") from exc

    except httpx.TransportError as exc:
        raise IngestionError(
            "Couldn't reach GitHub. Check your internet connection and try again."
        ) from exc

    except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        raise IngestionError(f"GitHub download failed: {exc}") from exc

    return metadata, commit_sha, buffer.getvalue()


# ---------------------------------------------------------------------------
# Path validation and classification
# ---------------------------------------------------------------------------


def safe_relative_path(name: str, strip_root: str | None = None) -> str:
    """Validate an archive entry name and return it as a relative POSIX path.

    Rejects (raises) anything that could escape the destination or that
    Windows would interpret specially. Returns "" if nothing is left after
    stripping the archive root.
    """

    if "\\" in name or "\x00" in name:
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
    parts = PurePosixPath(relative_path).parts

    if any(part in IGNORED_DIRECTORY_NAMES for part in parts[:-1]):
        return True

    return bool(parts and parts[-1] in IGNORED_FILE_NAMES)


def classify_file(relative_path: str) -> tuple[str, str | None]:
    extension = PurePosixPath(relative_path).suffix.lower()

    if is_ignored_path(relative_path):
        return "skipped", "Ignored directory or file"

    if extension not in SUPPORTED_EXTENSIONS:
        return "skipped", "Unsupported file type"

    return "pending", None


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

    if len(first_parts) == 1:
        return first_parts.pop()

    return None


def _record(
    relative_path: str,
    size_bytes: int,
    status: str,
    skip_reason: str | None,
    content_sha256: str | None = None,
) -> dict:
    return {
        "relative_path": relative_path,
        "extension": PurePosixPath(relative_path).suffix.lower(),
        "size_bytes": size_bytes,
        "content_sha256": content_sha256,
        "status": status,
        "skip_reason": skip_reason,
    }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def safe_extract_archive(archive_bytes: bytes, destination: Path) -> list[dict]:
    """Validate every entry, then extract eligible regular files only.

    Phase 1 (no disk writes) rejects the whole archive on: invalid ZIP,
    too many entries, encrypted entries, unsafe paths, duplicate paths
    (case-insensitive), oversized files, suspicious compression ratios
    and total-size overflow. Symlinks and special files are recorded as
    skipped and never created.

    Phase 2 streams each eligible file to disk while counting the real
    decompressed bytes, so a ZIP whose headers lie about sizes still
    cannot exceed the limits.
    """

    destination = Path(destination)

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError) as exc:
        raise IngestionError("The uploaded file is not a valid ZIP archive.") from exc

    records: list[dict] = []
    to_extract: list[tuple[zipfile.ZipInfo, str, int]] = []

    with archive:
        entries = archive.infolist()
        file_entries = [entry for entry in entries if not entry.is_dir()]

        if len(file_entries) > MAX_FILES:
            raise IngestionError(
                f"Archive contains too many files ({len(file_entries)} > {MAX_FILES})."
            )

        strip_root = _detect_archive_root(entries)
        seen_paths: set[str] = set()
        declared_total = 0

        # ---- Phase 1: validate everything before writing anything. ----
        for entry in entries:
            relative_path = safe_relative_path(entry.filename, strip_root=strip_root)

            if entry.is_dir() or not relative_path:
                continue

            if entry.flag_bits & 0x1:
                raise IngestionError(
                    f"Encrypted ZIP entries are not supported: {relative_path}"
                )

            # Case-insensitive: on Windows/macOS "App.py" and "app.py" collide.
            key = relative_path.casefold()

            if key in seen_paths:
                raise IngestionError(f"Archive contains duplicate paths: {relative_path}")

            seen_paths.add(key)

            # Unix file type lives in the upper 16 bits of external_attr.
            # A type of 0 means the creator stored no type -> regular file.
            file_type = stat.S_IFMT(entry.external_attr >> 16)

            if file_type == stat.S_IFLNK:
                records.append(_record(
                    relative_path, entry.file_size, "skipped",
                    "Symbolic links are not extracted",
                ))
                continue

            if file_type not in {0, stat.S_IFREG}:
                records.append(_record(
                    relative_path, entry.file_size, "skipped",
                    "Non-regular archive entry",
                ))
                continue

            status, reason = classify_file(relative_path)

            if status != "pending":
                # Never decompressed, so size limits do not apply.
                records.append(_record(relative_path, entry.file_size, status, reason))
                continue

            if entry.file_size > MAX_FILE_UNCOMPRESSED_BYTES:
                raise IngestionError(f"File exceeds size limit: {relative_path}")

            if entry.file_size > 0 and entry.compress_size == 0:
                raise IngestionError(f"Invalid compression metadata: {relative_path}")

            if (
                entry.compress_size > 0
                and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO
            ):
                raise IngestionError(f"Compression ratio exceeds limit: {relative_path}")

            declared_total += entry.file_size

            if declared_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise IngestionError("Archive exceeds total uncompressed size limit.")

            to_extract.append((entry, relative_path, len(records)))
            records.append(_record(relative_path, entry.file_size, "pending", None))

        # ---- Phase 2: stream eligible files to disk with real byte counts. ----
        destination.mkdir(parents=True, exist_ok=True)
        resolved_destination = destination.resolve()
        actual_total = 0

        try:
            for entry, relative_path, index in to_extract:
                output_path = destination.joinpath(*relative_path.split("/"))

                if not output_path.resolve().is_relative_to(resolved_destination):
                    raise IngestionError(f"Unsafe archive path: {entry.filename!r}")

                output_path.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0

                with archive.open(entry) as source, output_path.open("xb") as target:
                    while chunk := source.read(EXTRACT_CHUNK_SIZE):
                        written += len(chunk)
                        actual_total += len(chunk)

                        if written > min(entry.file_size, MAX_FILE_UNCOMPRESSED_BYTES):
                            raise IngestionError(
                                f"Decompressed size exceeds declared size: {relative_path}"
                            )

                        if actual_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                            raise IngestionError(
                                "Archive exceeds total uncompressed size limit."
                            )

                        digest.update(chunk)
                        target.write(chunk)

                if written != entry.file_size:
                    raise IngestionError(f"Archive size mismatch for {relative_path}")

                records[index]["content_sha256"] = digest.hexdigest()
                records[index]["size_bytes"] = written

        except IngestionError:
            raise

        except (zipfile.BadZipFile, EOFError) as exc:
            raise IngestionError(f"Corrupted ZIP archive: {exc}") from exc

        except (OSError, RuntimeError, NotImplementedError) as exc:
            raise IngestionError(f"Failed to extract ZIP archive: {exc}") from exc

    return records


def atomic_extract_archive(archive_bytes: bytes, final_destination: Path) -> list[dict]:
    """Extract into a sibling temp dir, then publish with a single rename.

    A failure at any point leaves no partial snapshot behind. The temp dir
    is created next to the destination so os.replace stays on one
    filesystem and is atomic.
    """

    final_destination.parent.mkdir(parents=True, exist_ok=True)

    if final_destination.exists():
        raise IngestionError("Snapshot destination already exists.")

    temporary_directory = Path(
        tempfile.mkdtemp(prefix=".evidenceloop-", dir=final_destination.parent)
    )

    try:
        records = safe_extract_archive(archive_bytes, temporary_directory)
        os.replace(temporary_directory, final_destination)
        return records

    except Exception:
        shutil.rmtree(temporary_directory, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Persistence and entry points
# ---------------------------------------------------------------------------


def persist_snapshot(
    snapshot_id: str,
    repository_id: str,
    source_type: str,
    source_url: str | None,
    owner: str | None,
    name: str,
    commit_sha: str | None,
    archive_bytes: bytes,
    records: list[dict],
    destination: Path,
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
                eligible_files, skipped_files
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                repository_id,
                commit_sha,
                sha256_bytes(archive_bytes),
                str(destination.resolve()),
                len(records),
                eligible,
                skipped,
            ),
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
                (
                    snapshot_id,
                    record["relative_path"],
                    record["extension"],
                    record["size_bytes"],
                    record["content_sha256"],
                    record["status"],
                    record["skip_reason"],
                )
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

    A directory without a database row is left over from a crash between
    extraction and persistence; it is removed so ingestion can start clean.
    """

    if not destination.exists():
        return False

    initialize_database()

    if get_snapshot(snapshot_id) is not None:
        return True

    shutil.rmtree(destination)
    return False


def _ingest_archive(
    *,
    archive_bytes: bytes,
    snapshot_id: str,
    repository_id: str,
    source_type: str,
    source_url: str | None,
    owner: str | None,
    name: str,
    commit_sha: str | None,
    destination: Path,
) -> dict:
    result = {
        "repository_id": repository_id,
        "snapshot_id": snapshot_id,
        "commit_sha": commit_sha,
        "root_path": str(destination),
    }

    if _prepare_destination(snapshot_id, destination):
        return {**result, "already_downloaded": True}

    records = atomic_extract_archive(archive_bytes, destination)

    try:
        persist_snapshot(
            snapshot_id=snapshot_id,
            repository_id=repository_id,
            source_type=source_type,
            source_url=source_url,
            owner=owner,
            name=name,
            commit_sha=commit_sha,
            archive_bytes=archive_bytes,
            records=records,
            destination=destination,
        )
    except Exception:
        # Do not leave a published snapshot dir with no metadata.
        shutil.rmtree(destination, ignore_errors=True)
        raise

    return {**result, **_summary(records), "already_downloaded": False}


def ingest_github_repository(
    url: str,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    owner, name = parse_github_url(url)
    repository_id = f"github:{owner}/{name}"

    def destination_for(sha: str) -> Path:
        return REPOSITORIES_DIR / f"{owner}_{name}" / sha

    # Asking again for an unchanged repository costs two small API calls, not a re-download.
    _metadata, commit_sha, archive_bytes = github_download(
        owner, name, transport,
        have_commit=lambda sha: _prepare_destination(f"{repository_id}@{sha}",
                                                     destination_for(sha)),
    )

    return _ingest_archive(
        archive_bytes=archive_bytes,
        snapshot_id=f"{repository_id}@{commit_sha}",
        repository_id=repository_id,
        source_type="github",
        source_url=url,
        owner=owner,
        name=name,
        commit_sha=commit_sha,
        destination=destination_for(commit_sha),
    )


def ingest_zip_file(zip_path: Path) -> dict:
    zip_path = Path(zip_path).resolve()

    if not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
        raise IngestionError("Please provide an existing ZIP file.")

    if zip_path.stat().st_size > MAX_ARCHIVE_DOWNLOAD_BYTES:
        raise IngestionError("ZIP exceeds the download size limit.")

    return ingest_zip_bytes(zip_path.read_bytes(), zip_path.stem)


def ingest_zip_bytes(archive_bytes: bytes, name: str) -> dict:
    """Ingest an uploaded ZIP held in memory (e.g. from the Streamlit uploader)."""
    if len(archive_bytes) > MAX_ARCHIVE_DOWNLOAD_BYTES:
        raise IngestionError("ZIP exceeds the download size limit.")

    archive_id = sha256_bytes(archive_bytes)[:24]
    snapshot_id = f"zip-{archive_id}"

    return _ingest_archive(
        archive_bytes=archive_bytes,
        snapshot_id=snapshot_id,
        repository_id=f"zip:{archive_id}",
        source_type="zip",
        source_url=None,
        owner=None,
        name=name or "upload",
        commit_sha=None,
        destination=REPOSITORIES_DIR / "uploaded_zips" / snapshot_id,
    )
