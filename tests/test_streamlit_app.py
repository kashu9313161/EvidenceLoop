"""Headless UI tests for the Streamlit app (no browser, no network)."""

import pytest
from streamlit.testing.v1 import AppTest

from ingestion import service
from tests.test_ingestion import COMMIT_SHA, github_transport, make_zip

ENTRYPOINT = "../app/streamlit_app.py"
PLACEHOLDER_PAGES = [
    "app_pages/understand.py",
    "app_pages/investigate.py",
    "app_pages/improve.py",
    "app_pages/module_map.py",
    "app_pages/export.py",
]


def new_app() -> AppTest:
    return AppTest.from_file(ENTRYPOINT, default_timeout=15).run()


def ingest_zip_through_ui(at: AppTest, files: dict[str, bytes]) -> AppTest:
    at.get_by_key("home_source").set_value("ZIP upload").run()
    at.file_uploader(key="home_zip").upload("proj.zip", make_zip(files))
    at.button(key="home_submit").click().run()
    return at


def test_home_renders_with_no_snapshots(isolated_storage):
    at = new_app()

    assert not at.exception
    assert at.title[0].value == "Add repository"
    assert any("Nothing ingested yet" in c.value for c in at.caption)


def test_zip_ingest_shows_result_and_coverage(isolated_storage):
    at = ingest_zip_through_ui(new_app(), {"proj/app.py": b"ok", "proj/logo.png": b"png"})

    assert not at.exception
    snapshot_id = at.session_state["snapshot"]
    assert snapshot_id.startswith("zip-")
    assert [m.value for m in at.metric][:3] == ["2", "1", "1"]

    at.switch_page("app_pages/coverage.py").run()

    assert not at.exception
    table = at.dataframe[-1].value
    assert sorted(table["relative_path"]) == ["app.py", "logo.png"]


def test_rejected_zip_shows_error_and_creates_nothing(isolated_storage):
    at = ingest_zip_through_ui(new_app(), {"proj/../evil.py": b"bad"})

    assert not at.exception
    assert at.error and "Unsafe archive path" in at.error[0].value
    assert "snapshot" not in at.session_state


def test_github_ingest_and_overview(isolated_storage, monkeypatch):
    archive = make_zip({"owner-repo-aaa/src/app.py": b"print('hi')"})
    real_ingest = service.ingest_github_repository
    monkeypatch.setattr(
        service,
        "ingest_github_repository",
        lambda url: real_ingest(url, transport=github_transport(archive)),
    )

    at = new_app()
    at.text_input(key="home_url").set_value("https://github.com/owner/repo")
    at.button(key="home_submit").click().run()

    assert not at.exception
    assert at.session_state["snapshot"] == f"github:owner/repo@{COMMIT_SHA}"

    at.switch_page("app_pages/overview.py").run()

    assert not at.exception
    assert any(COMMIT_SHA in str(t.value) for t in at.table)


def test_reingest_reports_already_ingested(isolated_storage):
    files = {"proj/app.py": b"ok"}
    at = ingest_zip_through_ui(new_app(), files)
    at.file_uploader(key="home_zip").upload("proj.zip", make_zip(files))
    at.button(key="home_submit").click().run()

    assert not at.exception
    assert any("Already ingested" in str(i.proto.title) for i in at.info)


@pytest.mark.parametrize("page", PLACEHOLDER_PAGES + ["app_pages/overview.py"])
def test_pages_without_snapshot_explain_what_to_do(isolated_storage, page):
    at = new_app()
    at.switch_page(page).run()

    assert not at.exception
    assert any("No snapshot selected" in str(i.proto.title) for i in at.info)


@pytest.mark.parametrize("page", PLACEHOLDER_PAGES)
def test_placeholder_pages_name_their_milestone(isolated_storage, page):
    at = ingest_zip_through_ui(new_app(), {"proj/app.py": b"ok"})
    at.switch_page(page).run()

    assert not at.exception
    assert any("Arrives in Milestone" in str(i.proto.title) for i in at.info)


def test_snapshot_survives_reload_via_query_param(isolated_storage):
    first = ingest_zip_through_ui(new_app(), {"proj/a.py": b"1"})
    first_id = first.session_state["snapshot"]
    ingest_zip_through_ui(first, {"proj/b.py": b"2"})  # newer snapshot becomes active

    # A fresh session (page reload / shared link) opened with ?snapshot=<older label>.
    # Streamlit serializes the selectbox's displayed label into the URL.
    at = AppTest.from_file(ENTRYPOINT, default_timeout=15)
    at.query_params["snapshot"] = f"proj (ZIP {first_id[4:11]})"
    at.run()

    assert not at.exception
    assert at.session_state["snapshot"] == first_id


def test_ingest_runs_analysis_and_overview_shows_code_structure(isolated_storage):
    files = {
        "proj/pkg/core.py": b"import os\n\n\nclass Engine:\n    def run(self):\n        return 1\n",
        "proj/README.md": b"# hi\n",
    }
    at = ingest_zip_through_ui(new_app(), files)

    assert not at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Code files analyzed"] == "1"
    assert labels["Indexed for search"] == "1"
    assert labels["Symbols"] == "3"  # module, Engine, Engine.run

    at.switch_page("app_pages/overview.py").run()

    assert not at.exception
    snapshot_id = at.session_state["snapshot"]
    assert at.selectbox(key=f"overview_file_{snapshot_id}").value == "pkg/core.py"
    symbols = at.dataframe[-2].value
    assert list(symbols["qualified_name"]) == ["Engine", "Engine.run"]
    assert at.button(key="overview_analyze").label == "Re-run analysis"

    at.button(key="overview_analyze").click().run()
    assert not at.exception

    at.switch_page("app_pages/coverage.py").run()

    assert not at.exception
    table = at.dataframe[-1].value.set_index("relative_path")
    assert table.loc["pkg/core.py", "python_ast"] == "analyzed"
    assert table.loc["README.md", "fts"] == "analyzed"
    assert table.loc["README.md", "status"] == "Indexed for search"
