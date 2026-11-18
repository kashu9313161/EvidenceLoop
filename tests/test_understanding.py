"""Understanding layer: import resolution, quality facts, graph metrics, insights, report."""

import codecs
import textwrap

import networkx as nx
import pytest

from analysis.graph.builder import build_graph, fan_in, impact, is_test_path
from analysis.graph.resolver import JsResolver, PythonResolver
from analysis.python.quality import analyze_python
from analysis.understanding.profile import parse_manifest, readme_summary
from analysis.understanding.report import load_report, run_full_analysis
from core import config
from core.textio import decode_text
from ingestion.service import ingest_zip_bytes
from storage.database import get_connection, snapshot_root
from tests.test_ingestion import make_zip


def src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


# ---------------------------------------------------------------------------- resolution


def test_relative_and_src_layout_imports_are_confirmed():
    files = {"src/pkg/__init__.py", "src/pkg/core.py", "src/pkg/util.py", "tests/test_core.py"}
    resolver = PythonResolver(files)

    rel = resolver.resolve("src/pkg/core.py", "util", "helper", 1)
    assert (rel.kind, rel.target, rel.tier) == ("internal", "src/pkg/util.py", "confirmed")

    absolute = resolver.resolve("tests/test_core.py", "pkg.core", "run", 0)
    assert (absolute.kind, absolute.target, absolute.tier) == (
        "internal", "src/pkg/core.py", "confirmed")

    # `from pkg import util` names a submodule, not an attribute of __init__
    sub = resolver.resolve("tests/test_core.py", "pkg", "util", 0)
    assert sub.target == "src/pkg/util.py"


def test_monorepo_project_root_resolution():
    files = {"app/src/api.py", "app/src/predict.py", "app/src/__init__.py"}
    resolver = PythonResolver(files)
    # run from app/: `from src.predict import x`
    res = resolver.resolve("app/src/api.py", "src.predict", "x", 0)
    assert (res.kind, res.target) == ("internal", "app/src/predict.py")
    # run from app/src/: `import predict`
    res = resolver.resolve("app/src/api.py", "predict", None, 0)
    assert res.target == "app/src/predict.py"


def test_ambiguous_or_stdlib_shadowing_imports_are_inferred():
    # From b/main.py both b/utils.py (script dir) and utils.py (repo root) could be `utils`.
    resolver = PythonResolver({"utils.py", "b/utils.py", "b/main.py", "a/logging.py",
                               "a/run.py"})
    ambiguous = resolver.resolve("b/main.py", "utils", None, 0)
    assert ambiguous.target == "b/utils.py"  # nearest wins
    assert ambiguous.tier == "inferred"
    shadow = resolver.resolve("a/run.py", "logging", None, 0)
    assert (shadow.target, shadow.tier) == ("a/logging.py", "inferred")


def test_stdlib_names_inside_a_package_are_the_standard_library():
    # click/_compat.py's `from types import TracebackType` is not click/types.py: Python 3
    # doesn't search a package module's own folder for absolute imports.
    resolver = PythonResolver({"src/click/__init__.py", "src/click/_compat.py",
                               "src/click/types.py"})
    stdlib = resolver.resolve("src/click/_compat.py", "types", "TracebackType", 0)
    assert (stdlib.kind, stdlib.package) == ("stdlib", "types")
    own = resolver.resolve("src/click/_compat.py", "click.types", "Path", 0)
    assert own.target == "src/click/types.py"


def test_external_stdlib_and_unresolved_classification():
    resolver = PythonResolver({"pkg/a.py"})
    assert resolver.resolve("pkg/a.py", "os.path", None, 0).kind == "stdlib"
    assert resolver.resolve("pkg/a.py", "pandas", None, 0).package == "pandas"
    assert resolver.resolve("pkg/a.py", "pkg.missing", None, 0).kind == "unresolved"


def test_js_resolution():
    resolver = JsResolver({"src/app.tsx", "src/lib/index.ts", "src/api.js"})
    assert resolver.resolve("src/app.tsx", "./lib").target == "src/lib/index.ts"
    assert resolver.resolve("src/app.tsx", "./api").target == "src/api.js"
    alias = resolver.resolve("src/app.tsx", "@/api")
    assert (alias.target, alias.tier) == ("src/api.js", "inferred")
    assert resolver.resolve("src/app.tsx", "@tanstack/react-query").package == (
        "@tanstack/react-query")
    assert resolver.resolve("src/app.tsx", "../../escape").kind == "unresolved"


# ---------------------------------------------------------------------------- graph


