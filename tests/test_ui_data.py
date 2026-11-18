import pytest

from app.components import read_source_range
from ingestion import service
from ingestion.service import IngestionError, ingest_zip_bytes, ingest_zip_file
from storage.database import get_coverage_summary, list_snapshots
from tests.test_ingestion import make_zip


def test_list_snapshots_empty(isolated_storage):
    assert list_snapshots() == []


def test_list_snapshots_joins_repository(isolated_storage):
    result = ingest_zip_bytes(make_zip({"proj/app.py": b"ok"}), "proj")

    rows = list_snapshots()

    assert [row["snapshot_id"] for row in rows] == [result["snapshot_id"]]
    assert rows[0]["source_type"] == "zip"
    assert rows[0]["name"] == "proj"
    assert rows[0]["total_files"] == 1


def test_coverage_summary_groups_by_status_and_reason(isolated_storage):
    archive = make_zip({
        "proj/a.py": b"1",
        "proj/b.py": b"22",
        "proj/logo.png": b"png",
        "proj/node_modules/x.js": b"x",
    })
    result = ingest_zip_bytes(archive, "proj")

    summary = {
        (row["status"], row["skip_reason"]): row["files"]
        for row in get_coverage_summary(result["snapshot_id"])
    }

    assert summary == {
        ("pending", None): 2,
        ("skipped", "Unsupported file type"): 1,
        ("skipped", "Ignored directory or file"): 1,
    }


def test_ingest_zip_bytes_matches_file_ingest(isolated_storage):
    archive = make_zip({"proj/app.py": b"ok"})
    zip_path = isolated_storage / "proj.zip"
    zip_path.write_bytes(archive)

    from_bytes = ingest_zip_bytes(archive, "proj")
    from_file = ingest_zip_file(zip_path)

    assert from_bytes["snapshot_id"] == from_file["snapshot_id"]
    assert from_file["already_downloaded"] is True


def test_ingest_zip_bytes_enforces_size_limit(isolated_storage, monkeypatch):
    monkeypatch.setattr(service, "MAX_ARCHIVE_DOWNLOAD_BYTES", 10)

    with pytest.raises(IngestionError, match="size limit"):
        ingest_zip_bytes(make_zip({"proj/app.py": b"x" * 100}), "proj")


def test_read_source_range_with_context(tmp_path):
    (tmp_path / "mod.py").write_text("\n".join(f"line {i}" for i in range(1, 11)))

    first, lines = read_source_range(tmp_path, "mod.py", 4, 5, context=1)

    assert first == 3
    assert lines == ["line 3", "line 4", "line 5", "line 6"]


def test_read_source_range_stays_inside_snapshot(tmp_path):
    (tmp_path / "outside.py").write_text("secret")
    root = tmp_path / "snapshot"
    root.mkdir()

    assert read_source_range(root, "../outside.py", 1, 1) is None
    assert read_source_range(root, "missing.py", 1, 1) is None
