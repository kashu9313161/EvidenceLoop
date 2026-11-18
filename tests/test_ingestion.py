import io
import json
import stat
import zipfile

import httpx
import pytest

from ingestion import service
from ingestion.service import (
    IngestionError,
    atomic_extract_archive,
    github_download,
    ingest_github_repository,
    ingest_zip_file,
    parse_github_url,
    safe_extract_archive,
    safe_relative_path,
)
from storage.database import get_coverage, get_snapshot

COMMIT_SHA = "a" * 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_zip(files: dict[str, bytes], compression=zipfile.ZIP_STORED) -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, content in files.items():
            archive.writestr(name, content)

    return buffer.getvalue()


def make_zip_entries(entries: list[zipfile.ZipInfo | tuple[str, bytes]]) -> bytes:
    """Build a ZIP from raw ZipInfo objects (for symlinks, flags, duplicates)."""
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        for entry in entries:
            if isinstance(entry, tuple):
                archive.writestr(*entry)
            else:
                archive.writestr(entry, b"data")

    return buffer.getvalue()


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


def test_parse_github_url():
    assert parse_github_url("https://github.com/psf/requests") == ("psf", "requests")


def test_parse_github_url_with_git_suffix():
    assert parse_github_url("https://github.com/psf/requests.git") == ("psf", "requests")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/psf/requests",
        "https://evil.com/psf/requests",
        "https://github.com.evil.com/psf/requests",
        "https://github.com",
        "https://github.com/owner/repo/../../other",
        "https://github.com/psf/requests?query=value",
        "https://github.com/psf/requests#fragment",
        "https://github.com:8080/psf/requests",
        "https://github.com/../requests",
    ],
)
def test_reject_invalid_github_urls(url):
    with pytest.raises(IngestionError):
        parse_github_url(url)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "/etc/passwd",
        "../outside.py",
        "root/../../outside.py",
        "root/./app.py",
        "root//app.py",
        "C:/Windows/evil.py",
        "root\\..\\evil.py",
        "root/file\x00.py",
        "root/file.py:stream",
        "root/CON.py",
        "root/nul",
        "root/trailing. ",
    ],
)
def test_reject_unsafe_archive_paths(name):
    with pytest.raises(IngestionError):
        safe_relative_path(name)


def test_strip_archive_root():
    assert safe_relative_path("repo-abc/src/app.py", strip_root="repo-abc") == "src/app.py"


# ---------------------------------------------------------------------------
# Extraction: happy path and classification
# ---------------------------------------------------------------------------


def test_extract_python_file(tmp_path):
    archive = make_zip({"project-root/app.py": b"print('hello')"})

    records = safe_extract_archive(archive, tmp_path)

    assert len(records) == 1
    assert records[0]["relative_path"] == "app.py"
    assert records[0]["status"] == "pending"
    assert records[0]["content_sha256"] == service.sha256_bytes(b"print('hello')")
    assert (tmp_path / "app.py").read_bytes() == b"print('hello')"


def test_extract_unix_regular_file_mode(tmp_path):
    info = zipfile.ZipInfo("root/app.py")
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16

    records = safe_extract_archive(make_zip_entries([info]), tmp_path)

    assert records[0]["status"] == "pending"
    assert (tmp_path / "app.py").exists()


def test_keeps_multiple_top_level_entries(tmp_path):
    archive = make_zip({"a/app.py": b"1", "b/lib.py": b"2"})

    records = safe_extract_archive(archive, tmp_path)

    assert {r["relative_path"] for r in records} == {"a/app.py", "b/lib.py"}


def test_skip_unsupported_file(tmp_path):
    archive = make_zip({"project-root/photo.png": b"fake-image"})

    records = safe_extract_archive(archive, tmp_path)

    assert records[0]["status"] == "skipped"
    assert records[0]["skip_reason"] == "Unsupported file type"
    assert not (tmp_path / "photo.png").exists()


