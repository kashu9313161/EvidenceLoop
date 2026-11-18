"""HTTP API: analysis jobs, report, file view, search and path safety."""

import time

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from ingestion.service import ingest_zip_bytes
from tests.test_ingestion import make_zip
from tests.test_understanding import SAMPLE


@pytest.fixture
def client(isolated_storage):
    return TestClient(api_main.app)


def wait_for(client, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["stage"] in {"done", "error"}:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_upload_runs_the_whole_analysis(client):
    response = client.post("/api/analyze/upload",
                           files={"file": ("shop.zip", make_zip(SAMPLE), "application/zip")})
    assert response.status_code == 200
    job = wait_for(client, response.json()["id"])
    assert job["stage"] == "done", job["error"]

    repos = client.get("/api/repos").json()
    assert repos[0]["analyzed"] and repos[0]["totals"]["insights"] > 0

    report = client.get("/api/report", params={"snapshot": job["snapshot_id"]}).json()
    assert report["summary"]["headline"].startswith("shop is a Python web service")


def test_report_is_built_on_first_request(client):
    snapshot_id = ingest_zip_bytes(make_zip(SAMPLE), "shop")["snapshot_id"]
    report = client.get("/api/report", params={"snapshot": snapshot_id})
    assert report.status_code == 200
    assert {i["rule"] for i in report.json()["insights"]} >= {"import_cycle"}


def test_file_view_and_path_safety(client):
    snapshot_id = ingest_zip_bytes(make_zip(SAMPLE), "shop")["snapshot_id"]
    client.get("/api/report", params={"snapshot": snapshot_id})
    detail = client.get("/api/file", params={"snapshot": snapshot_id, "path": "shop/orders.py"})
    assert detail.status_code == 200
    body = detail.json()
    assert "def place_order" in body["content"]
    assert body["symbols"][0]["name"] == "place_order"

    for bad in ("../../etc/passwd", "/etc/passwd", "shop/../../secret"):
        assert client.get("/api/file",
                          params={"snapshot": snapshot_id, "path": bad}).status_code == 404


def test_search_finds_files_symbols_and_text(client):
    snapshot_id = ingest_zip_bytes(make_zip(SAMPLE), "shop")["snapshot_id"]
    client.get("/api/report", params={"snapshot": snapshot_id})
    found = client.get("/api/search", params={"snapshot": snapshot_id, "q": "place"}).json()
    assert "shop/orders.py" not in found["files"]  # path doesn't contain "place"
    assert any(s["name"] == "place_order" for s in found["symbols"])
    assert any(c["relative_path"] == "shop/api.py" for c in found["content"])


def test_bad_requests(client):
    assert client.post("/api/analyze", json={}).status_code == 422
    assert client.get("/api/report", params={"snapshot": "nope"}).status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404
    rejected = client.post("/api/analyze/upload",
                           files={"file": ("evil.zip", make_zip({"../x.py": b""}), "x")})
    job = wait_for(client, rejected.json()["id"])
    assert job["stage"] == "error" and job["error"]