def _graph(imports: dict[str, list[tuple[str, str | None, int]]]):
    files = list(imports)
    rows = {path: [{"module": m, "name": n, "level": lvl, "line": i + 1}
                   for i, (m, n, lvl) in enumerate(refs)]
            for path, refs in imports.items()}
    return build_graph(files, rows)


def test_impact_walks_reverse_dependencies():
    # app imports auth, auth imports user_service
    result = _graph({
        "app.py": [("auth", None, 0)],
        "auth.py": [("user_service", None, 0)],
        "user_service.py": [],
    })
    assert impact(result.graph, "user_service.py") == {"auth.py", "app.py"}
    assert impact(result.graph, "app.py") == set()


def test_fan_in_counts_distinct_files_and_ignores_tests():
    result = _graph({
        "a.py": [("core", "x", 0), ("core", "y", 0)],
        "b.py": [("core", None, 0)],
        "tests/test_core.py": [("core", None, 0)],
        "core.py": [],
    })
    assert fan_in(result.graph, "core.py") == 2
    assert result.edges[("a.py", "core.py")].lines == [1, 2]
    assert result.edges[("tests/test_core.py", "core.py")].kind == "tests"


def test_cycles_are_detected():
    result = _graph({"a.py": [("b", None, 0)], "b.py": [("c", None, 0)],
                     "c.py": [("a", None, 0)], "d.py": [("a", None, 0)]})
    assert result.cycles == [["a.py", "b.py", "c.py"]]


def test_is_test_path():
    assert is_test_path("tests/x.py") and is_test_path("pkg/test_x.py")
    assert is_test_path("web/src/App.test.tsx") and not is_test_path("src/contest.py")


# ---------------------------------------------------------------------------- quality


def test_complexity_routes_and_smells():
    quality = analyze_python(src('''
        """Prediction API."""
        import typing as t
        from fastapi import FastAPI
        if t.TYPE_CHECKING:
            import typing_extensions as te

        app = FastAPI()
        API_KEY = "sk_live_9f8e7d6c5b4a"
        PASSWORD = "changeme"

        @app.post("/predict")
        def predict(items=[], *, cache={}):
            for item in items:
                if item and item > 1 or item < -1:
                    try:
                        pass
                    except Exception:
                        pass
            return [x for x in items if x]

        if __name__ == "__main__":
            predict()
    '''))
    fn = quality.functions[0]
    # 1 + for + if + boolop(2 extra) + except handler + comprehension(1 + 1 if) = 8
    assert fn.complexity == 8
    assert quality.routes[0].method == "POST" and quality.routes[0].path == "/predict"
    assert quality.app_objects[0][:2] == ("app", "FastAPI app")
    assert quality.has_main_guard == 21
    assert quality.module_docstring == "Prediction API."
    rules = [s.rule for s in quality.smells]
    assert rules.count("mutable_default") == 2
    assert "swallowed_exception" in rules
    assert rules.count("hardcoded_secret") == 1  # the placeholder is ignored
    assert 5 in quality.type_checking_lines


def test_utf16_text_is_decoded():
    data = codecs.BOM_UTF16_LE + "pandas==2.0\nnumpy\n".encode("utf-16-le")
    assert decode_text(data) == "pandas==2.0\nnumpy\n"
    assert decode_text(b"\x00\x01binary") is None


def test_manifest_parsing():
    reqs = parse_manifest("requirements.txt", "pandas==2.0\nnumpy>=1  # comment\n-r other.txt\n")
    assert [(r.name, r.pinned, r.line) for r in reqs.requirements] == [
        ("pandas", True, 1), ("numpy", False, 2)]
    pyproject = parse_manifest("pyproject.toml", src('''
        [project]
        dependencies = ["httpx>=0.27"]
        [build-system]
        requires = ["setuptools"]
    '''))
    assert pyproject.names() == {"httpx", "setuptools"}


def test_readme_summary_skips_taglines_and_badges():
    text = src('''
        <div align="center"><img src="x.svg"></div>

        # Tool

        [![CI](https://x/badge.svg)](https://x)

        ... so better sign this

        Various helpers to pass data to untrusted environments and to get it
        back safe and sound.
    ''')
    assert readme_summary(text) == (
        "Various helpers to pass data to untrusted environments and to get it back safe and "
        "sound.")


# ---------------------------------------------------------------------------- report


