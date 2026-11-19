"""Security fixes from the review: slow regexes a crafted repository could trigger, which pages
may use the API, limits on concurrent analyses, and a safely encoded branch name."""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from analysis.javascript.shallow import extract_file
from analysis.understanding.explain import redact_secrets
from api import main as api_main
from ingestion.preflight import open_client, resolve_commit
from ingestion.service import IngestionError
from tests.test_ingestion import make_zip
from tests.test_understanding import SAMPLE


def quick(fn, *args, limit: float = 2.0):
    started = time.perf_counter()
    result = fn(*args)
    assert time.perf_counter() - started < limit, f"took over {limit}s"
    return result


# --------------------------------------------------------------------------- slow regexes


@pytest.mark.parametrize("source", [
    "import" + " " * 200_000 + "x",          # used to take seconds per few thousand spaces
    "import a " * 20_000,                     # every `import` scanned to the end of the file
    "import {" + "a, " * 100_000,             # an import list that never closes
    "import { " * 20_000,
])
def test_crafted_javascript_cannot_stall_the_parser(source):
    quick(extract_file, "a.js", source)


def test_real_imports_still_parse():
    source = ("import React from 'react'\nimport { a, b as c } from './x.js'\n"
              "import * as ns from 'z'\nimport type { T } from './t.js'\n"
              "import {\n" + ",\n".join(f"Icon{i}" for i in range(300)) + "\n} from './icons'\n")
    modules = {i.module for i in extract_file("a.ts", source).imports}
    assert modules == {"react", "./x.js", "z", "./t.js", "./icons"}


@pytest.mark.parametrize("text", [
    "token" * 200_000,                                   # one huge identifier
    ("api_key" + "x" * 50 + " ") * 20_000,
    "-----BEGIN RSA PRIVATE KEY-----\n" * 5_000,         # BEGIN with no END
])
def test_crafted_text_cannot_stall_secret_redaction(text):
    quick(redact_secrets, text)


@pytest.mark.parametrize(("text", "expected", "count"), [
    ('API_KEY = "abcdefgh1234"', 'API_KEY = "[redacted]"', 1),
    ("db_password = 'hunter22'", "db_password = '[redacted]'", 1),
    ('"secretKey": "zzzz9999"', '"secretKey": "[redacted]"', 1),
    ('auth_token => "qwertyuiop"', 'auth_token => "[redacted]"', 1),
    ('name = "widget"', 'name = "widget"', 0),
    ("MAX_TOKENS = 4000", "MAX_TOKENS = 4000", 0),
])
def test_redaction_still_finds_secrets(text, expected, count):
    assert redact_secrets(text) == (expected, count)


# --------------------------------------------------------------------------- who may use the API


@pytest.fixture
def client(isolated_storage):
    return TestClient(api_main.app, base_url="http://127.0.0.1:8000")


ZIP = {"file": ("shop.zip", make_zip(SAMPLE), "application/zip")}


def test_another_program_on_localhost_cannot_send_changes(client):
    for origin in ("http://127.0.0.1:3000", "http://localhost:8080", "null"):
        response = client.post("/api/analyze/upload", files=ZIP, headers={"Origin": origin})
        assert response.status_code == 403, origin
    own = client.post("/api/analyze/upload", files=ZIP,
                      headers={"Origin": "http://127.0.0.1:8000"})
    assert own.status_code == 200
    dev = client.post("/api/analyze/upload", files=ZIP, headers={"Origin": "http://localhost:5173"})
    assert dev.status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_requests_started_by_other_pages_are_refused(client, site):
    # e.g. <img src="http://127.0.0.1:8000/api/report?snapshot=...&background=1">
    response = client.get("/api/repos", headers={"Sec-Fetch-Site": site})
    assert response.status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_the_apps_own_pages_and_typed_addresses_still_work(client, site):
    assert client.get("/api/repos", headers={"Sec-Fetch-Site": site}).status_code == 200


def test_the_dev_server_and_non_browser_tools_still_work(client):
    assert client.get("/api/repos", headers={"Sec-Fetch-Site": "same-site",
                                             "Origin": "http://localhost:5173"}).status_code == 200
    assert client.get("/api/repos").status_code == 200  # curl, scripts: no fetch metadata


def test_pages_outside_the_api_are_not_blocked(client):
    # A link from another site to the app itself is a normal navigation.
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code != 403


# --------------------------------------------------------------------------- limits


def test_concurrent_analyses_are_capped(client, monkeypatch):
    monkeypatch.setattr(api_main, "MAX_RUNNING_JOBS", 0)
    response = client.post("/api/analyze", json={"url": "pallets/click"})
    assert response.status_code == 429 and "already running" in response.json()["detail"]
    upload = client.post("/api/analyze/upload", files=ZIP)
    assert upload.status_code == 429  # refused before the upload is read


def test_finished_jobs_are_forgotten_after_an_hour(client, monkeypatch):
    old = api_main.Job(id="old", source="x", stage="done", finished=time.time() - 7200)
    monkeypatch.setitem(api_main._jobs, "old", old)
    client.post("/api/analyze/upload", files=ZIP)
    assert "old" not in api_main._jobs


def test_search_text_has_a_length_limit(client):
    from ingestion.service import ingest_zip_bytes

    snapshot = ingest_zip_bytes(make_zip(SAMPLE), "shop")["snapshot_id"]
    assert client.get("/api/search", params={"snapshot": snapshot, "q": "x" * 201}).status_code \
        == 422


# --------------------------------------------------------------------------- GitHub


