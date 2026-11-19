"""M2 code analysis: Python extractor, JS/TS shallow imports, pipeline and storage."""

import textwrap

import pytest

from analysis.javascript import shallow
from analysis.models import AnalysisFailure
from analysis.pipeline import AnalysisError, analyze_snapshot
from analysis.python.extractor import extract_file, module_name_for
from ingestion.service import ingest_zip_bytes
from storage.database import (
    get_connection,
    get_file_stages,
    get_latest_analysis,
    list_imports,
    list_symbols,
    search_symbols,
)
from tests.test_ingestion import make_zip


def py(source: str):
    return extract_file("pkg/mod.py", textwrap.dedent(source).lstrip("\n"))


def by_qualname(facts):
    return {symbol.qualified_name: symbol for symbol in facts.symbols}


# ---------------------------------------------------------------------------
# Python extractor
# ---------------------------------------------------------------------------


def test_nested_classes_and_methods_get_qualified_names():
    facts = py(
        """
        class Outer:
            class Inner:
                def method(self):
                    def helper():
                        return 1
                    return helper()

            def run(self):
                pass
        """
    )
    symbols = by_qualname(facts)

    assert symbols["Outer"].kind == "class"
    assert symbols["Outer.Inner"].kind == "class"
    assert symbols["Outer.Inner"].parent == "Outer"
    assert symbols["Outer.Inner.method"].kind == "method"
    assert symbols["Outer.Inner.method.helper"].kind == "function"
    assert symbols["Outer.run"].kind == "method"


def test_line_ranges_include_decorators_and_body():
    facts = py(
        """
        import functools


        @functools.lru_cache(maxsize=None)
        @staticmethod
        def cached(x):
            '''Doc.'''
            return x
        """
    )
    cached = by_qualname(facts)["cached"]

    assert (cached.start_line, cached.end_line) == (4, 8)
    assert cached.decorators == ["functools.lru_cache(maxsize=None)", "staticmethod"]
    assert cached.has_docstring
    assert cached.text.splitlines()[0] == "@functools.lru_cache(maxsize=None)"


def test_async_functions_and_class_bases():
    facts = py(
        """
        class Repo(Base, metaclass=Meta):
            async def fetch(self):
                await self.load()
        """
    )
    symbols = by_qualname(facts)

    assert symbols["Repo"].bases == ["Base", "metaclass=Meta"]
    assert symbols["Repo.fetch"].is_async
    assert not symbols["Repo"].has_docstring


def test_class_text_is_header_only():
    facts = py(
        """
        class Signer:
            '''Signs things.'''
            default_salt = b"x"

            def sign(self):
                return 1
        """
    )
    signer = by_qualname(facts)["Signer"]

    assert "default_salt" in signer.text
    assert "def sign" not in signer.text
    assert signer.end_line == 6


def test_module_symbol_covers_file_and_loose_code():
    facts = py(
        """
        '''Module doc.'''
        import os

        LIMIT = 3


        def f():
            return LIMIT
        """
    )
    module = facts.symbols[0]

    assert module.kind == "module" and module.qualified_name == "<module>"
    assert module.has_docstring
    assert (module.start_line, module.end_line) == (1, 8)
    assert "LIMIT = 3" in module.text and "def f" not in module.text
    assert facts.module == "pkg.mod"


def test_imports_cover_plain_from_relative_and_alias():
    facts = py(
        """
        import os.path as osp, sys
        from . import utils
        from ..core.config import DATABASE_PATH as DB, MAX_FILES
        from typing import *

        def lazy():
            import json
        """
    )
    refs = [(r.kind, r.module, r.name, r.alias, r.level, r.line) for r in facts.imports]

    assert refs == [
        ("import", "os.path", None, "osp", 0, 1),
        ("import", "sys", None, None, 0, 1),
        ("from", "", "utils", None, 1, 2),
        ("from", "core.config", "DATABASE_PATH", "DB", 2, 3),
        ("from", "core.config", "MAX_FILES", None, 2, 3),
        ("from", "typing", "*", None, 0, 4),
        ("import", "json", None, None, 0, 7),
    ]


def test_calls_record_callee_line_and_enclosing_symbol():
    facts = py(
        """
        import json

        CONFIG = json.loads("{}")

        class A:
            def go(self, handlers, key):
                self.prepare()
                handlers[key]()
                getattr(self, key)()
        """
    )
    calls = [(c.callee, c.line, c.enclosing_symbol) for c in facts.calls]

    assert ("json.loads", 3, None) in calls
    assert ("self.prepare", 7, "A.go") in calls
    assert calls.count(("<dynamic>", 8, "A.go")) == 1
    # getattr(self, key)() is dynamic; the inner getattr(...) is a plain call.
    assert ("<dynamic>", 9, "A.go") in calls
    assert ("getattr", 9, "A.go") in calls


def test_syntax_error_raises_analysis_failure_with_line():
    with pytest.raises(AnalysisFailure, match=r"Syntax error at line 2"):
        extract_file("bad.py", "def ok():\n    return (\n")