def test_skip_ignored_directory(tmp_path):
    archive = make_zip({"root/node_modules/lib.js": b"x", "root/app.js": b"y"})

    records = {r["relative_path"]: r for r in safe_extract_archive(archive, tmp_path)}

    assert records["node_modules/lib.js"]["status"] == "skipped"
    assert records["app.js"]["status"] == "pending"
    assert not (tmp_path / "node_modules").exists()


def test_skipped_files_do_not_count_toward_size_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "MAX_FILE_UNCOMPRESSED_BYTES", 10)
    archive = make_zip({"root/big.png": b"x" * 100, "root/app.py": b"ok"})

    records = safe_extract_archive(archive, tmp_path)

    assert len(records) == 2


# ---------------------------------------------------------------------------
# Extraction: security checks (one test per limit)
# ---------------------------------------------------------------------------


def test_reject_path_traversal(tmp_path):
    archive = make_zip({"project-root/../../outside.py": b"malicious"})

    with pytest.raises(IngestionError):
        safe_extract_archive(archive, tmp_path)

    assert not (tmp_path.parent / "outside.py").exists()


def test_symlink_is_recorded_but_not_created(tmp_path):
    link = zipfile.ZipInfo("root/link.py")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16

    records = safe_extract_archive(
        make_zip_entries([link, ("root/app.py", b"ok")]), tmp_path
    )

    by_path = {r["relative_path"]: r for r in records}
    assert by_path["link.py"]["status"] == "skipped"
    assert by_path["link.py"]["skip_reason"] == "Symbolic links are not extracted"
    assert not (tmp_path / "link.py").exists()
    assert (tmp_path / "app.py").exists()


def test_special_file_is_skipped(tmp_path):
    fifo = zipfile.ZipInfo("root/pipe.py")
    fifo.create_system = 3
    fifo.external_attr = (stat.S_IFIFO | 0o644) << 16

    records = safe_extract_archive(make_zip_entries([fifo]), tmp_path)

    assert records[0]["skip_reason"] == "Non-regular archive entry"
    assert not (tmp_path / "pipe.py").exists()


def set_encrypted_flag(archive: bytes) -> bytes:
    """zipfile clears flag_bits on write, so set bit 0 in the raw headers."""
    data = bytearray(archive)

    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = data.find(signature)
        data[start + flag_offset] |= 0x1

    return bytes(data)


def test_reject_encrypted_entry(tmp_path):
    archive = set_encrypted_flag(make_zip({"root/secret.py": b"data"}))

    with pytest.raises(IngestionError, match="Encrypted"):
        safe_extract_archive(archive, tmp_path)


def test_reject_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "MAX_FILE_UNCOMPRESSED_BYTES", 10)

    with pytest.raises(IngestionError, match="File exceeds size limit"):
        safe_extract_archive(make_zip({"root/app.py": b"x" * 11}), tmp_path)


def test_reject_total_size_overflow(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "MAX_TOTAL_UNCOMPRESSED_BYTES", 15)
    archive = make_zip({"root/a.py": b"x" * 10, "root/b.py": b"y" * 10})

    with pytest.raises(IngestionError, match="total uncompressed size"):
        safe_extract_archive(archive, tmp_path)


def test_reject_too_many_files(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "MAX_FILES", 3)
    archive = make_zip({f"root/f{i}.py": b"x" for i in range(4)})

    with pytest.raises(IngestionError, match="too many files"):
        safe_extract_archive(archive, tmp_path)


def test_reject_high_compression_ratio(tmp_path):
    # 1 MiB of zeros deflates to ~1 KiB: ratio ~1000 > 100.
    archive = make_zip({"root/bomb.py": b"\0" * (1024 * 1024)}, zipfile.ZIP_DEFLATED)

    with pytest.raises(IngestionError, match="Compression ratio"):
        safe_extract_archive(archive, tmp_path)


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_reject_duplicate_paths(tmp_path):
    archive = make_zip_entries([("root/app.py", b"one"), ("root/app.py", b"two")])

    with pytest.raises(IngestionError, match="duplicate"):
        safe_extract_archive(archive, tmp_path)