def test_the_branch_name_is_encoded_in_the_url():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        if request.url.path.endswith("/repos/o/n"):
            return httpx.Response(200, json={"private": False, "default_branch": "fix#1?x=y"})
        return httpx.Response(404, json={})

    with pytest.raises(IngestionError):  # the fake GitHub has no such commit
        with open_client(httpx.MockTransport(handler)) as client:
            resolve_commit(client, "o", "n")
    assert seen[1].endswith("/repos/o/n/commits/fix%231%3Fx%3Dy")


# --------------------------------------------------------------------------- crafted manifests

DEEP_JSON = "[" * 100_000 + "]" * 100_000


def test_crafted_manifests_are_unreadable_not_fatal():
    from analysis.graph.resolver import js_aliases, package_names
    from analysis.understanding.profile import parse_manifest

    assert parse_manifest("package.json", DEEP_JSON) is None
    assert parse_manifest("pyproject.toml", "a = " + DEEP_JSON) is None
    assert js_aliases("tsconfig.json", DEEP_JSON) == []
    assert package_names({"package.json": DEEP_JSON}) == {}
    odd = parse_manifest("pyproject.toml", 'project = "x"\n[tool]\npoetry = 3')
    assert odd is not None and odd.requirements == []


def test_a_malformed_section_keeps_the_rest_of_the_manifest():
    from analysis.understanding.profile import parse_manifest

    manifest = parse_manifest("package.json",
                              '{"dependencies": ["a"], "devDependencies": {"b": "1.0.0"}}')
    assert [r.name for r in manifest.requirements] == ["b"]
    py = parse_manifest("pyproject.toml",
                        '[project]\ndependencies = ["requests>=2"]\noptional-dependencies = ["x"]')
    assert [r.name for r in py.requirements] == ["requests"]


@pytest.mark.parametrize("manifest", [
    b'{"name": "x", "dependencies": ["a"]}',
    ("[" * 50_000 + "]" * 50_000).encode(),
])
def test_one_crafted_manifest_cannot_fail_the_whole_analysis(isolated_storage, manifest):
    from analysis.understanding.report import run_full_analysis
    from ingestion.service import ingest_zip_bytes

    files = {"package.json": manifest, "index.js": b"module.exports = 1\n"}
    report = run_full_analysis(ingest_zip_bytes(make_zip(files), "crafted")["snapshot_id"])
    assert report["summary"]["headline"]


# --------------------------------------------------------------------------- security headers


def test_every_response_forbids_framing_and_sniffing(client):
    for response in (client.get("/api/repos"), client.get("/"),
                     client.get("/api/repos", headers={"Host": "evil.example"})):  # a refusal
        csp = response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_the_inline_theme_script_is_allowed_by_hash_only(tmp_path):
    import base64
    import hashlib

    page = tmp_path / "index.html"
    page.write_text("<head><script>var t = 1;</script>"
                    "<script type=\"module\" src=\"/a.js\"></script></head>", encoding="utf-8")
    digest = base64.b64encode(hashlib.sha256(b"var t = 1;").digest()).decode()
    assert api_main._inline_script_hashes(page) == [f"'sha256-{digest}'"]
    assert api_main._inline_script_hashes(tmp_path / "missing.html") == []


def test_oversized_bodies_are_refused_before_reading(client):
    big = b"{" + b" " * (api_main.MAX_BODY_BYTES + 10) + b"}"
    response = client.post("/api/ask", content=big, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.headers["x-frame-options"] == "DENY"


# --------------------------------------------------------------------------- LIKE wildcards


def test_like_patterns_match_user_text_literally():
    import sqlite3

    from storage.database import like_literal

    assert like_literal(r"a_b%c\d") == r"a\_b\%c\\d"
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (v TEXT)")
    db.executemany("INSERT INTO t VALUES (?)", [("a_b",), ("aXb",), ("100%",), ("1000",)])
    found = lambda text: sorted(r[0] for r in db.execute(  # noqa: E731
        r"SELECT v FROM t WHERE v LIKE ? ESCAPE '\'", (f"%{like_literal(text)}%",)))
    assert found("a_b") == ["a_b"] and found("0%") == ["100%"]


# --------------------------------------------------------------------------- secrets files


@pytest.mark.parametrize(("path", "secret"), [
    (".env", True), ("config/.env.production", True), ("deploy/id_rsa", True),
    ("certs/server.pem", True), ("tls/site.key", True), ("secrets.json", True),
    ("config/credentials.yml", True), ("db_passwords.txt", True), (".npmrc", True),
    ("app/secret_manager.py", False), ("src/credentials.ts", False), ("keys.py", False),
    ("README.md", False), ("monkey.json", False),
])
def test_secret_files_are_recognised(path, secret):
    from retrieval.evidence import is_secret_file

    assert is_secret_file(path) is secret


def test_secret_files_are_never_sent_as_evidence(isolated_storage):
    from analysis.understanding.report import load_report, run_full_analysis
    from ingestion.service import ingest_zip_bytes
    from retrieval.evidence import build_evidence
    from retrieval.source import SnapshotFiles

    files = {**SAMPLE, "secrets.json": b'{"payment_gateway": "pk_live_payment_gateway_key"}',
             "shop/payments.py": b"def payment_gateway():\n    return 'gateway'\n"}
    snapshot = ingest_zip_bytes(make_zip(files), "shop")["snapshot_id"]
    run_full_analysis(snapshot)
    items = build_evidence(snapshot, "how does the payment_gateway work?", load_report(snapshot),
                           SnapshotFiles(snapshot))
    paths = {i.path for i in items}
    assert "shop/payments.py" in paths and "secrets.json" not in paths
