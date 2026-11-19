"""Preflight, selective fetch and folder uploads: all offline (mock transports, no git)."""

import io
import subprocess
from pathlib import Path

import httpx
import pytest

from core import config
from ingestion import fetch, preflight, rules, service
from ingestion.preflight import PRIORITY, Entry, NeedsScope, build_plan, priority_key
from ingestion.rules import IngestionError
from storage.database import get_coverage, get_snapshot
from tests.test_ingestion import COMMIT_SHA, github_transport

MB = 1024 * 1024


def blob(path: str, size: int, mode: str = "100644") -> dict:
    return {"path": path, "type": "blob", "mode": mode, "size": size, "sha": "c" * 40}


# A repository that is mostly data, models and a committed virtual environment.
BIG_TREE = [
    blob("README.md", 2_000),
    blob("requirements.txt", 300),
    blob("app/main.py", 4_000),
    blob("app/model.py", 3_000),
    blob("notebooks/train.py", 1_500),
    blob("data/train.csv", 400 * MB),
    blob("data/test.parquet", 50 * MB),
    blob("models/best.pt", 70 * MB),
    blob("models/clf.keras", 88 * MB),
    blob("docs/paper.pdf", 9 * MB),
    blob("venv/Lib/site-packages/numpy/core.py", 50_000),
    blob("project/venv/lib/python3.11/site-packages/requests/api.py", 6_000),
    blob("app/generated_bundle.js", 5 * MB),
    blob("link.py", 10, mode="120000"),
    {"path": "third_party", "type": "commit", "mode": "160000", "sha": "d" * 40},
    {"path": "app", "type": "tree", "mode": "040000", "sha": "e" * 40},
]


def entries(tree: list[dict]) -> list[Entry]:
    return preflight._entries_from(tree, "")[0]


# --------------------------------------------------------------------------- classification


def test_preflight_keeps_the_source_and_skips_data_models_venv_and_big_files():
    plan = build_plan(entries(BIG_TREE), owner="o", name="r", commit_sha=COMMIT_SHA)

    assert plan.status == "ok"
    assert [e.path for e in plan.selected] == [
        "README.md", "app/main.py", "app/model.py", "notebooks/train.py", "requirements.txt"]
    reasons = {path: reason for path, _, reason in plan.skipped}
    assert reasons["data/train.csv"] == reasons["data/test.parquet"] == "Dataset"
    assert reasons["models/best.pt"] == reasons["models/clf.keras"] == "Model weights"
    assert reasons["docs/paper.pdf"] == "Media or document"
    assert reasons["venv/Lib/site-packages/numpy/core.py"] == "Virtual environment"
    assert reasons["project/venv/lib/python3.11/site-packages/requests/api.py"] == \
        "Virtual environment"  # at any depth
    assert reasons["app/generated_bundle.js"] == "Larger than 1 MB"
    assert reasons["link.py"] == rules.SYMLINK
    assert reasons["third_party"] == rules.SUBMODULE

    summary = plan.to_dict()
    assert summary["repo_bytes"] > 600 * MB and summary["source_files"] == 5
    assert "a committed virtual environment" in summary["summary"]
    assert {r["reason"] for r in summary["skipped_by_reason"]} >= {"Dataset", "Model weights"}
    assert {f["folder"]: f["files"] for f in summary["folders"]} == {
        "": 2, "app": 2, "notebooks": 1}


def test_vendor_is_skipped_only_for_javascript_and_go():
    assert rules.skip_reason("web/vendor/jquery.js") == "Installed dependencies"
    assert rules.skip_reason("pkg/vendor/helpers.py") is None
    assert rules.skip_reason("icons/big.svg", 200 * 1024) == "Media or document"
    assert rules.skip_reason("icons/small.svg", 2 * 1024) == rules.UNSUPPORTED


def test_unsafe_and_case_clashing_tree_paths_are_skipped_not_fetched():
    plan = build_plan(entries([blob("aux.py", 1), blob("App.py", 1), blob("app.py", 1)]))
    reasons = {path: reason for path, _, reason in plan.skipped}
    assert reasons == {"aux.py": rules.UNSAFE_NAME, "app.py": rules.CASE_DUPLICATE}
    assert [e.path for e in plan.selected] == ["App.py"]


# --------------------------------------------------------------------------- the tree


