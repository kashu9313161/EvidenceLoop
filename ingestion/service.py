
import hashlib
import io
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx

from core.config import (
    IGNORED_DIRECTORY_NAMES,
    IGNORED_FILE_NAMES,
    MAX_ARCHIVE_DOWNLOAD_BYTES,
    MAX_FILE_UNCOMPRESSED_BYTES,
    MAX_FILES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    REPOSITORIES_DIR,
    SUPPORTED_EXTENSIONS,
)
from storage.database import get_connection, get_snapshot, initialize_database

GITHUB_API = "https://api.github.com"


class IngestionError(Exception):
    """Raised when a repository cannot be safely ingested."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_github_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())

    if parsed.scheme != "https":
        raise IngestionError("Only HTTPS GitHub URLs are accepted.")

    if parsed.hostname not in {"github.com", "www.github.com"}:
        raise IngestionError("Only public GitHub repositories are supported.")

    if parsed.port is not None:
        raise IngestionError("Custom ports are not allowed.")
    
    if parsed.query or parsed.fragment:
        raise IngestionError("Query parameters and fragments are not allowed.")

    parts = parsed.path.strip("/").split("/")

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


def github_download(owner: str, name: str) -> tuple[dict, str, bytes]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "EvidenceLoop-Local",
    }

    try:
        with httpx.Client(
            timeout=60,
            follow_redirects=True,
            headers=headers,
        ) as client:

            # fetch repository metadata
            metadata_response = client.get(
                f"{GITHUB_API}/repos/{owner}/{name}"
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()

            if metadata.get("private") is not False:
                raise IngestionError("Only Public repositories are supported.")

            # 2. Resolve the default branch
            branch = metadata.get("default_branch")

            if not branch:
                raise IngestionError("Could not determine repository default branch.")

            # 3. Resolve exact commit SHA
            commit_response = client.get(
                f"{GITHUB_API}/repos/{owner}/{name}/commits/{branch}"
            )

            commit_response.raise_for_status()
            commit_data = commit_response.json()
            commit_sha = commit_data.get("sha")

            if not commit_sha:
                raise IngestionError("Github did not return a commit SHA.")

            # 4. Download the archive for the exact commit
            archive_response = client.get(
                f"{GITHUB_API}/repos/{owner}/{name}/zipball/{commit_sha}"
            )
            archive_response.raise_for_status()

            # check Content-length before reading when available.
            content_length = archive_response.headers.get("Content-Length")

            if content_length:
                if int(content_length) > MAX_ARCHIVE_DOWNLOAD_BYTES:
                    raise IngestionError(
                        "Repository archive exceeds the download size limit."
                    )

            archive = archive_response.content

    except IngestionError:
        raise

    except httpx.HTTPError as exc:
        status = exc.response.status_code

        if status == 404:
            raise IngestionError("Repository not found or not publicly accessible.") from exc

        if status == 403:
            raise IngestionError(
                "GitHub API rate limit access restriction encountered."
            ) from exc

        
        raise IngestionError(
            f"GitHub returned HTTP {status}.") from exc

    
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise IngestionError("" \
        f"GitHub request failed: {exc}") from exc

    if len(archive) > MAX_ARCHIVE_DOWNLOAD_BYTES:
        raise IngestionError("Repository archive exceeds the download size limit.")

    return metadata, commit_sha, archive



def safe_relative_path(name: str, strip_root: str | None = None) -> str:
    if "\\" in name or "\x00" in name:
        raise IngestionError(f"Unsafe archive path: {name!r}")

    path = PurePosixPath(name)

    if path.is_absolute() or not path.parts:
        raise IngestionError(f"Unsafe archive path: {name!r}")

    if any(part in {"", ".", ".."} for part in path.parts):
        raise IngestionError(f"Unsafe archive path: {name!r}")

    if re.match(r"^[A-Za-z]:", name):
        raise IngestionError(f"Windows absolute path is not allowed: {name!r}")

    parts = path.parts

    if strip_root and parts[0] == strip_root:
        parts = parts[1:]

    if not parts:
        return ""

    return "/".join(parts)


def is_ignored_path(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts

    if any(part in IGNORED_DIRECTORY_NAMES for part in parts[:-1]):
        return True

    if parts and parts[-1] in IGNORED_FILE_NAMES:
        return True

    return False


def classify_file(relative_path: str) -> tuple[str, str | None]:
    path = PurePosixPath(relative_path)
    extension = path.suffix.lower()

    if is_ignored_path(relative_path):
        return "skipped", "Ignored directory or file"

    if extension not in SUPPORTED_EXTENSIONS:
        return "skipped", "Unsupported file type"

    return "pending", None


def safe_extract_archive(
    archive_bytes: bytes,
    destination: Path,
) -> list[dict]:
    """
    Validate and extract eligible regular files only.

    No repository code is executed. Symlinks are skipped.
    Unsafe paths reject the archive.
    """

    destination.mkdir(parents=True, exist_ok=True)

    records = []
    seen_paths = set()
    total_uncompressed = 0

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise IngestionError("The uploaded file is not a valid ZIP archive.") from exc

    with archive:
        entries = archive.infolist()

        file_entries = [entry for entry in entries if not entry.is_dir()]
        first_parts = {
            PurePosixPath(entry.filename).parts[0]
            for entry in file_entries
            if PurePosixPath(entry.filename).parts
        }

        strip_root = None

        if file_entries and len(first_parts) == 1:
            candidate_root = next(iter(first_parts))

            all_have_root = all(
                len(PurePosixPath(entry.filename).parts) > 1
                and PurePosixPath(entry.filename).parts[0] == candidate_root
                for entry in file_entries
            )

            if all_have_root:
                strip_root = candidate_root

        if len(entries) > MAX_FILES:
            raise IngestionError("Archive contains too many entries.")

        for entry in entries:
            if entry.is_dir():
                continue

            relative_path = safe_relative_path(
                entry.filename,
                strip_root=strip_root,
            )

            if not relative_path:
                continue

            if relative_path in seen_paths:
                raise IngestionError(
                    f"Archive contains duplicate paths: {relative_path}"
                )

            seen_paths.add(relative_path)

            # Unix file type is stored in the upper bits of external_attr.
            unix_mode = (entry.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)

            if file_type == stat.S_IFLNK:
                records.append({
                    "relative_path": relative_path,
                    "extension": PurePosixPath(relative_path).suffix.lower(),
                    "size_bytes": entry.file_size,
                    "content_sha256": None,
                    "status": "skipped",
                    "skip_reason": "Symbolic links are not extracted",
                })
                continue

            if file_type not in {0, stat.S_IFREG}:
                records.append({
                    "relative_path": relative_path,
                    "extension": PurePosixPath(relative_path).suffix.lower(),
                    "size_bytes": entry.file_size,
                    "content_sha256": None,
                    "status": "skipped",
                    "skip_reason": "Non-regular archive entry",
                })
                continue

            if entry.file_size > MAX_FILE_UNCOMPRESSED_BYTES:
                raise IngestionError(
                    f"File exceeds size limit: {relative_path}"
                )

            total_uncompressed += entry.file_size

            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise IngestionError(
                    "Archive exceeds total uncompressed size limit."
                )

            status, reason = classify_file(relative_path)

            content = archive.read(entry)

            if len(content) != entry.file_size:
                raise IngestionError(
                    f"Archive size mismatch for {relative_path}"
                )

            digest = sha256_bytes(content)

            if status == "pending":
                target = destination.joinpath(*PurePosixPath(relative_path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            records.append({
                "relative_path": relative_path,
                "extension": PurePosixPath(relative_path).suffix.lower(),
                "size_bytes": len(content),
                "content_sha256": digest,
                "status": status,
                "skip_reason": reason,
            })

    return records


def persist_snapshot(
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
    archive_sha = sha256_bytes(archive_bytes)

    snapshot_id = (
        f"{repository_id}@{commit_sha}"
        if commit_sha
        else f"zip-{archive_sha[:24]}"
    )

    eligible = sum(record["status"] == "pending" for record in records)
    skipped = sum(record["status"] == "skipped" for record in records)

    initialize_database()

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
                archive_sha,
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


def ingest_github_repository(url: str) -> dict:
    owner, name = parse_github_url(url)

    repository_id = f"github:{owner}/{name}"

    _metadata, commit_sha, archive_bytes = github_download(owner, name)

    snapshot_id = f"{repository_id}@{commit_sha}"

    destination = (
        REPOSITORIES_DIR
        / f"{owner}_{name}"
        / commit_sha
    )

    if destination.exists():
        existing_snapshot = get_snapshot(snapshot_id)

        if existing_snapshot is not None:
            return {
                "repository_id": repository_id,
                "snapshot_id": snapshot_id,
                "commit_sha": commit_sha,
                "root_path": str(destination),
                "already_downloaded": True,
            }

    records = safe_extract_archive(archive_bytes, destination)

    snapshot_id = persist_snapshot(
        repository_id=repository_id,
        source_type="github",
        source_url=url,
        owner=owner,
        name=name,
        commit_sha=commit_sha,
        archive_bytes=archive_bytes,
        records=records,
        destination=destination,
    )

    return {
        "repository_id": repository_id,
        "snapshot_id": snapshot_id,
        "commit_sha": commit_sha,
        "root_path": str(destination),
        "total_files": len(records),
        "eligible_files": sum(
            record["status"] == "pending" for record in records
        ),
        "skipped_files": sum(
            record["status"] == "skipped" for record in records
        ),
        "already_downloaded": False,
    }


def ingest_zip_file(zip_path: Path) -> dict:
    zip_path = zip_path.resolve()

    if not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
        raise IngestionError("Please provide an existing ZIP file.")

    if zip_path.stat().st_size > MAX_ARCHIVE_DOWNLOAD_BYTES:
        raise IngestionError("ZIP exceeds the download size limit.")

    archive_bytes = zip_path.read_bytes()
    archive_sha = sha256_bytes(archive_bytes)

    repository_id = f"zip:{archive_sha[:24]}"
    snapshot_id = f"zip-{archive_sha[:24]}"

    destination = REPOSITORIES_DIR / "uploaded_zips" / snapshot_id

    if destination.exists():
        return {
            "repository_id": repository_id,
            "snapshot_id": snapshot_id,
            "root_path": str(destination),
            "already_downloaded": True,
        }

    records = safe_extract_archive(archive_bytes, destination)

    snapshot_id = persist_snapshot(
        repository_id=repository_id,
        source_type="zip",
        source_url=None,
        owner=None,
        name=zip_path.stem,
        commit_sha=None,
        archive_bytes=archive_bytes,
        records=records,
        destination=destination,
    )

    return {
        "repository_id": repository_id,
        "snapshot_id": snapshot_id,
        "root_path": str(destination),
        "total_files": len(records),
        "eligible_files": sum(r["status"] == "pending" for r in records),
        "skipped_files": sum(r["status"] == "skipped" for r in records),
        "already_downloaded": False,
    }
