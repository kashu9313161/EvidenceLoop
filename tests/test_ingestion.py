import io
import json
import stat
import time
import zipfile

import httpx
import pytest

from core import config
from ingestion import fetch, preflight, rules, service
from ingestion.service import (
    IngestionError,
    atomic_extract_archive,
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
        "https://github.com/psf/requests/tree/main/src/requests",
        "https://github.com/psf/requests/blob/main/README.md",
        "https://github.com/psf/requests/issues",
        "https://github.com/psf/requests?tab=readme-ov-file",
        "https://github.com/psf/requests#readme",
    ],
)
def test_links_copied_from_github_name_the_repository(url):
    assert parse_github_url(url) == ("psf", "requests")


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
        "https://github.com/psf/requests/tree/../../other",
        "https://github.com/psf/requests/settings",
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
    assert records[0]["skip_reason"] == "Media or document"
    assert not (tmp_path / "photo.png").exists()


def test_skip_ignored_directory(tmp_path):
    archive = make_zip({"root/node_modules/lib.js": b"x", "root/app.js": b"y"})

    records = {r["relative_path"]: r for r in safe_extract_archive(archive, tmp_path)}

    assert records["node_modules/lib.js"]["status"] == "skipped"
    assert records["node_modules/lib.js"]["skip_reason"] == "Installed dependencies"
    assert records["app.js"]["status"] == "pending"
    assert not (tmp_path / "node_modules").exists()


def test_skipped_files_do_not_count_toward_size_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "MAX_FILE_ANALYSIS_BYTES", 10)
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


def test_an_oversized_file_is_skipped_not_extracted(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "MAX_FILE_ANALYSIS_BYTES", 10)

    records = safe_extract_archive(make_zip({"root/app.py": b"x" * 11, "root/ok.py": b"1"}),
                                   tmp_path)

    by_path = {r["relative_path"]: r for r in records}
    assert by_path["app.py"]["skip_reason"] == "Larger than 10 bytes"
    assert not (tmp_path / "app.py").exists()
    assert (tmp_path / "ok.py").exists()


def test_reject_total_size_overflow_past_the_hard_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HARD_MAX_SOURCE_BYTES", 15)
    monkeypatch.setattr(config, "MAX_SOURCE_BYTES", 100)
    archive = make_zip({"root/a.py": b"x" * 10, "root/b.py": b"y" * 10})

    with pytest.raises(IngestionError, match="total uncompressed size"):
        safe_extract_archive(archive, tmp_path)


def test_over_the_source_limit_a_zip_keeps_the_most_important_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_SOURCE_FILES", 3)
    archive = make_zip({"root/README.md": b"r", "root/main.py": b"m",
                        **{f"root/pkg/deep/f{i}.py": b"x" for i in range(4)},
                        "root/top.py": b"t"})

    records = {r["relative_path"]: r for r in safe_extract_archive(archive, tmp_path)}

    kept = sorted(p for p, r in records.items() if r["status"] == "pending")
    assert kept == ["README.md", "main.py", "top.py"]
    assert records["pkg/deep/f0.py"]["skip_reason"] == rules.LEFT_OUT
    assert not (tmp_path / "pkg").exists()


def test_reject_too_many_entries(tmp_path, monkeypatch):
    from ingestion import archive as archive_module

    monkeypatch.setattr(archive_module, "MAX_LISTED_ENTRIES", 3)
    archive = make_zip({f"root/f{i}.csv": b"x" for i in range(4)})

    with pytest.raises(IngestionError, match="more than 3"):
        safe_extract_archive(archive, tmp_path)


def test_a_zip_with_a_huge_csv_is_accepted_and_the_csv_is_skipped(tmp_path):
    # 20 MB of data next to the code: never decompressed, so no limit is hit.
    archive = make_zip({"root/data/train.csv": b"1,2,3\n" * 3_500_000,
                        "root/model.pt": b"\0" * 1000, "root/app.py": b"print(1)"},
                       zipfile.ZIP_DEFLATED)

    records = {r["relative_path"]: r for r in safe_extract_archive(archive, tmp_path)}

    assert records["data/train.csv"]["skip_reason"] == "Dataset"
    assert records["model.pt"]["skip_reason"] == "Model weights"
    assert records["app.py"]["status"] == "pending"
    assert not (tmp_path / "data").exists()


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


def tree_of(archive: bytes) -> list[dict]:
    """The git tree GitHub would list for a zipball's files (its root folder stripped)."""
    if not archive:
        return []
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        return [{"path": info.filename.split("/", 1)[1], "type": "blob", "mode": "100644",
                 "size": info.file_size, "sha": "b" * 40}
                for info in z.infolist() if not info.is_dir()]