SAMPLE = {
    "README.md": b"# Shop\n\nA small shop backend that sells widgets over an HTTP API to anyone.\n",
    "requirements.txt": b"fastapi\n",
    "shop/__init__.py": b"",
    "shop/api.py": src('''
        from fastapi import FastAPI
        from shop.orders import place_order
        import requests

        app = FastAPI()

        @app.get("/orders")
        def orders():
            return place_order()
    ''').encode(),
    "shop/orders.py": src('''
        from shop import pricing

        def place_order(items=[]):
            return pricing.total(items)
    ''').encode(),
    "shop/pricing.py": src('''
        from shop.orders import place_order

        def total(items):
            return sum(items)
    ''').encode(),
    "shop/unused.py": b"def helper():\n    return 1\n",
    "tests/test_pricing.py": b"from shop.pricing import total\n\ndef test_total():\n"
                             b"    assert total([1]) == 1\n",
}


@pytest.fixture
def analyzed(isolated_storage):
    result = ingest_zip_bytes(make_zip(SAMPLE), "shop")
    report = run_full_analysis(result["snapshot_id"])
    return result["snapshot_id"], report


def test_report_end_to_end(analyzed):
    snapshot_id, report = analyzed
    summary = report["summary"]
    assert summary["headline"].startswith("shop is a Python web service")
    assert summary["readme"].startswith("A small shop backend")
    assert [r["path"] for r in report["architecture"]["routes"]] == ["/orders"]

    files = {f["path"]: f for f in report["files"]}
    assert files["shop/api.py"]["role"] == "api"
    assert files["shop/orders.py"]["used_by"] == ["shop/api.py", "shop/pricing.py"]
    assert files["shop/pricing.py"]["tested_by"] == ["tests/test_pricing.py"]

    rules = {i["rule"] for i in report["insights"]}
    assert {"import_cycle", "mutable_default", "undeclared_dependency", "orphan_module",
            "unpinned_dependencies"} <= rules
    undeclared = [i for i in report["insights"] if i["rule"] == "undeclared_dependency"]
    assert [i["title"] for i in undeclared] == ["`requests` is imported but not declared"]

    cycle = next(i for i in report["insights"] if i["rule"] == "import_cycle")
    assert cycle["files"] == ["shop/orders.py", "shop/pricing.py"]
    for evidence in cycle["evidence"]:
        line = evidence["snippet"]["lines"][0]
        assert "import" in line  # evidence points at the real import line

    edges = report["graph"]["edges"]
    assert all(e["tier"] == "confirmed" for e in edges)
    assert {(e["source"], e["target"]) for e in edges} >= {
        ("shop/api.py", "shop/orders.py"), ("shop/orders.py", "shop/pricing.py"),
        ("shop/pricing.py", "shop/orders.py")}

    # stored and reloadable; graph tables populated
    assert load_report(snapshot_id)["summary"]["headline"] == summary["headline"]
    with get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()[0]
    assert count == len(edges)


def test_report_is_idempotent(analyzed):
    snapshot_id, first = analyzed
    second = run_full_analysis(snapshot_id)
    assert [i["title"] for i in second["insights"]] == [i["title"] for i in first["insights"]]
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM analysis_reports WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()[0] == 1


def test_snapshot_root_survives_a_moved_project(analyzed, monkeypatch, tmp_path):
    snapshot_id, _ = analyzed
    with get_connection() as connection:
        row = dict(connection.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone())
    real = snapshot_root(row)
    moved = dict(row, root_path="C:\\Users\\someone\\old\\data\\repositories\\"
                 + "\\".join(real.parts[-2:]))
    monkeypatch.setattr(config, "REPOSITORIES_DIR", real.parents[1])
    assert snapshot_root(moved) == real
    monkeypatch.setattr(config, "REPOSITORIES_DIR", tmp_path / "nowhere")
    assert snapshot_root(moved) is None


def test_graph_json_matches_networkx(analyzed):
    _, report = analyzed
    graph = nx.DiGraph()
    graph.add_edges_from((e["source"], e["target"]) for e in report["graph"]["edges"])
    nodes = {n["id"]: n for n in report["graph"]["nodes"]}
    for node_id, node in nodes.items():
        if node_id in graph and not node["is_test"]:
            expected = sum(1 for p in graph.predecessors(node_id) if not is_test_path(p))
            assert node["fan_in"] == expected


# ---------------------------------------------------------------------------- review fixes


TYPED = {
    "pyproject.toml": b"[project]\nname = 'pkg'\n",
    "pkg/__init__.py": b"from pkg.core import run\nfrom pkg.state import current\n",
    "pkg/core.py": b"from pkg.state import current\n\ndef run():\n    return current()\n",
    "pkg/state.py": src('''
        import typing as t

        if t.TYPE_CHECKING:
            from pkg.core import run

        def current():
            return 1
    ''').encode(),
    "pkg/a.py": b"from pkg.state import current\n",
    "pkg/b.py": b"from pkg.state import current\n",
    "tests/test_pkg.py": b"import pkg\n\ndef test_run():\n    assert pkg.run() == 1\n",
}