def test_reject_case_insensitive_duplicate_paths(tmp_path):
    archive = make_zip({"root/App.py": b"one", "root/app.py": b"two"})

    with pytest.raises(IngestionError, match="duplicate"):
        safe_extract_archive(archive, tmp_path)


def test_validation_happens_before_any_write(tmp_path):
    archive = make_zip({"root/good.py": b"ok", "root/../evil.py": b"bad"})

    with pytest.raises(IngestionError):
        safe_extract_archive(archive, tmp_path / "out")

    assert not (tmp_path / "out").exists()


def test_reject_non_zip(tmp_path):
    with pytest.raises(IngestionError, match="not a valid ZIP"):
        safe_extract_archive(b"definitely not a zip", tmp_path)


def corrupt_crc_archive() -> bytes:
    payload = b"print('original content')"
    archive = bytearray(make_zip({"root/a.py": b"fine", "root/b.py": payload}))
    offset = archive.find(payload)
    archive[offset] ^= 0xFF
    return bytes(archive)


def test_reject_corrupted_entry(tmp_path):
    with pytest.raises(IngestionError, match="Corrupted"):
        safe_extract_archive(corrupt_crc_archive(), tmp_path)


# ---------------------------------------------------------------------------
# Atomic publication
# ---------------------------------------------------------------------------


def test_atomic_extract_publishes_complete_snapshot(tmp_path):
    destination = tmp_path / "snapshots" / "snap"

    atomic_extract_archive(make_zip({"root/app.py": b"ok"}), destination)

    assert (destination / "app.py").read_bytes() == b"ok"
    assert [p.name for p in destination.parent.iterdir()] == ["snap"]


def test_atomic_extract_rolls_back_on_failure(tmp_path):
    destination = tmp_path / "snapshots" / "snap"

    # First file extracts fine, second fails its CRC mid-extraction.
    with pytest.raises(IngestionError):
        atomic_extract_archive(corrupt_crc_archive(), destination)

    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_atomic_extract_refuses_existing_destination(tmp_path):
    destination = tmp_path / "snap"
    destination.mkdir()

    with pytest.raises(IngestionError, match="already exists"):
        atomic_extract_archive(make_zip({"root/app.py": b"ok"}), destination)


# ---------------------------------------------------------------------------
# GitHub download (mocked transport, no network)
# ---------------------------------------------------------------------------