def test_empty_file_has_only_a_module_symbol():
    facts = extract_file("pkg/__init__.py", "")

    assert [s.kind for s in facts.symbols] == ["module"]
    assert facts.module == "pkg"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/itsdangerous/signer.py", "itsdangerous.signer"),
        ("pkg/__init__.py", "pkg"),
        ("setup.py", "setup"),
        ("src/__init__.py", "src"),
    ],
)
def test_module_name_for(path, expected):
    assert module_name_for(path) == expected


# ---------------------------------------------------------------------------
# JS/TS shallow extractor
# ---------------------------------------------------------------------------


def js(source: str, path: str = "web/app.ts"):
    return shallow.extract_file(path, textwrap.dedent(source).lstrip("\n"))


def test_js_import_forms():
    facts = js(
        """
        import React from "react";
        import { useState, useEffect as useFx } from 'react';
        import * as api from "./api";
        import Default, { named } from "../lib/thing";
        import type { Props } from "./types";
        import "./styles.css";
        const fs = require("fs");
        const lazy = await import("./lazy");
        import {
          a,
          b,
        } from "./multi";
        """
    )
    refs = [(r.kind, r.module, r.name, r.alias, r.line) for r in facts.imports]

    assert ("import", "react", "default", "React", 1) in refs
    assert ("import", "react", "useState", None, 2) in refs
    assert ("import", "react", "useEffect", "useFx", 2) in refs
    assert ("import", "./api", "*", "api", 3) in refs
    assert ("import", "../lib/thing", "default", "Default", 4) in refs
    assert ("import", "../lib/thing", "named", None, 4) in refs
    # `import type` is erased at runtime, so the graph must not count it as a dependency.
    assert ("type", "./types", "Props", None, 5) in refs
    assert ("side_effect", "./styles.css", None, None, 6) in refs
    assert ("require", "fs", None, None, 7) in refs
    assert ("dynamic", "./lazy", None, None, 8) in refs
    assert ("import", "./multi", "a", None, 9) in refs
    assert ("import", "./multi", "b", None, 9) in refs
    assert facts.language == "typescript"


def test_js_exports_and_reexports():
    facts = js(
        """
        export function sign() {}
        export const VERSION = "1";
        export default class Signer {}
        export { helper, other as alias };
        export * from "./all";
        export { x as y } from "./xs";
        export type { Shape } from "./shapes";
        """,
        path="lib/index.ts",
    )
    exports = [(s.name, s.start_line) for s in facts.symbols if s.kind == "export"]
    reexports = [(r.module, r.name, r.alias) for r in facts.imports if r.kind == "reexport"]

    assert ("sign", 1) in exports
    assert ("VERSION", 2) in exports
    assert ("Signer", 3) in exports
    assert ("default", 3) not in exports
    assert ("helper", 4) in exports and ("alias", 4) in exports
    assert ("y", 6) in exports
    assert reexports == [("./all", "*", None), ("./xs", "x", "y")]
    types = [(r.module, r.name) for r in facts.imports if r.kind == "type"]
    assert types == [("./shapes", "Shape")]
    assert ("Shape", 7) in exports
    assert facts.language == "typescript"


def test_js_commented_imports_are_ignored_but_strings_are_kept():
    facts = js(
        """
        // import nope from "commented";
        /* const x = require("block");
           import also from "nope"; */
        const url = "http://example.com"; import real from "./real";
        obj.import("not-a-module");
        """
    )
    modules = [r.module for r in facts.imports]

    assert modules == ["./real"]
    assert facts.imports[0].line == 4


def test_anonymous_default_export():
    facts = js("export default class extends Base {}\nexport default () => 1;\n",
               path="a.js")

    assert [(s.name, s.start_line) for s in facts.symbols if s.kind == "export"] == [
        ("default", 1),
        ("default", 2),
    ]


# ---------------------------------------------------------------------------
# Pipeline and storage
# ---------------------------------------------------------------------------

PROJECT = {
    "proj/src/pkg/__init__.py": b"from .core import run\n",
    "proj/src/pkg/core.py": (
        b"import json\n\n\nclass Engine:\n    @property\n    def size(self):\n"
        b"        return 1\n\n    @size.setter\n    def size(self, value):\n"
        b"        self._size = value\n\n\ndef run():\n    return json.dumps(Engine().size)\n"
    ),
    "proj/src/pkg/broken.py": b"def oops(:\n",
    "proj/web/app.js": b"import { run } from './api';\n",
    "proj/README.md": b"# Project\nSearchable text about engines.\n",
    "proj/data.json": b"{\x00}",
    "proj/logo.png": b"\x89PNG",
}


@pytest.fixture
def snapshot_id(isolated_storage):
    return ingest_zip_bytes(make_zip(PROJECT), "proj")["snapshot_id"]