def test_type_hint_imports_make_no_cycle_and_package_tests_count(isolated_storage):
    snapshot_id = ingest_zip_bytes(make_zip(TYPED), "pkg")["snapshot_id"]
    report = run_full_analysis(snapshot_id)
    rules = {i["rule"] for i in report["insights"]}
    assert "import_cycle" not in rules  # state -> core is for type hints only
    assert "untested_hub" not in rules  # `import pkg` in a test reaches pkg/state.py
    edge = next(e for e in report["graph"]["edges"]
                if (e["source"], e["target"]) == ("pkg/state.py", "pkg/core.py"))
    assert edge["type_only"] is True
    assert "through the package" in " ".join(report["summary"]["facts"])


def test_tsconfig_paths_package_names_and_node_builtins(isolated_storage):
    files = {
        "package.json": b'{"name": "lib", "exports": {".": "./index.js"}, '
                        b'"devDependencies": {"vite": "1"}}',
        "tsconfig.json": b'{\n  // aliases\n  "compilerOptions": {"paths": '
                         b'{"lib/*": ["./src/*.ts"],}}\n}',
        "src/index.ts": b"export * from './vanilla'\n",
        "src/vanilla.ts": b"export const create = () => 1\n",
        "vite.config.ts": b"import path from 'path'\nexport default {}\n",
        "tests/vanilla.test.ts": b"import { create } from 'lib/vanilla'\n",
        "tests/index.test.ts": b"import { create } from 'lib'\n",
    }
    report = run_full_analysis(ingest_zip_bytes(make_zip(files), "lib")["snapshot_id"])
    files_json = {f["path"]: f for f in report["files"]}
    assert files_json["src/vanilla.ts"]["tested_by"] == ["tests/vanilla.test.ts"]
    assert files_json["src/index.ts"]["tested_by"] == ["tests/index.test.ts"]
    assert not [i for i in report["insights"] if i["rule"] == "undeclared_dependency"]
    assert report["summary"]["kind"] == "library"


def test_nested_tsconfig_base_url_own_package_and_builtins():
    from analysis.graph.resolver import js_aliases, package_names

    aliases = js_aliases("web/tsconfig.json", '{"compilerOptions": {"baseUrl": "src", '
                                              '"paths": {"@app/*": ["app/*"]}}}')
    files = {"web/src/app/store.ts", "web/src/main.ts", "packages/ui/src/index.ts",
             "packages/ui/src/button.ts"}
    packages = package_names({"packages/ui/package.json": '{"name": "@acme/ui"}'})
    assert packages == {"@acme/ui": "packages/ui"}
    resolver = JsResolver(files, aliases, packages)

    assert resolver.resolve("web/src/main.ts", "@app/store").target == "web/src/app/store.ts"
    own = resolver.resolve("web/src/main.ts", "@acme/ui")
    assert (own.target, own.tier) == ("packages/ui/src/index.ts", "inferred")
    assert resolver.resolve("web/src/main.ts", "@acme/ui/button").target == (
        "packages/ui/src/button.ts")
    assert resolver.resolve("web/src/main.ts", "fs").kind == "stdlib"
    assert resolver.resolve("web/src/main.ts", "react").kind == "external"
    assert js_aliases("tsconfig.json", "not json") == []


EXAMPLES = {
    "pyproject.toml": b"[project]\nname = 'kit'\n",
    "kit/__init__.py": b"from kit.core import run\n",
    "kit/core.py": b"def run():\n    return 1\n",
    "kit/util.py": b"from kit.core import run\n",
    "kit/more.py": b"from kit.core import run\n",
    "tests/test_kit.py": b"import kit\n\ndef test_run():\n    assert kit.run() == 1\n",
    "docs_src/first/tutorial001.py": b"from fastapi import FastAPI\nfrom kit import run\n"
                                     b"app = FastAPI()\n\n@app.get('/')\ndef root():\n"
                                     b"    return run()\n",
    "docs_src/second/tutorial002.py": b"from kit import run\n\nif __name__ == '__main__':\n"
                                      b"    run()\n",
    "examples/demo/main.py": b"from kit import run\n\nif __name__ == '__main__':\n    run()\n",
}


