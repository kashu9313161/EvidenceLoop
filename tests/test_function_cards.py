"""Function cards: code facts, call graph tiers, examples, AI checks, cache and fallback.

Offline: the model is a FakeProvider.
"""

import textwrap

import pytest
from fastapi.testclient import TestClient

from analysis.python.quality import analyze_python
from analysis.understanding import explain
from analysis.understanding.calls import build_call_graph
from analysis.understanding.function_facts import (
    CallSite,
    check_why,
    choose_example,
    statement_span,
)
from analysis.understanding.report import call_edges, load_report, run_full_analysis
from api import main as api_main
from ingestion.service import ingest_zip_bytes
from intelligence.providers.fake import FakeProvider
from storage.database import get_snapshot, snapshot_root
from tests.test_explain import no_ai
from tests.test_ingestion import make_zip


def src(text: str) -> bytes:
    return textwrap.dedent(text).lstrip("\n").encode()


STORE = src('''
    import logging
    import os
    import sqlite3
    from pathlib import Path

    log = logging.getLogger(__name__)


    class Store:
        def __init__(self, path):
            self.path = path

        def save(self, key: str, value: int = 0) -> bool:
            """Write one value to the database."""
            conn = sqlite3.connect(self.path)
            conn.execute("INSERT INTO t VALUES (?, ?)", (key, value))
            log.info("saved %s", key)
            if not key:
                raise ValueError("empty key")
            return True

        def save_all(self, items):
            for key, value in items:
                self.save(key, value)
            return len(items)


    def load_config(name, *paths, strict=False, **options):
        home = os.environ.get("HOME")
        text = Path(name).read_text()
        if strict and not text:
            raise RuntimeError("empty")
        return text + str(home)


    def tiny(x):
        return x + 1
''')

SERVICE = src('''
    from app import store as st
    from app.store import Store, load_config


    def run(items):
        cfg = load_config("a.toml")
        s = Store(cfg)
        s.save_all(items)
        handlers = {}
        handlers["x"]()
        st.tiny(1)
        return cfg


    def report(rows):
        return mystery_helper(rows)
''')

REPO = {
    "app/__init__.py": b"",
    "app/store.py": STORE,
    "app/service.py": SERVICE,
    "app/helpers.py": b"def mystery_helper(rows):\n    return rows\n",
    "tests/test_store.py": src('''
        from app.store import load_config


        def test_load():
            assert load_config("x")
    '''),
}

STORE_ID = "app/store.py::load_config"


@pytest.fixture
def repo(isolated_storage):
    snapshot_id = ingest_zip_bytes(make_zip(REPO), "app")["snapshot_id"]
    run_full_analysis(snapshot_id)
    return snapshot_id


# --------------------------------------------------------------------------- 1. code facts


def test_parameters_cover_every_kind_and_skip_self():
    quality = analyze_python(textwrap.dedent('''
        class C:
            def m(self, a: int, /, b=2, *args: str, c, d: bool = True, **kw) -> list[int]:
                pass

            @classmethod
            def build(cls, x=None):
                pass

        def f(self, y):
            pass

        def g(*, only):
            pass
    '''))
    by_name = {f.qualified_name: f for f in quality.functions}
    m = by_name["C.m"]
    assert [(p.name, p.kind, p.annotation, p.default) for p in m.parameters] == [
        ("a", "positional_only", "int", None),
        ("b", "positional", None, "2"),
        ("args", "var_positional", "str", None),
        ("c", "keyword_only", None, None),
        ("d", "keyword_only", "bool", "True"),
        ("kw", "var_keyword", None, None),
    ]
    assert m.returns == "list[int]"
    assert [p.name for p in by_name["C.build"].parameters] == ["x"]  # cls dropped
    assert [p.name for p in by_name["f"].parameters] == ["self", "y"]  # not a method: kept
    assert [(p.name, p.kind) for p in by_name["g"].parameters] == [("only", "keyword_only")]


