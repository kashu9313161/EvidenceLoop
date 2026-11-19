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
    return TestClient(api_main.app, base_url="http://127.0.0.1:8000")


def wait_for(client, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["stage"] in {"done", "error", "needs_scope"}:
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


def test_a_bad_address_fails_with_a_message(client):
    # Fails before any network call, so the job may already be finished in the response:
    # the page must show this error instead of waiting for progress that never comes.
    job = client.post("/api/analyze", json={"url": "hello world"}).json()
    job = wait_for(client, job["id"])
    assert job["stage"] == "error"
    assert "github.com/owner/repository" in job["error"]


def test_a_link_inside_a_repository_is_named_by_owner_and_repo(client, monkeypatch):
    seen = []
    monkeypatch.setattr(api_main, "ingest_github_repository",
                        lambda url, **_: seen.append(url) or (_ for _ in ()).throw(
                            api_main.IngestionError("offline")))
    job = client.post("/api/analyze",
                      json={"url": "github.com/pallets/flask/tree/main/src/flask"}).json()
    assert job["source"] == "https://github.com/pallets/flask"
    wait_for(client, job["id"])
    assert seen == ["https://github.com/pallets/flask"]


# ---------------------------------------------------------------------------- local only


def test_a_foreign_host_name_is_refused(client):
    # DNS rebinding: a site whose domain resolves to 127.0.0.1 must not read the API.
    response = client.get("/api/repos", headers={"Host": "evil.example:8000"})
    assert response.status_code == 421
    assert client.get("/api/repos", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/api/repos", headers={"Host": "[::1]:8000"}).status_code == 200


def test_another_sites_page_cannot_upload(client):
    # A cross-site form upload needs no preflight, so CORS alone wouldn't stop it.
    files = {"file": ("x.zip", make_zip({"a.py": b""}), "application/zip")}
    refused = client.post("/api/analyze/upload", files=files,
                          headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403
    for origin in ("http://127.0.0.1:8000", "http://localhost:5173"):
        allowed = client.post("/api/analyze/upload", files=files, headers={"Origin": origin})
        assert allowed.status_code == 200
        wait_for(client, allowed.json()["id"])
    # Reading stays open to the app's own pages and to tools without an Origin.
    assert client.get("/api/repos", headers={"Origin": "https://evil.example"}).status_code \
        == 200


def test_extra_host_names_can_be_allowed(client, monkeypatch):
    monkeypatch.setenv("EVIDENCELOOP_ALLOWED_HOSTS", "devbox.local, 192.168.1.20")
    assert client.get("/api/health", headers={"Host": "devbox.local:8000"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "192.168.1.20"}).status_code == 200


def test_large_responses_are_compressed(client):
    job = client.post("/api/analyze/upload",
                      files={"file": ("shop.zip", make_zip(SAMPLE), "application/zip")}).json()
    snapshot = wait_for(client, job["id"])["snapshot_id"]
    response = client.get("/api/report", params={"snapshot": snapshot},
                          headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"


def test_the_same_upload_again_opens_at_once_and_a_rerun_still_analyses(client, monkeypatch):
    archive = make_zip(SAMPLE)
    first = wait_for(client, client.post(
        "/api/analyze/upload", files={"file": ("shop.zip", archive, "application/zip")}
    ).json()["id"])
    calls = []
    real = api_main.run_full_analysis
    monkeypatch.setattr(api_main, "run_full_analysis",
                        lambda sid, progress=None: calls.append(sid) or real(sid, progress))

    again = wait_for(client, client.post(
        "/api/analyze/upload", files={"file": ("shop.zip", archive, "application/zip")}
    ).json()["id"])
    assert again["stage"] == "done" and again["snapshot_id"] == first["snapshot_id"]
    assert again["message"] == "Already analysed" and calls == []

    rerun = wait_for(client, client.post(
        "/api/analyze", json={"snapshot_id": first["snapshot_id"]}).json()["id"])
    assert rerun["stage"] == "done" and calls == [first["snapshot_id"]]


def test_an_unknown_repository_explains_what_to_do(client):
    response = client.get("/api/report", params={"snapshot": "github:nobody/nothing@abc"})
    assert response.status_code == 404
    assert "Analyse it again from the home page" in response.json()["detail"]


def test_a_repository_can_be_removed(client):
    snapshot_id = ingest_zip_bytes(make_zip(SAMPLE), "shop")["snapshot_id"]
    client.get("/api/report", params={"snapshot": snapshot_id})
    assert [r["snapshot_id"] for r in client.get("/api/repos").json()] == [snapshot_id]

    foreign = client.post("/api/repos/remove", json={"snapshot": snapshot_id},
                          headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403  # another site can't delete your data

    removed = client.post("/api/repos/remove", json={"snapshot": snapshot_id})
    assert removed.status_code == 200 and removed.json() == {"removed": snapshot_id}
    assert client.get("/api/repos").json() == []
    assert client.post("/api/repos/remove", json={"snapshot": snapshot_id}).status_code == 404


def test_a_repository_being_analysed_cannot_be_removed(client, monkeypatch):
    snapshot_id = ingest_zip_bytes(make_zip(SAMPLE), "shop")["snapshot_id"]
    job = api_main.Job(id="busy", source="x", stage="parse", snapshot_id=snapshot_id)
    monkeypatch.setitem(api_main._jobs, "busy", job)
    refused = client.post("/api/repos/remove", json={"snapshot": snapshot_id})
    assert refused.status_code == 409 and "still being analysed" in refused.json()["detail"]


def test_an_outdated_report_is_rebuilt_in_the_background(client):
    snapshot_id = ingest_zip_bytes(make_zip(SAMPLE), "shop")["snapshot_id"]
    first = client.get("/api/report", params={"snapshot": snapshot_id, "background": 1})
    assert first.status_code == 202 and first.json()["rebuilding"] is True
    job = first.json()["job"]
    assert job["source"] == "shop" and job["snapshot_id"] == snapshot_id
    again = client.get("/api/report", params={"snapshot": snapshot_id, "background": 1})
    if again.status_code == 202:  # still running: the second request joins the same job
        assert again.json()["job"]["id"] == job["id"]
    assert wait_for(client, job["id"])["stage"] == "done"
    ready = client.get("/api/report", params={"snapshot": snapshot_id, "background": 1})
    assert ready.status_code == 200 and ready.json()["summary"]["headline"]


# --------------------------------------------------------------------------- big repositories


def test_a_repository_over_the_limits_ends_with_a_plan_to_pick_from(client, monkeypatch):
    from ingestion.preflight import Entry, NeedsScope, build_plan

    plan = build_plan([Entry("app/a.py", 10), Entry("lib/b.py", 10), Entry("data.csv", 10**9)],
                      max_files=1)
    scopes = []

    def needs_scope(url, scope=None, on_plan=None):
        scopes.append(scope)
        if scope is None:
            raise NeedsScope(plan)
        raise api_main.IngestionError("offline")

    monkeypatch.setattr(api_main, "ingest_github_repository", needs_scope)
    job = wait_for(client, client.post("/api/analyze", json={"url": "o/r"}).json()["id"])
    assert job["stage"] == "needs_scope" and job["error"] is None
    assert {f["folder"] for f in job["plan"]["folders"]} == {"app", "lib"}
    assert "Archive exceeded" not in job["message"]
    assert api_main._running_jobs() == 0  # waiting for a choice doesn't hold a slot

    wait_for(client, client.post("/api/analyze", json={"url": "o/r", "scope": "app"}).json()["id"])
    assert scopes == [None, "app"]


def test_a_folder_upload_is_analysed_and_bad_paths_are_refused(client):
    def send(paths: dict[str, bytes]):
        data, files = [], []
        for path, content in paths.items():
            files.append(("path", (None, path)))
            files.append(("file", ("blob", content, "application/octet-stream")))
        return client.post("/api/analyze/folder", files=files + data)

    good = send({f"shop/{p}": c.encode() if isinstance(c, str) else c for p, c in SAMPLE.items()})
    assert good.status_code == 200
    job = wait_for(client, good.json()["id"])
    assert job["stage"] == "done", job["error"]
    assert job["snapshot_id"].startswith("folder-")

    bad = wait_for(client, send({"shop/../../evil.py": b"x", "shop/ok.py": b"1"}).json()["id"])
    assert bad["stage"] == "error" and "Unsafe" in bad["error"]
    assert client.post("/api/analyze/folder", files=[("path", (None, "a.py"))]).status_code == 422


def test_the_config_ships_the_rules_to_the_browser(client):
    data = client.get("/api/config").json()
    assert ".py" in data["rules"]["supported_extensions"]
    assert "venv" in data["rules"]["ignored_directories"]["Virtual environment"]
    assert ".csv" in data["rules"]["skipped_extensions"]["Dataset"]
    assert data["limits"]["source_files"] > 0 and data["limits"]["upload_bytes"] >= 10**9