def test_a_truncated_tree_is_listed_folder_by_folder_without_entering_venv():
    asked: list[str] = []
    trees = {
        f"{COMMIT_SHA}?recursive=1": {"truncated": True, "tree": []},
        COMMIT_SHA: {"tree": [blob("README.md", 10),
                              {"path": "src", "type": "tree", "sha": "s1"},
                              {"path": "venv", "type": "tree", "sha": "v1"}]},
        "s1?recursive=1": {"truncated": True, "tree": []},
        "s1": {"tree": [blob("main.py", 20), {"path": "pkg", "type": "tree", "sha": "p1"}]},
        "p1?recursive=1": {"truncated": False, "tree": [blob("core.py", 30),
                                                        blob("sub/util.py", 40)]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1] + (
            "?" + request.url.query.decode() if request.url.query else "")
        asked.append(key)
        return httpx.Response(200, json=trees[key]) if key in trees else httpx.Response(404)

    with preflight.open_client(httpx.MockTransport(handler)) as client:
        found = preflight.fetch_tree(client, "o", "r", COMMIT_SHA)

    assert [(e.path, e.size, e.kind) for e in found] == [
        ("README.md", 10, "file"), ("src/main.py", 20, "file"),
        ("src/pkg/core.py", 30, "file"), ("src/pkg/sub/util.py", 40, "file"),
        ("venv", 0, "folder")]
    assert not any(key.startswith("v1") for key in asked)  # never listed inside venv
    plan = build_plan(found)
    assert ("venv", 0, "Virtual environment") in plan.skipped


# --------------------------------------------------------------------------- limits & scope


def test_over_the_limits_the_plan_needs_a_scope_with_folder_counts(monkeypatch):
    monkeypatch.setattr(config, "MAX_SOURCE_FILES", 3)
    plan = build_plan(entries(BIG_TREE))

    assert plan.status == "needs_scope" and plan.selected == []
    assert "pick a folder" in plan.describe()
    assert plan.to_dict()["folders"][0] == {"folder": "", "files": 2, "bytes": 2_300,
                                            "top_level_files": True}

    scoped = build_plan(entries(BIG_TREE), scope="app/")
    assert scoped.status == "ok"
    assert [e.path for e in scoped.selected] == ["app/main.py", "app/model.py"]
    assert ("README.md", 2_000, rules.OUTSIDE_SCOPE) in scoped.skipped

    with pytest.raises(IngestionError, match="no source files in data/"):
        build_plan(entries(BIG_TREE), scope="data")
    with pytest.raises(IngestionError):
        build_plan(entries(BIG_TREE), scope="../etc")


def test_analyse_anyway_picks_manifests_then_entry_points_then_shallow_files(monkeypatch):
    monkeypatch.setattr(config, "MAX_SOURCE_FILES", 4)
    tree = [blob("a/b/c/deep.py", 10), blob("z.py", 10), blob("pkg/cli.py", 10),
            blob("pkg/x/README.md", 10), blob("pyproject.toml", 10), blob("lib/y.py", 10)]
    plan = build_plan(entries(tree), scope=PRIORITY)

    assert [e.path for e in plan.selected] == [
        "pkg/cli.py", "pkg/x/README.md", "pyproject.toml", "z.py"]
    left = {p for p, _, r in plan.skipped if r == rules.LEFT_OUT}
    assert left == {"a/b/c/deep.py", "lib/y.py"}
    assert priority_key("manage.py")[0] == 0 and priority_key("src/app.py")[0] == 1


def test_the_byte_limit_also_counts(monkeypatch):
    monkeypatch.setattr(config, "MAX_SOURCE_BYTES", 25)
    tree = [blob("README.md", 20), blob("a.py", 10), blob("b.py", 4)]
    assert build_plan(entries(tree)).status == "needs_scope"
    chosen = build_plan(entries(tree), scope=PRIORITY).selected
    assert [e.path for e in chosen] == ["README.md", "b.py"]  # a.py would go over


# --------------------------------------------------------------------------- git


def test_the_sparse_checkout_commands_carry_the_hardening_flags(tmp_path):
    commands = fetch.sparse_checkout_commands("o", "r", COMMIT_SHA, tmp_path / "w")
    for command in commands:
        joined = " ".join(command)
        for flag in ("core.symlinks=false", f"core.hooksPath={fetch.NULL_DEVICE}",
                     "protocol.file.allow=never", "protocol.ext.allow=never",
                     "submodule.recurse=false", "filter.lfs.smudge="):
            assert f"-c {flag}" in joined
    clone = commands[0]
    for flag in ("--filter=blob:none", "--depth", "--no-checkout", "--no-tags",
                 "--no-recurse-submodules"):
        assert flag in clone
    assert clone[-2:] == ["https://github.com/o/r.git", str(tmp_path / "w")]
    assert commands[2][-3:] == ["set", "--no-cone", "--stdin"]
    assert commands[3][-2:] == ["--detach", COMMIT_SHA]
    assert fetch.git_env()["GIT_TERMINAL_PROMPT"] == "0"
    assert fetch.sparse_pattern("a/b[1]*.py") == "/a/b\\[1]\\*.py"


def fake_git(files: dict[str, bytes], calls: list):
    """A stand-in for subprocess.run: records each command and, on checkout, writes the
    files (and a .git folder) as a sparse checkout would."""
    def run(command, **kwargs):
        calls.append((command, kwargs))
        workdir = Path(command[command.index("-C") + 1]) if "-C" in command else None
        if "sparse-checkout" in command:
            patterns = kwargs["stdin"].read().decode().splitlines()
            calls.append(("patterns", patterns))
        if command[-2] == "--detach":
            for path, data in files.items():
                target = workdir.joinpath(*path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (workdir / ".git" / "objects").mkdir(parents=True)
            (workdir / ".git" / "objects" / "pack").write_bytes(b"pack")
        return subprocess.CompletedProcess(command, 0, b"", b"")
    return run


def big_repo_transport(raw: dict[str, bytes] | None = None):
    tree = [blob("app/main.py", 9), blob("README.md", 4), blob("data/huge.csv", 900 * MB)]
    return github_transport(b"", tree=tree, raw=raw)


def test_a_big_repository_is_fetched_with_git_and_only_its_source(isolated_storage):
    calls: list = []
    run = fake_git({"app/main.py": b"print(1)\n", "README.md": b"# r\n"}, calls)
    seen_plan: list[dict] = []

    result = service.ingest_github_repository(
        "https://github.com/owner/repo", transport=big_repo_transport(), run=run,
        git="git", on_plan=seen_plan.append)

    assert result["fetched_with"] == "git"  # 900 MB repo: no zipball
    assert ("patterns", ["/README.md", "/app/main.py"]) in calls
    assert all(kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0" and kwargs["timeout"]
               for command, kwargs in calls if command != "patterns")
    root = Path(result["root_path"])
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == [
        "README.md", "app", "app/main.py"]  # .git removed, nothing else
    coverage = {r["relative_path"]: (r["status"], r["skip_reason"])
                for r in get_coverage(result["snapshot_id"])}
    assert coverage["data/huge.csv"] == ("skipped", "Dataset")
    assert coverage["app/main.py"][0] == "pending"
    assert seen_plan[0]["source_files"] == 2


def test_git_that_writes_an_unexpected_file_falls_back_to_raw_downloads(isolated_storage):
    calls: list = []
    run = fake_git({"app/main.py": b"print(1)\n", "README.md": b"# r\n", "evil.py": b"x"},
                   calls)
    raw = {"app/main.py": b"print(1)\n", "README.md": b"# r\n"}

    result = service.ingest_github_repository(
        "https://github.com/owner/repo", transport=big_repo_transport(raw), run=run, git="git")

    assert result["fetched_with"] == "raw"
    assert not (Path(result["root_path"]) / "evil.py").exists()


def test_without_git_each_file_is_downloaded_and_size_checked(isolated_storage):
    raw = {"app/main.py": b"print(1)\n", "README.md": b"# r\n"}
    result = service.ingest_github_repository(
        "https://github.com/owner/repo", transport=big_repo_transport(raw), git=None)
    assert result["fetched_with"] == "raw"
    assert (Path(result["root_path"]) / "app" / "main.py").read_bytes() == b"print(1)\n"
    assert get_snapshot(result["snapshot_id"])["scope"] is None


def test_a_raw_file_bigger_than_the_tree_says_is_refused(isolated_storage):
    raw = {"app/main.py": b"print(1)\n" + b"#" * 5000, "README.md": b"# r\n"}
    with pytest.raises(IngestionError, match="larger than GitHub said"):
        service.ingest_github_repository(
            "https://github.com/owner/repo", transport=big_repo_transport(raw), git=None)
    assert not (isolated_storage / "repositories" / "owner_repo" / COMMIT_SHA).exists()


def test_the_raw_fetch_caps_each_file_at_the_analysis_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "MAX_FILE_ANALYSIS_BYTES", 5)
    plan = preflight.Plan(owner="owner", name="repo", commit_sha=COMMIT_SHA, scope=None,
                          entries_listed=1, repo_bytes=9, selected=[Entry("a.py", 9)])
    with pytest.raises(IngestionError, match="larger than"):
        fetch.fetch_raw(plan, tmp_path, transport=github_transport(b"", raw={"a.py": b"x" * 9}))


def test_needs_scope_is_raised_with_the_plan_and_a_scoped_run_works(isolated_storage,
                                                                    monkeypatch):
    monkeypatch.setattr(config, "MAX_SOURCE_FILES", 1)
    raw = {"app/main.py": b"print(1)\n", "README.md": b"# r\n"}
    transport = big_repo_transport(raw)

    with pytest.raises(NeedsScope) as caught:
        service.ingest_github_repository("https://github.com/owner/repo", transport=transport,
                                         git=None)
    assert caught.value.plan.status == "needs_scope"
    assert {f["folder"] for f in caught.value.plan.to_dict()["folders"]} == {"", "app"}
    assert "Archive exceeded" not in str(caught.value)

    scoped = service.ingest_github_repository("https://github.com/owner/repo",
                                              transport=transport, scope="app", git=None)
    assert scoped["snapshot_id"] == f"github:owner/repo@{COMMIT_SHA}#app"
    assert get_snapshot(scoped["snapshot_id"])["scope"] == "app"

    top = service.ingest_github_repository("https://github.com/owner/repo",
                                           transport=transport, scope=PRIORITY, git=None)
    assert top["snapshot_id"].endswith("#top")
    coverage = {r["relative_path"]: r["skip_reason"] for r in get_coverage(top["snapshot_id"])}
    assert coverage["app/main.py"] == rules.LEFT_OUT and coverage["README.md"] is None


# --------------------------------------------------------------------------- folder uploads


def folder(files: dict[str, bytes]) -> list[tuple[str, io.BytesIO]]:
    return [(path, io.BytesIO(data)) for path, data in files.items()]


def test_a_folder_upload_writes_only_eligible_files(isolated_storage):
    result = service.ingest_folder(folder({
        "proj/app.py": b"print(1)", "proj/data/big.csv": b"1,2\n" * 1000,
        "proj/.venv/lib/x.py": b"x", "proj/README.md": b"# hi"}))

    root = Path(result["root_path"])
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.*")) == [
        "README.md", "app.py"]
    assert result["snapshot_id"].startswith("folder-")
    coverage = {r["relative_path"]: r["skip_reason"] for r in get_coverage(result["snapshot_id"])}
    assert coverage["data/big.csv"] == "Dataset"
    again = service.ingest_folder(folder({"proj/app.py": b"print(1)", "proj/README.md": b"# hi"}))
    assert again["already_downloaded"] is True and again["snapshot_id"] == result["snapshot_id"]