def test_side_effects_raises_and_returns_are_found():
    quality = analyze_python(STORE.decode())
    by_name = {f.qualified_name: f for f in quality.functions}

    save = by_name["Store.save"]
    assert [(e.kind, e.call) for e in save.side_effects] == [
        ("database", "sqlite3.connect"), ("database", "conn.execute"), ("log", "log.info")]
    assert save.raises == [("ValueError", 19)]
    assert save.return_lines == [20]
    assert save.docstring == "Write one value to the database."

    load = by_name["load_config"]
    assert [(e.kind, e.line) for e in load.side_effects] == [("env", 29), ("file", 30)]
    assert load.raises == [("RuntimeError", 32)]
    assert by_name["tiny"].body_lines == 1


def test_side_effect_rules_follow_imports_and_skip_nested_functions():
    quality = analyze_python(textwrap.dedent('''
        import subprocess as sp
        from os import getenv
        import requests

        def outer(url, text):
            sp.run(["ls"])
            getenv("X")
            requests.get(url)
            print("done")
            text.replace("a", "b")

            def inner():
                open("f")
                raise KeyError
            return inner
    '''))
    outer = next(f for f in quality.functions if f.qualified_name == "outer")
    assert [e.kind for e in outer.side_effects] == ["subprocess", "env", "network", "log"]
    assert outer.raises == []  # the nested function's raise is its own
    inner = next(f for f in quality.functions if f.qualified_name == "outer.inner")
    assert [e.kind for e in inner.side_effects] == ["file"]


# --------------------------------------------------------------------------- 2. call graph


def test_calls_resolve_with_tiers(repo):
    def callees(symbol_id):
        return {(e["id"], e["tier"]) for e in call_edges(repo, symbol_id)["callees"]}

    assert callees("app/service.py::run") == {
        ("app/store.py::load_config", "confirmed"),  # from-import, name matches
        ("app/store.py::Store", "confirmed"),  # a class is called to build one
        ("app/store.py::Store.save_all", "inferred"),  # `s.save_all()`: name only
        ("app/store.py::tiny", "confirmed"),  # module alias: `st.tiny()`
    }
    assert callees("app/store.py::Store.save_all") == {("app/store.py::Store.save", "confirmed")}
    assert callees("app/service.py::report") == {("app/helpers.py::mystery_helper", "inferred")}

    report = load_report(repo)
    assert report["calls"]["dynamic"] == 2  # handlers["x"]() and Path(name).read_text()
    metrics = {f["id"]: f for info in report["files"] for f in info["function_metrics"]}
    assert metrics[STORE_ID]["callers"] == 2 and metrics[STORE_ID]["public"]


def test_calls_through_reexports_nested_and_self():
    symbols = {
        "pkg/__init__.py": [],
        "pkg/core.py": [
            {"symbol_id": "pkg/core.py::work", "kind": "function", "name": "work",
             "qualified_name": "work"},
            {"symbol_id": "pkg/core.py::work.step", "kind": "function", "name": "step",
             "qualified_name": "work.step"},
        ],
        "main.py": [
            {"symbol_id": "main.py::go", "kind": "function", "name": "go",
             "qualified_name": "go"},
        ],
    }
    imports = {
        "pkg/__init__.py": [{"kind": "from", "module": "core", "name": "work", "alias": None,
                             "level": 1, "line": 1}],
        "main.py": [{"kind": "from", "module": "pkg", "name": "work", "alias": "w",
                     "level": 0, "line": 1},
                    {"kind": "import", "module": "json", "name": None, "alias": None,
                     "level": 0, "line": 2}],
    }
    calls = {
        "pkg/core.py": [{"callee": "step", "line": 4, "enclosing_symbol": "work"}],
        "main.py": [{"callee": "w", "line": 5, "enclosing_symbol": "go"},
                    {"callee": "json.dumps", "line": 6, "enclosing_symbol": "go"}],
    }
    graph = build_call_graph(set(symbols), symbols, imports, calls)
    edges = {(e.caller, e.callee, e.tier) for e in graph.edges}
    assert edges == {
        ("pkg/core.py::work", "pkg/core.py::work.step", "confirmed"),  # nested function
        ("main.py::go", "pkg/core.py::work", "confirmed"),  # followed through __init__.py
    }
    assert graph.unresolved == 1  # json.dumps: the standard library