def github_transport(
    archive: bytes,
    *,
    private: bool = False,
    sha: str = COMMIT_SHA,
    status: int = 200,
    content_length: str | None = None,
    tree: list[dict] | None = None,
    raw: dict[str, bytes] | None = None,
) -> httpx.MockTransport:
    """A fake api.github.com (repo, commit, tree, zipball) and raw.githubusercontent.com."""
    listing = tree if tree is not None else tree_of(archive)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if status != 200:
            return httpx.Response(status)

        if request.url.host == "raw.githubusercontent.com":
            prefix = f"/owner/repo/{sha}/"
            name = path[len(prefix):] if path.startswith(prefix) else ""
            if raw is not None and name in raw:
                return httpx.Response(200, content=raw[name])
            return httpx.Response(404)

        if path == f"/repos/owner/repo/git/trees/{sha}":
            return httpx.Response(200, json={"sha": sha, "tree": listing, "truncated": False})

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


def resolve(transport):
    with preflight.open_client(transport) as client:
        return preflight.resolve_commit(client, "owner", "repo")


def test_the_preflight_resolves_the_commit():
    metadata, sha = resolve(github_transport(b""))
    assert sha == COMMIT_SHA
    assert metadata["default_branch"] == "main"


def test_the_preflight_rejects_a_private_repo():
    with pytest.raises(IngestionError, match="public"):
        resolve(github_transport(b"", private=True))


def test_the_preflight_rejects_an_invalid_sha():
    with pytest.raises(IngestionError, match="valid commit SHA"):
        resolve(github_transport(b"", sha="../x"))


@pytest.mark.parametrize(("status", "message"), [
    (404, "not found, or it is private"),
    (403, "refused access"),
    (429, "limiting requests"),
    (500, "HTTP 500"),
])
def test_the_preflight_maps_http_errors(status, message):
    with pytest.raises(IngestionError, match=message):
        resolve(github_transport(b"", status=status))


def test_an_exhausted_rate_limit_says_when_and_how(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    reset = str(int(time.time()) + 600)

    def handler(request):
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0",
                                            "x-ratelimit-reset": reset})

    with pytest.raises(IngestionError) as caught:
        resolve(httpx.MockTransport(handler))
    assert "limit is used up" in str(caught.value)
    assert "10 minutes" in str(caught.value)
    assert "GITHUB_TOKEN" in str(caught.value)


@pytest.mark.parametrize(("failure", "message"), [
    (httpx.ConnectTimeout("slow"), "took too long"),
    (httpx.ConnectError("getaddrinfo failed"), "Check your internet connection"),
])
def test_network_failures_read_as_plain_advice(failure, message):
    def handler(request):
        raise failure

    with pytest.raises(IngestionError, match=message) as error:
        resolve(httpx.MockTransport(handler))
    assert "getaddrinfo" not in str(error.value)


def test_a_github_token_is_sent_when_set(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    seen = []

    def handler(request):
        seen.append(request.headers.get("Authorization"))
        return github_transport(b"").handle_request(request)

    resolve(httpx.MockTransport(handler))
    assert seen and all(value == "Bearer t0ken" for value in seen)


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
    # A digest of what was fetched (path + content hash of each selected file).
    assert snapshot["archive_sha256"] == fetch.manifest_digest(
        {"src/app.py": (11, service.sha256_bytes(b"print('hi')"))})
    assert result["fetched_with"] == "zipball"  # the whole repository is tiny

    coverage = {row["relative_path"]: row["status"] for row in get_coverage(snapshot_id)}
    assert coverage == {"src/app.py": "pending", "logo.png": "skipped"}

    requested: list[str] = []
    fake = github_transport(archive)

    def counting(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return fake.handle_request(request)

    again = ingest_github_repository(
        "https://github.com/owner/repo", transport=httpx.MockTransport(counting)
    )
    assert again["already_downloaded"] is True
    # The commit is already stored: no archive download, just the two lookups.
    assert requested == ["/repos/owner/repo", "/repos/owner/repo/commits/main"]


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
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    zip_path = isolated_storage / "big.zip"
    zip_path.write_bytes(make_zip({"proj/app.py": b"x" * 100}))

    with pytest.raises(IngestionError, match="larger than"):
        ingest_zip_file(zip_path)


def test_coverage_cli_output_is_json_serialisable(isolated_storage):
    zip_path = isolated_storage / "upload.zip"
    zip_path.write_bytes(make_zip({"proj/app.py": b"ok"}))

    result = ingest_zip_file(zip_path)

    json.dumps(result)
    json.dumps(dict(get_snapshot(result["snapshot_id"])))