@pytest.mark.parametrize("bad", ["proj/../../outside.py", "/etc/passwd.py", "C:/x.py",
                                 "proj/a\\..\\b.py", "proj/con.py", "proj/a/./b.py"])
def test_a_folder_upload_rejects_unsafe_relative_paths(isolated_storage, bad):
    with pytest.raises(IngestionError):
        service.ingest_folder(folder({bad: b"x", "proj/ok.py": b"y"}))
    assert not (isolated_storage.parent / "outside.py").exists()
    left = isolated_storage / "repositories" / "uploaded_folders"
    assert not left.exists() or not any(left.iterdir())


def test_a_folder_upload_rejects_case_clashes_and_needs_source(isolated_storage):
    with pytest.raises(IngestionError, match="apart from case"):
        service.ingest_folder(folder({"p/App.py": b"1", "p/app.py": b"2"}))
    with pytest.raises(IngestionError, match="no source code"):
        service.ingest_folder(folder({"p/a.csv": b"1"}))


# --------------------------------------------------------------------------- coverage


def test_the_report_says_how_much_of_the_source_was_analysed(isolated_storage, monkeypatch):
    from analysis.understanding.report import run_full_analysis

    monkeypatch.setattr(config, "MAX_SOURCE_FILES", 2)
    files = {"proj/main.py": b"print(1)\n", "proj/README.md": b"# p\n",
             **{f"proj/lib/m{i}.py": b"x = 1\n" for i in range(3)},
             "proj/tools/t.py": b"y = 2\n"}
    snapshot = service.ingest_folder(folder(files))["snapshot_id"]
    scope = run_full_analysis(snapshot)["coverage"]["scope"]

    assert scope["kind"] == "priority"
    assert scope["analysed_source_files"] == 2 and scope["source_files"] == 6
    assert scope["left_out_folders"] == [{"folder": "lib", "files": 3},
                                         {"folder": "tools", "files": 1}]
    assert scope["sentence"] == "Analysed 2 of 6 source files. Left out: lib/, tools/."