# --------------------------------------------------------------------------- 3. examples


def test_examples_prefer_tests_then_the_shortest(repo):
    card = explain.function_card(repo, STORE_ID, None)
    assert card["example"]["path"] == "tests/test_store.py"
    assert card["example"]["from_test"] is True
    assert card["example"]["code"] == 'assert load_config("x")'

    def site(path, length, is_test):
        return CallSite("id", "f", path, 1, "confirmed", is_test, 1, length, "x()")

    long_test = site("tests/t.py", 9, True)
    short = site("a.py", 2, False)
    assert choose_example([long_test, short]) is short  # a test longer than 6 lines doesn't fit
    assert choose_example([site("b.py", 3, False), short]) is short
    assert choose_example([long_test]) is None


def test_statement_span_is_the_header_of_a_block():
    import ast

    tree = ast.parse("if check(x):\n    y = 1\n\nvalue = make(\n    a,\n    b,\n)\n")
    assert statement_span(tree, 1) == (1, 1)  # `if check(x):` only, not the block
    assert statement_span(tree, 5) == (4, 7)  # the whole multi-line call


# --------------------------------------------------------------------------- 4. AI checks


def card_answer(steps, why="It is called by `run()` to read the settings file."):
    return {"purpose": "Reads a settings file.",
            "inputs": [{"name": "name", "meaning": "the file to read"},
                       {"name": "invented", "meaning": "not a parameter"}],
            "steps": steps, "output_meaning": "The file's text.", "why": why}


def test_ai_answers_are_checked_against_the_code(repo):
    provider = FakeProvider(card_answer([
        {"text": "Reads HOME.", "start": 29, "end": 29},
        {"text": "Reads the file.", "start": 30, "end": 30},
        {"text": "Outside the function.", "start": 1, "end": 2},
        {"text": "Checks strict mode.", "start": 31, "end": 32},
    ]))
    card = explain.explain_function(repo, STORE_ID, provider)

    assert card["ai"]["status"] == "ready"
    assert [s["start"] for s in card["steps"]] == [29, 30, 31]
    assert all(s["source"] == "ai-checked" for s in card["steps"])
    assert card["purpose"] == {"text": "Reads a settings file.", "source": "ai"}  # no docstring
    inputs = {i["name"]: i for i in card["inputs"]}
    assert inputs["name"]["meaning"] == "the file to read" and inputs["name"]["source"] == "ai"
    assert inputs["strict"]["default"] == "False" and inputs["strict"]["source"] == "code"
    assert card["why"]["source"] == "ai-checked" and "run()" in card["why"]["text"]
    assert card["output"]["meaning"] == "The file's text."
    assert card["ai"]["dropped"] == 1

    prompt = provider.calls[0]["messages"][0]["content"]
    assert "Called by:" in prompt and 'assert load_config("x")' in prompt
    assert "- strict = False (must be passed by name)" in prompt
    assert "env: os.environ.get (line 29)" in prompt
    assert "Not clear from the code." in provider.calls[0]["system"]


def test_why_naming_a_caller_that_does_not_exist_is_dropped(repo):
    provider = FakeProvider(card_answer(
        [{"text": "Reads the file.", "start": 31, "end": 31}],
        why="`checkout()` calls it before every order."))
    card = explain.explain_function(repo, STORE_ID, provider)
    assert card["why"]["source"] == "code"
    assert card["why"]["text"].startswith("Called from 2 places")
    assert card["ai"]["dropped"] == 1

    assert check_why("Used by `run()` in service.py with a `dict`.", {"run"}, {"run", "save"})
    assert not check_why("Called by `save()`.", {"run"}, {"run", "save"})


def test_docstring_beats_the_ai_purpose(repo):
    card = explain.explain_function(repo, "app/store.py::Store.save", FakeProvider(card_answer(
        [{"text": "Connects.", "start": 16, "end": 16}])))
    assert card["purpose"] == {"text": "Write one value to the database.", "source": "docstring"}