def github_transport(
    archive: bytes,
    *,
    private: bool = False,
    sha: str = COMMIT_SHA,
    status: int = 200,
    content_length: str | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if status != 200:
            return httpx.Response(status)

        if path == "/repos/owner/repo":
            return httpx.Response(
                200, json={"private": private, "default_branch": "main"}
            )

        if path == "/repos/owner/repo/commits/main":
            return httpx.Response(200, json={"sha": sha})

        if path == f"/repos/owner/repo/zipball/{sha}":
            headers = {"Content-Length": content_length} if content_length else {}
            return httpx.Response(200, content=archive, headers=headers)

        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_github_download_resolves_commit():
    archive = make_zip({"owner-repo-aaa/app.py": b"ok"})

    metadata, sha, data = github_download(
        "owner", "repo", transport=github_transport(archive)
    )

    assert sha == COMMIT_SHA
    assert data == archive
    assert metadata["default_branch"] == "main"


def test_github_download_rejects_oversized_stream(monkeypatch):
    monkeypatch.setattr(service, "MAX_ARCHIVE_DOWNLOAD_BYTES", 10)
    monkeypatch.setattr(service, "DOWNLOAD_CHUNK_SIZE", 4)

    with pytest.raises(IngestionError, match="download size limit"):
        github_download("owner", "repo", transport=github_transport(b"x" * 50))


def test_github_download_rejects_large_content_length(monkeypatch):
    monkeypatch.setattr(service, "MAX_ARCHIVE_DOWNLOAD_BYTES", 10)

    with pytest.raises(IngestionError, match="download size limit"):
        github_download(
            "owner", "repo",
            transport=github_transport(b"x", content_length="999999"),
        )


def test_github_download_rejects_private_repo():
    with pytest.raises(IngestionError, match="public"):
        github_download("owner", "repo", transport=github_transport(b"", private=True))


def test_github_download_rejects_invalid_sha():
    with pytest.raises(IngestionError, match="commit SHA"):
        github_download("owner", "repo", transport=github_transport(b"", sha="../x"))


@pytest.mark.parametrize(
    ("status", "message"),
    [(404, "not found"), (403, "rate limit"), (500, "HTTP 500")],
)
def test_github_download_maps_http_errors(status, message):
    with pytest.raises(IngestionError, match=message):
        github_download("owner", "repo", transport=github_transport(b"", status=status))


# ---------------------------------------------------------------------------
# End-to-end ingestion into an isolated database
# ---------------------------------------------------------------------------


def test_ingest_github_repository_end_to_end(isolated_storage):
    archive = make_zip({
        "owner-repo-aaa/src/app.py": b"print('hi')",
        "owner-repo-aaa/logo.png": b"png",
    })

    result = ingest_github_repository(
        "https://github.com/owner/repo", transport=github_transport(archive)
    )

    snapshot_id = f"github:owner/repo@{COMMIT_SHA}"
    assert result["snapshot_id"] == snapshot_id
    assert result["eligible_files"] == 1
    assert result["skipped_files"] == 1

    snapshot = get_snapshot(snapshot_id)
    assert snapshot["commit_sha"] == COMMIT_SHA
    assert snapshot["archive_sha256"] == service.sha256_bytes(archive)

    coverage = {row["relative_path"]: row["status"] for row in get_coverage(snapshot_id)}
    assert coverage == {"src/app.py": "pending", "logo.png": "skipped"}

    again = ingest_github_repository(
        "https://github.com/owner/repo", transport=github_transport(archive)
    )
    assert again["already_downloaded"] is True


def test_ingest_zip_file_end_to_end(isolated_storage):
    zip_path = isolated_storage / "upload.zip"
    zip_path.write_bytes(make_zip({"proj/app.py": b"ok"}))

    result = ingest_zip_file(zip_path)

    assert result["snapshot_id"].startswith("zip-")
    assert get_snapshot(result["snapshot_id"])["total_files"] == 1
    assert ingest_zip_file(zip_path)["already_downloaded"] is True


def test_ingest_recovers_from_orphaned_snapshot_dir(isolated_storage):
    """A crash between extraction and persistence must not block re-ingestion."""
    zip_path = isolated_storage / "upload.zip"
    zip_path.write_bytes(make_zip({"proj/app.py": b"ok"}))

    snapshot_id = f"zip-{service.sha256_bytes(zip_path.read_bytes())[:24]}"
    orphan = isolated_storage / "repositories" / "uploaded_zips" / snapshot_id
    orphan.mkdir(parents=True)
    (orphan / "partial.py").write_bytes(b"stale")

    result = ingest_zip_file(zip_path)

    assert result["already_downloaded"] is False
    assert not (orphan / "partial.py").exists()
    assert (orphan / "app.py").exists()


def test_failed_ingest_leaves_no_snapshot(isolated_storage):
    zip_path = isolated_storage / "evil.zip"
    zip_path.write_bytes(make_zip({"proj/../../evil.py": b"bad"}))

    with pytest.raises(IngestionError):
        ingest_zip_file(zip_path)

    uploads = isolated_storage / "repositories" / "uploaded_zips"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_ingest_rejects_oversized_zip_upload(isolated_storage, monkeypatch):
    monkeypatch.setattr(service, "MAX_ARCHIVE_DOWNLOAD_BYTES", 10)
    zip_path = isolated_storage / "big.zip"
    zip_path.write_bytes(make_zip({"proj/app.py": b"x" * 100}))

    with pytest.raises(IngestionError, match="size limit"):
        ingest_zip_file(zip_path)


def test_coverage_cli_output_is_json_serialisable(isolated_storage):
    zip_path = isolated_storage / "upload.zip"
    zip_path.write_bytes(make_zip({"proj/app.py": b"ok"}))

    result = ingest_zip_file(zip_path)

    json.dumps(result)
    json.dumps(dict(get_snapshot(result["snapshot_id"])))
