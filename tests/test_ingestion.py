
import io
import zipfile

import pytest

from ingestion.service import (
    IngestionError,
    parse_github_url,
    safe_extract_archive,
)


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)

    return buffer.getvalue()


def test_parse_github_url():
    owner, repo = parse_github_url(
        "https://github.com/psf/requests"
    )

    assert owner == "psf"
    assert repo == "requests"


def test_parse_github_url_with_git_suffix():
    owner, repo = parse_github_url(
        "https://github.com/psf/requests.git"
    )

    assert owner == "psf"
    assert repo == "requests"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/psf/requests",
        "https://evil.com/psf/requests",
        "https://github.com",
        "https://github.com/owner/repo/../../other",
        "https://github.com/psf/requests?query=value",
        "https://github.com/psf/requests#fragment",
        "https://github.com:8080/psf/requests",
    ],
)
def test_reject_invalid_github_urls(url):
    with pytest.raises(IngestionError):
        parse_github_url(url)


def test_extract_python_file(tmp_path):
    archive = make_zip({
        "project-root/app.py": b"print('hello')",
    })

    records = safe_extract_archive(archive, tmp_path)

    assert len(records) == 1
    assert records[0]["relative_path"] == "app.py"
    assert records[0]["status"] == "pending"
    assert (tmp_path / "app.py").read_bytes() == b"print('hello')"


def test_skip_unsupported_file(tmp_path):
    archive = make_zip({
        "project-root/photo.png": b"fake-image",
    })

    records = safe_extract_archive(archive, tmp_path)

    assert records[0]["status"] == "skipped"
    assert records[0]["skip_reason"] == "Unsupported file type"
    assert not (tmp_path / "photo.png").exists()


def test_reject_path_traversal(tmp_path):
    archive = make_zip({
        "project-root/../../outside.py": b"malicious",
    })

    with pytest.raises(IngestionError):
        safe_extract_archive(archive, tmp_path)


def test_reject_duplicate_normalized_paths(tmp_path):
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project-root/app.py", b"one")
        archive.writestr("project-root/app.py", b"two")

    with pytest.raises(IngestionError):
        safe_extract_archive(buffer.getvalue(), tmp_path)