def test_trivial_functions_never_reach_the_model(repo):
    provider = FakeProvider(card_answer([]))
    card = explain.explain_function(repo, "app/store.py::tiny", provider)
    assert card["ai"]["status"] == "trivial" and provider.calls == []
    assert card["output"]["text"] == "Returns a value (line 37); its type isn't written down."


# --------------------------------------------------------------------------- 5. cache


def test_cache_follows_the_function_and_its_callers(repo):
    provider = FakeProvider(card_answer([{"text": "Reads.", "start": 31, "end": 31}]))
    explain.explain_function(repo, STORE_ID, provider)
    explain.explain_function(repo, STORE_ID, provider)
    assert len(provider.calls) == 1
    assert explain.function_card(repo, STORE_ID, provider)["ai"]["status"] == "ready"

    root = snapshot_root(get_snapshot(repo))
    service = root / "app/service.py"
    service.write_bytes(service.read_bytes().replace(b'"a.toml"', b'"b.toml"'))
    assert explain.function_card(repo, STORE_ID, provider)["ai"]["status"] == "missing"
    explain.explain_function(repo, STORE_ID, provider)
    assert len(provider.calls) == 2

    store = root / "app/store.py"
    store.write_bytes(store.read_bytes().replace(b'raise RuntimeError("empty")',
                                                 b'raise RuntimeError("blank")'))
    assert explain.function_card(repo, STORE_ID, provider)["ai"]["status"] == "missing"


def test_pregenerate_picks_called_or_complex_public_functions(repo):
    picked = explain.functions_worth_explaining(load_report(repo))
    assert STORE_ID in picked and "app/store.py::tiny" not in picked
    provider = FakeProvider(card_answer([{"text": "Reads.", "start": 31, "end": 31}]))
    assert explain.pregenerate_functions(repo, provider) == len(picked)


# --------------------------------------------------------------------------- 6. no key


def test_without_a_key_only_code_and_docstring_rows_show(repo, monkeypatch):
    no_ai(monkeypatch)
    client = TestClient(api_main.app, base_url="http://127.0.0.1:8000")
    params = {"snapshot": repo, "symbol": "app/store.py::Store.save"}
    got = client.get("/api/explain/function", params=params)
    assert got.status_code == 200
    body = got.json()
    assert body["available"] is False and body["status"] == "off"
    card = body["card"]
    assert card["purpose"]["source"] == "docstring"
    assert card["steps"] == []
    assert {i["source"] for i in card["inputs"]} == {"code"}
    assert card["why"]["source"] == "code" and card["ai"]["reason"]
    assert [e["kind"] for e in card["side_effects"]] == ["database", "database", "log"]
    assert client.post("/api/explain/function", json=params).json()["status"] == "off"

    file = client.get("/api/explain", params={"snapshot": repo, "path": "app/store.py"}).json()
    assert file["available"] is False
    facts = file["facts"]
    assert facts["outputs"]["names"][0]["name"] == "load_config"
    assert "service.py" in facts["why"]["text"]
    assert [s["symbol"] for s in facts["steps"]] == ["Store", "load_config", "tiny"]


def test_api_reads_the_card_and_writes_on_post(repo, monkeypatch):
    provider = FakeProvider(card_answer([{"text": "Reads.", "start": 31, "end": 31}]))
    monkeypatch.setattr(explain, "current_provider", lambda: provider)
    client = TestClient(api_main.app, base_url="http://127.0.0.1:8000")
    params = {"snapshot": repo, "symbol": STORE_ID}
    assert client.get("/api/explain/function", params=params).json()["status"] == "missing"
    assert provider.calls == []  # reading never calls the model
    posted = client.post("/api/explain/function", json=params).json()
    assert posted["status"] == "ready" and posted["card"]["steps"][0]["start"] == 31
    foreign = client.post("/api/explain/function", json=params,
                          headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    missing = client.get("/api/explain/function", params={**params, "symbol": "nope::x"})
    assert missing.json()["status"] == "unavailable"