def statuses(snapshot_id):
    with get_connection() as connection:
        return {
            row["relative_path"]: (row["status"], row["skip_reason"])
            for row in connection.execute(
                "SELECT relative_path, status, skip_reason FROM files WHERE snapshot_id = ?",
                (snapshot_id,),
            )
        }


def test_pipeline_sets_file_statuses_and_stages(snapshot_id):
    summary = analyze_snapshot(snapshot_id)
    files = statuses(snapshot_id)

    assert files["src/pkg/core.py"] == ("analyzed", None)
    assert files["web/app.js"] == ("analyzed", None)
    assert files["README.md"] == ("indexed", None)
    assert files["src/pkg/broken.py"][0] == "failed"
    assert "Syntax error at line 1" in files["src/pkg/broken.py"][1]
    assert files["data.json"] == ("failed", "Binary content")
    assert files["logo.png"] == ("skipped", "Media or document")

    stages = {(r["relative_path"], r["stage"]): r["status"] for r in get_file_stages(snapshot_id)}
    assert stages[("src/pkg/core.py", "python_ast")] == "analyzed"
    assert stages[("src/pkg/core.py", "fts")] == "analyzed"
    assert stages[("web/app.js", "js_shallow")] == "analyzed"
    assert stages[("src/pkg/broken.py", "python_ast")] == "failed"
    assert ("README.md", "python_ast") not in stages
    assert ("logo.png", "fts") not in stages

    assert summary["analyzed"] == 3  # __init__.py, core.py, app.js
    assert summary["indexed"] == 1
    assert summary["failed"] == 2
    assert summary["python_files"] == 2 and summary["js_ts_files"] == 1


def test_duplicate_symbol_names_get_unique_ids(snapshot_id):
    analyze_snapshot(snapshot_id)
    ids = [row["symbol_id"] for row in list_symbols(snapshot_id, "src/pkg/core.py")]

    assert "src/pkg/core.py::Engine.size" in ids
    assert "src/pkg/core.py::Engine.size@9" in ids
    assert len(ids) == len(set(ids))


def test_rerun_is_idempotent(snapshot_id):
    analyze_snapshot(snapshot_id)

    def counts():
        with get_connection() as connection:
            return {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE snapshot_id = ?", (snapshot_id,)
                ).fetchone()[0]
                for table in ("symbols", "imports", "calls", "file_stages", "files_fts",
                              "symbols_fts")
            }

    first = counts()
    analyze_snapshot(snapshot_id)

    assert counts() == first
    assert all(first[table] > 0 for table in first)
    assert get_latest_analysis(snapshot_id)["run_id"] == 2


def test_imports_and_search_are_queryable(snapshot_id):
    analyze_snapshot(snapshot_id)

    init_imports = list_imports(snapshot_id, "src/pkg/__init__.py")
    assert [(r["module"], r["name"], r["level"]) for r in init_imports] == [("core", "run", 1)]

    hits = search_symbols(snapshot_id, "engine")
    assert any(hit["qualified_name"] == "Engine" for hit in hits)
    # FTS operators in user input are treated as plain words, not syntax.
    assert search_symbols(snapshot_id, 'run OR "') is not None
    assert search_symbols(snapshot_id, "   ") == []


def test_latest_analysis_summary(snapshot_id):
    assert get_latest_analysis(snapshot_id) is None

    analyze_snapshot(snapshot_id)
    latest = get_latest_analysis(snapshot_id)

    assert latest["analyzer_version"] == "m2.1"
    assert latest["symbols"] > 0
    assert latest["symbol_kinds"]["class"] == 1


def test_missing_file_on_disk_is_failed_not_crash(snapshot_id, isolated_storage):
    with get_connection() as connection:
        root = connection.execute(
            "SELECT root_path FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()[0]

    from pathlib import Path

    (Path(root) / "README.md").unlink()
    analyze_snapshot(snapshot_id)

    assert statuses(snapshot_id)["README.md"] == (
        "failed",
        "File missing from the stored snapshot",
    )


def test_unknown_snapshot_raises(isolated_storage):
    with pytest.raises(AnalysisError, match="Unknown snapshot"):
        analyze_snapshot("zip-doesnotexist")


def test_an_empty_handler_that_leaves_a_checked_fallback_is_not_swallowing():
    from analysis.python.quality import analyze_python

    fallback = ("def f(raw):\n    ts = None\n    try:\n        ts = int(raw)\n"
                "    except Exception:\n        pass\n    if ts is None:\n"
                "        raise ValueError('bad')\n    return ts\n")
    swallowed = "def f():\n    try:\n        risky()\n    except Exception:\n        pass\n"
    unused = ("def f(raw):\n    try:\n        x = int(raw)\n    except Exception:\n"
              "        pass\n    return 1\n")
    assert [s.rule for s in analyze_python(fallback).smells] == []
    assert [s.rule for s in analyze_python(swallowed).smells] == ["swallowed_exception"]
    assert [s.rule for s in analyze_python(unused).smells] == ["swallowed_exception"]