def test_examples_and_docs_code_do_not_define_the_project(isolated_storage):
    report = run_full_analysis(ingest_zip_bytes(make_zip(EXAMPLES), "kit")["snapshot_id"])
    summary = report["summary"]
    assert summary["kind"] == "library"  # not a web service because of a docs tutorial
    assert summary["headline"].startswith("kit is a Python library: 4 source files")
    assert "plus 3 example or docs files" in summary["headline"]
    facts = " ".join(summary["facts"])
    assert "tutorial" not in facts and "main.py" not in facts
    assert summary["totals"]["source_files"] == 4
    assert summary["totals"]["supporting_files"] == 3
    areas = report["architecture"]["areas"]
    names = [a["name"] for a in areas]
    # One area per example folder, after the project's own areas and before tests.
    assert names == ["kit", "docs_src", "examples", "tests"]
    assert [a["supporting"] for a in areas] == [False, True, True, False]
    assert all(f["path"].startswith("kit/") for f in summary["key_files"])
    assert not [i for i in report["insights"] if i["rule"] == "orphan_module"]


def test_a_repository_of_only_examples_is_still_described():
    from analysis.graph.builder import support_area

    assert support_area("docs_src/security/tutorial001.py") == "docs_src"
    assert support_area("packages/a/examples/x/y.ts") == "packages/a/examples"
    assert support_area("src/kit/core.py") is None


def test_imports_inside_functions_make_no_cycle_but_still_count(isolated_storage):
    files = {
        "pyproject.toml": b"[project]\nname = 'app'\n",  # a second root entry: no unwrapping
        "app/__init__.py": b"",
        "app/core.py": b"from app.helpers import fmt\n\ndef run():\n    return fmt(1)\n",
        "app/helpers.py": src('''
            def fmt(value):
                from app.core import run  # deferred: runs on call, after both modules load
                return str(value)
        ''').encode(),
        "app/loop_a.py": b"from app.loop_b import b\na = 1\n",
        "app/loop_b.py": b"from app.loop_a import a\nb = 2\n",
    }
    report = run_full_analysis(ingest_zip_bytes(make_zip(files), "app")["snapshot_id"])
    cycles = [i["files"] for i in report["insights"] if i["rule"] == "import_cycle"]
    assert cycles == [["app/loop_a.py", "app/loop_b.py"]]  # a real load-time loop is kept
    files_json = {f["path"]: f for f in report["files"]}
    assert "app/helpers.py" in files_json["app/core.py"]["used_by"]  # still a dependency


def test_a_library_is_described_by_its_public_api_not_its_scripts(isolated_storage):
    files = {
        "pyproject.toml": b"[project]\nname = 'kit'\nclassifiers = "
                          b"['Topic :: Software Development :: Libraries']\n",
        "kit/__init__.py": b"from kit.core import run\nfrom kit.util import fmt\n",
        "kit/core.py": b"def run():\n    return 1\n",
        "kit/util.py": b"def fmt():\n    return ''\n",
        "scripts/release.py": b"import kit\n\nif __name__ == '__main__':\n    kit.run()\n",
    }
    report = run_full_analysis(ingest_zip_bytes(make_zip(files), "kit")["snapshot_id"])
    facts = " ".join(report["summary"]["facts"])
    assert "Its public API is kit/__init__.py" in facts
    assert "release.py" not in facts
    assert [f["entry"] for f in report["architecture"]["flows"]] == ["kit/__init__.py"]


def test_a_repository_without_code_says_so(isolated_storage):
    files = {"img/logo.png": b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "notes.txt": b"hello\n"}
    report = run_full_analysis(ingest_zip_bytes(make_zip(files), "assets")["snapshot_id"])
    headline = report["summary"]["headline"]
    assert headline == ("assets has no code EvidenceLoop can read: 2 files, none in Python, "
                        "JavaScript or TypeScript.")


def test_deleting_a_snapshot_removes_its_rows_and_files(isolated_storage):
    from ingestion import service
    from storage.database import SNAPSHOT_TABLES, delete_snapshot, get_snapshot

    keep = ingest_zip_bytes(make_zip({"k/a.py": b"x = 1\n"}), "keep")["snapshot_id"]
    gone = ingest_zip_bytes(make_zip(SAMPLE), "gone")["snapshot_id"]
    run_full_analysis(keep)
    run_full_analysis(gone)
    root = snapshot_root(get_snapshot(gone))

    assert delete_snapshot(gone, service.REPOSITORIES_DIR) is True
    assert get_snapshot(gone) is None and not root.exists()
    with get_connection() as connection:
        for table in SNAPSHOT_TABLES:
            left = connection.execute(
                f"SELECT count(*) FROM {table} WHERE snapshot_id = ?", (gone,)).fetchone()[0]
            assert left == 0, table
    assert get_snapshot(keep) is not None and load_report(keep) is not None
    assert delete_snapshot(gone, service.REPOSITORIES_DIR) is False
