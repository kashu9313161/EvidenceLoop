"""JavaScript and TypeScript import resolution and library detection, from real-repository
patterns that used to resolve nothing: TypeScript ESM imports written with `.js` (ky), folder
requires such as `require('..')` (express), a package importing itself by name from a `source/`
folder (ky), and a published package with no `main` (express)."""

import json

import pytest

from analysis.graph.resolver import JsResolver
from analysis.understanding.profile import parse_manifest
from analysis.understanding.report import run_full_analysis
from ingestion.service import ingest_zip_bytes
from tests.test_ingestion import make_zip

TS_FILES = {"source/index.ts", "source/core/Ky.ts", "source/errors/HTTPError.ts",
            "source/components/Button.tsx", "source/esm/util.mts", "source/cjs/legacy.cts",
            "source/plain.js", "source/plain.ts"}


@pytest.mark.parametrize(("importer", "specifier", "expected"), [
    ("source/core/Ky.ts", "../errors/HTTPError.js", "source/errors/HTTPError.ts"),
    ("source/index.ts", "./components/Button.js", "source/components/Button.tsx"),
    ("source/index.ts", "./components/Button.jsx", "source/components/Button.tsx"),
    ("source/index.ts", "./esm/util.mjs", "source/esm/util.mts"),
    ("source/index.ts", "./cjs/legacy.cjs", "source/cjs/legacy.cts"),
    ("source/index.ts", "./plain.js", "source/plain.js"),  # a real .js file wins
])
def test_typescript_imports_written_with_js_extensions(importer, specifier, expected):
    found = JsResolver(TS_FILES).resolve(importer, specifier)
    assert (found.kind, found.target, found.tier) == ("internal", expected, "confirmed")


def test_a_missing_file_is_still_unresolved():
    assert JsResolver(TS_FILES).resolve("source/index.ts", "./nope.js").kind == "unresolved"


JS_FILES = {"index.js", "lib/index.js", "lib/router/index.js", "lib/utils.js",
            "test/app.js", "test/router/route.js"}


@pytest.mark.parametrize(("importer", "specifier", "expected"), [
    ("test/app.js", "..", "index.js"),        # the repository root's index file
    ("test/app.js", "../", "index.js"),
    ("test/router/route.js", "../..", "index.js"),
    ("test/app.js", "../lib", "lib/index.js"),
    ("lib/utils.js", ".", "lib/index.js"),
    ("lib/utils.js", "./router", "lib/router/index.js"),
])
def test_folder_requires_resolve_to_their_index_file(importer, specifier, expected):
    found = JsResolver(JS_FILES).resolve(importer, specifier)
    assert (found.kind, found.target) == ("internal", expected)


def test_a_folder_above_the_repository_is_not_resolved():
    assert JsResolver(JS_FILES).resolve("index.js", "..").kind == "unresolved"


@pytest.mark.parametrize(("specifier", "expected"), [
    ("ky", "source/index.ts"),
    ("ky/errors/HTTPError", "source/errors/HTTPError.ts"),
])
def test_a_package_importing_itself_from_a_source_folder(specifier, expected):
    found = JsResolver(TS_FILES, packages={"ky": ""}).resolve("test-d/index.test-d.ts",
                                                             specifier)
    assert (found.kind, found.target, found.tier) == ("internal", expected, "inferred")


def test_own_package_still_prefers_src_over_source():
    files = {"src/index.ts", "source/index.ts"}
    assert JsResolver(files, packages={"x": ""}).resolve("t.ts", "x").target == "src/index.ts"


@pytest.mark.parametrize(("manifest", "library"), [
    ({"name": "express", "version": "5.0.0", "files": ["index.js", "lib/"]}, True),
    ({"name": "x", "publishConfig": {"access": "public"}}, True),
    ({"name": "x", "main": "index.js"}, True),
    ({"name": "x", "exports": "./index.js", "private": True}, True),  # exports always count
    ({"name": "app", "private": True, "files": ["dist"]}, False),
    ({"name": "cli", "bin": {"cli": "bin.js"}}, False),
    ({"name": "site", "scripts": {"start": "next start"}}, False),
])
def test_published_javascript_packages_are_libraries(manifest, library):
    assert parse_manifest("package.json", json.dumps(manifest)).library is library


# --------------------------------------------------------------------------- end to end

KY_LIKE = {
    "package.json": json.dumps({"name": "kylike", "type": "module",
                                "exports": {"default": "./distribution/index.js"}}).encode(),
    "source/index.ts": b"import {Ky} from './core/Ky.js';\nexport default Ky;\n",
    "source/core/Ky.ts": b"import {HTTPError} from '../errors/HTTPError.js';\n"
                         b"export class Ky { fail() { throw new HTTPError(); } }\n",
    "source/errors/HTTPError.ts": b"export class HTTPError extends Error {}\n",
    "test/main.ts": b"import test from 'ava';\nimport ky from '../source/index.js';\n"
                    b"test('works', t => { t.truthy(ky); });\n",
    "test-d/types.test-d.ts": b"import ky from 'kylike';\nky;\n",
}

EXPRESS_LIKE = {
    "package.json": json.dumps({"name": "expresslike", "version": "1.0.0",
                                "files": ["index.js", "lib/"]}).encode(),
    "index.js": b"module.exports = require('./lib/express');\n",
    "lib/express.js": b"var utils = require('./utils');\nmodule.exports = function () {};\n",
    "lib/utils.js": b"exports.noop = function () {};\n",
    "test/app.js": b"var express = require('../');\nvar assert = require('node:assert');\n"
                   b"describe('app', function () { assert(express); });\n",
    "test/utils.js": b"var express = require('..');\nvar utils = require('../lib/utils');\n",
}


def analyse(files: dict[str, bytes], name: str) -> dict:
    return run_full_analysis(ingest_zip_bytes(make_zip(files), name)["snapshot_id"])


def test_a_typescript_esm_repository_is_connected(isolated_storage):
    report = analyse(KY_LIKE, "kylike")
    edges = {(e["source"], e["target"]) for e in report["graph"]["edges"]}
    assert ("source/core/Ky.ts", "source/errors/HTTPError.ts") in edges
    assert ("source/index.ts", "source/core/Ky.ts") in edges
    assert report["coverage"]["imports"]["resolution_rate"] == 1.0
    files = {f["path"]: f for f in report["files"]}
    assert "test/main.ts" in files["source/index.ts"]["tested_by"]
    rules = {i["rule"] for i in report["insights"]}
    assert "orphan_module" not in rules
    assert not any(i["rule"] == "undeclared_dependency" and "kylike" in i["title"]
                   for i in report["insights"])  # importing itself isn't a missing dependency


def test_a_commonjs_package_with_folder_requires(isolated_storage):
    report = analyse(EXPRESS_LIKE, "expresslike")
    edges = {(e["source"], e["target"]) for e in report["graph"]["edges"]}
    assert ("test/app.js", "index.js") in edges
    assert ("test/utils.js", "index.js") in edges
    files = {f["path"]: f for f in report["files"]}
    assert files["index.js"]["tested_by"]  # tests reach the package through require('..')
    assert "library" in report["summary"]["headline"]
