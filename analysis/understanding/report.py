"""Build the snapshot report: everything the workspace shows, computed once after analysis.

The report is a single JSON document stored per snapshot (table `analysis_reports`), and the
file-level graph is also written to `graph_nodes` / `graph_edges`. Nothing here calls an LLM:
every sentence is assembled from parsed facts and points back to files and lines.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import networkx as nx

from analysis.graph.builder import (
    area_of,
    build_graph,
    choose_area_depth,
    fan_in,
    impact,
    importance,
    is_test_path,
    support_area,
)
from analysis.graph.resolver import JsResolver, js_aliases, package_names
from analysis.python.quality import analyze_python
from analysis.understanding import insights as insight_rules
from analysis.understanding.profile import (
    ROLE_LABELS,
    STACK_CATALOG,
    STACK_ORDER,
    first_sentence,
    human_list,
    normalize_dist,
    parse_manifest,
    plural,
    readme_summary,
    short_name,
)
from core.textio import decode_text, detect_encoding
from storage.database import get_connection, get_snapshot, initialize_database, snapshot_root


def distinct_names(paths: list[str]) -> list[str]:
    """Short names, adding the parent folder where two files would read the same."""
    counts = Counter(short_name(p) for p in paths)
    out = []
    for path in paths:
        name = short_name(path)
        parts = PurePosixPath(path).parts
        out.append("/".join(parts[-2:]) if counts[name] > 1 and len(parts) > 1 else name)
    return out


REPORT_VERSION = "2.4"
CODE_SUFFIXES = {".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
                 ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript",
                 ".tsx": "TypeScript"}
MANIFEST_NAMES = {"pyproject.toml", "package.json", "setup.py"}
CI_MARKERS = (".github/workflows/", ".gitlab-ci.yml", ".circleci/", "azure-pipelines.yml",
              "Jenkinsfile", ".travis.yml", "bitbucket-pipelines.yml")
BUILD_SCRIPTS = {
    "setup.py": "Packaging script (setuptools)", "noxfile.py": "Task runner (nox)",
    "tasks.py": "Task runner", "fabfile.py": "Deployment tasks (Fabric)",
    "manage.py": "Django management entry point", "bench.py": "Benchmark script",
}
CONFIG_NAMES = {"config.py", "settings.py", "conf.py", "constants.py", "configuration.py"}
ML_PACKAGES = {"sklearn", "torch", "tensorflow", "keras", "xgboost", "lightgbm", "catboost",
               "transformers", "jax"}
SNIPPET_LINES = 14
SNIPPET_CONTEXT = 3  # lines shown either side of a short flagged range
SNIPPET_CONTEXT_BELOW = 4  # ranges shorter than this get context
DOCKER_NAMES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml"}
STDLIB_STACK = {"argparse", "sqlite3", "tkinter", "unittest"}
ENTRY_ROLES = {"entry", "api", "ui", "script"}
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
ROLE_NOUNS = {
    "test": ("test file", "test files"), "docs": ("docs config", "docs configs"),
    "api": ("API module", "API modules"), "ui": ("UI file", "UI files"),
    "entry": ("entry point", "entry points"), "config": ("config file", "config files"),
    "model": ("data-model module", "data-model modules"), "package": ("package", "packages"),
    "core": ("core module", "core modules"), "script": ("script", "scripts"),
    "module": ("module", "modules"), "component": ("component", "components"),
    "build": ("build script", "build scripts"), "stub": ("type stub", "type stubs"),
}
RUN_NOT_IMPORT = {"uvicorn", "gunicorn", "pytest", "celery", "vite", "jest", "vitest",
                  "tailwindcss"}

Progress = Callable[[str, int, int, str], None]


class ReportError(Exception):
    pass


# --------------------------------------------------------------------------- helpers


def resolve_root(snapshot) -> Path:
    root = snapshot_root(snapshot)
    if root is None:
        raise ReportError("The snapshot's files are missing on disk. Ingest the repository again.")
    return root


def _read_bytes(root: Path, relative_path: str) -> bytes | None:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _read_text(root: Path, relative_path: str) -> str | None:
    data = _read_bytes(root, relative_path)
    return decode_text(data) if data is not None else None


def _loc(lines: list[str], language: str) -> int:
    marker = "#" if language == "Python" else "//"
    return sum(1 for line in lines if line.strip() and not line.strip().startswith(marker))


def _snippet(lines: list[str] | None, start: int, end: int, redact: bool = False) -> dict:
    if not lines:
        return {"start": start, "lines": []}
    start = max(1, start)
    end = min(len(lines), max(start, end))
    if end - start < SNIPPET_CONTEXT_BELOW:
        # A short range (a handler, an import) means little alone: show the lines around it.
        first = max(1, start - SNIPPET_CONTEXT)
        stop = min(len(lines), end + SNIPPET_CONTEXT)
    else:
        first = start
        stop = min(end, start + SNIPPET_LINES - 1)
    chunk = lines[first - 1 : stop]
    if redact:
        chunk = [insight_rules.redact_line(line) for line in chunk]
    return {"start": first, "lines": chunk, "truncated": stop < end}


# --------------------------------------------------------------------------- roles


def _classify(path: str, info: dict, graph) -> tuple[str, str]:
    name = PurePosixPath(path).name
    quality = info.get("quality")
    packages = info["packages"]
    hub = fan_in(graph, path) if path in graph else 0

    if is_test_path(path):
        if name == "conftest.py":
            return "test", "Shared pytest fixtures"
        return "test", "Test file"
    if name == "conf.py" and "docs" in PurePosixPath(path).parts:
        return "docs", "Sphinx documentation configuration"
    if name in BUILD_SCRIPTS:
        return "build", BUILD_SCRIPTS[name]
    if path.endswith(".pyi"):
        return "stub", "Type stubs: signatures only, no implementation"
    if quality is not None:
        if quality.routes:
            return "api", f"Defines {plural(len(quality.routes), 'HTTP route')}"
        if packages & {"streamlit", "gradio", "dash"}:
            ui = STACK_CATALOG[sorted(packages & {"streamlit", "gradio", "dash"})[0]][0]
            if hub == 0:
                return "ui", f"{ui} interface"
            return ("core" if hub >= 3 else "module"), f"{ui} helpers used by {plural(hub, 'file')}"
        if quality.app_objects:
            var, kind, line = quality.app_objects[0]
            return "entry", f"Creates the {kind} (`{var}`, line {line})"
        if name == "__main__.py":
            return "entry", "Package entry point (run with `python -m`)"
        if quality.has_main_guard and hub == 0:
            return "entry", f"Runs as a program (`__main__` guard, line {quality.has_main_guard})"
        if name in CONFIG_NAMES:
            return "config", "Configuration values"
        top_functions = sum(1 for f in quality.functions if "." not in f.qualified_name)
        if (quality.model_classes and quality.model_classes * 2 >= len(quality.classes)
                and top_functions <= quality.model_classes):
            return "model", f"Defines {plural(quality.model_classes, 'data model')}"
        if name == "__init__.py":
            reexports = len(info["imports"])
            if reexports >= 3:
                return "package", f"Public API of `{PurePosixPath(path).parent.name}`"
            return "package", "Package initialiser"
        runnable = " and also runs as a script" if quality.has_main_guard else ""
        if hub >= 3:
            return "core", f"Used by {plural(hub, 'file')}{runnable}"
        if hub == 0 and quality.top_level_statements >= 2 and len(quality.functions) <= 1:
            return "script", "Script that runs its steps at top level"
        if runnable:
            return "module", f"Library module that{runnable}"
        return "module", "Module"

    # JavaScript / TypeScript
    if name.split(".")[-2:-1] == ["config"] or ".config." in name:
        return "config", "Tool configuration"
    if packages & {"express", "koa", "fastify", "@nestjs/core"} and hub == 0:
        return "api", "HTTP server"
    if name.endswith((".jsx", ".tsx")) and any(
        s["name"][:1].isupper() for s in info["symbols"] if s["kind"] == "export"
    ):
        return "component", "UI component"
    if PurePosixPath(path).stem in {"main", "index", "server", "app"} and hub == 0:
        return "entry", "Application entry point"
    if hub >= 3:
        return "core", f"Used by {plural(hub, 'file')}"
    return "module", "Module"


def _describe(path: str, info: dict, graph, tests_of: dict[str, list[str]],
              stack_names: dict[str, str]) -> str:
    quality = info.get("quality")
    parts: list[str] = []
    docstring = first_sentence(quality.module_docstring) if quality else None
    if docstring:
        parts.append(docstring)
    if info.get("role") in {"entry", "ui", "script", "build", "docs", "stub", "config"}:
        reason = info["role_reason"]
        if info["role"] in {"ui", "script"} and info["loc"]:
            reason += f" ({info['loc']:,} lines)"
        parts.append(reason + ".")

    if is_test_path(path):
        targets = [short_name(t) for t in graph.successors(path) if not is_test_path(t)]
        tests = [f for f in (quality.functions if quality else []) if
                 f.qualified_name.split(".")[-1].startswith("test")]
        if tests:
            parts.append(f"{plural(len(tests), 'test')}" +
                         (f" exercising {human_list(targets)}." if targets else "."))
        elif targets:
            parts.append(f"Exercises {human_list(targets)}.")
        return " ".join(parts) or "Test file."

    if quality is not None:
        classes = [(n, m) for n, _, _, m in quality.classes if "." not in n]
        functions = [f.qualified_name for f in quality.functions
                     if "." not in f.qualified_name]
        public = [f for f in functions if not f.startswith("_")] or functions
        what = []
        if classes:
            label = "class" if len(classes) == 1 else "classes"
            what.append(f"{label} " + human_list(
                [f"`{n}` ({plural(m, 'method')})" if m else f"`{n}`" for n, m in classes]))
        if functions:
            what.append(f"{plural(len(functions), 'function')} (" +
                        human_list([f"`{f}`" for f in public]) + ")")
        if what:
            parts.append("Defines " + " and ".join(what) + ".")
        if quality.routes:
            parts.append(f"Exposes {plural(len(quality.routes), 'route')}: " + human_list(
                [f"`{r.method} {r.path}`" for r in quality.routes], 4) + ".")
    else:
        exports = [s["name"] for s in info["symbols"] if s["kind"] == "export"]
        if exports:
            parts.append(f"Exports {human_list([f'`{e}`' for e in exports], 4)}.")

    used_by = sorted(short_name(p) for p in graph.predecessors(path) if not is_test_path(p))
    uses = sorted(short_name(p) for p in graph.successors(path))
    if used_by:
        parts.append(f"Used by {human_list(used_by)}.")
    testers = [t for t in tests_of.get(path, []) if not t.endswith("conftest.py")]
    if testers:
        parts.append(f"Tested by {human_list([short_name(t) for t in testers])}.")
    if uses:
        parts.append(f"Builds on {human_list(uses)}.")
    libraries = sorted({stack_names[p] for p in info["packages"] if p in stack_names})
    if libraries:
        parts.append(f"Uses {human_list(libraries, 4)}.")
    if not parts:
        parts.append(info.get("role_reason", "Module") + ".")
    return " ".join(parts)


# --------------------------------------------------------------------------- build


@dataclass
class _Build:
    """Everything the report stages share. Each stage reads earlier fields and fills its own."""

    snapshot_id: str
    snapshot: Any
    repo: dict
    root: Path
    progress: Progress | None
    all_files: list[dict] = field(default_factory=list)
    imports_by_file: dict[str, list[dict]] = field(default_factory=dict)
    symbols_by_file: dict[str, list[dict]] = field(default_factory=dict)
    code_rows: list[dict] = field(default_factory=list)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    lines: dict[str, list[str]] = field(default_factory=dict)
    graph_result: Any = None
    ranks: dict[str, float] = field(default_factory=dict)
    manifests: list = field(default_factory=list)
    utf16_files: list[str] = field(default_factory=list)
    readme_text: str | None = None
    readme_path: str | None = None
    has_ci: bool = False
    has_docker: bool = False
    stack: dict[str, dict] = field(default_factory=dict)
    tests_of: dict[str, list[str]] = field(default_factory=dict)  # direct test imports
    tested: set[str] = field(default_factory=set)  # reached by a test, directly or via a package
    entry_points: set[str] = field(default_factory=set)
    insights: list[dict] = field(default_factory=list)
    per_file_insights: dict[str, list[str]] = field(default_factory=dict)
    area_for: dict[str, str] = field(default_factory=dict)
    area_links: Counter = field(default_factory=Counter)
    areas: list[dict] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)
    flows: list[dict] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)

    @property
    def graph(self):
        return self.graph_result.graph

    @property
    def production(self) -> list[str]:
        return [p for p in self.files if not is_test_path(p)]

    @property
    def supporting(self) -> set[str]:
        """Example, docs and benchmark code (`examples/`, `docs_src/`): analysed and mapped,
        but not what the project is. Empty when the repository is nothing but such folders."""
        found = {p for p in self.production if support_area(p) is not None}
        return found if len(found) < len(self.production) else set()

    @property
    def core(self) -> list[str]:
        """The project's own code: what the headline, entry points and rankings describe."""
        supporting = self.supporting
        return [p for p in self.production if p not in supporting]

    @property
    def core_entry_points(self) -> set[str]:
        core = set(self.core)
        return {p for p in self.entry_points if p in core}

    @property
    def declared_library(self) -> bool:
        """The root manifest says this is a reusable library (see Manifest.library)."""
        return any(m.library and m.directory == "" for m in self.manifests)

    @property
    def all_paths(self) -> list[str]:
        return [f["relative_path"] for f in self.all_files]

    def step(self, stage: str, done: int, total: int, message: str) -> None:
        if self.progress is not None:
            self.progress(stage, done, total, message)

    def production_impact(self, path: str) -> int:
        return sum(1 for p in impact(self.graph, path) if not is_test_path(p))


STACK_NAMES = {pkg: display for pkg, (display, _) in STACK_CATALOG.items()}


def _load(b: _Build) -> None:
    with get_connection() as connection:
        b.all_files = [dict(r) for r in connection.execute(
            "SELECT relative_path, extension, size_bytes, status, skip_reason FROM files "
            "WHERE snapshot_id = ? ORDER BY relative_path", (b.snapshot_id,))]
        import_rows = connection.execute(
            "SELECT relative_path, kind, module, name, alias, level, line FROM imports "
            "WHERE snapshot_id = ? ORDER BY relative_path, line, import_id", (b.snapshot_id,))
        symbol_rows = connection.execute(
            "SELECT relative_path, kind, name, qualified_name, start_line, end_line FROM symbols "
            "WHERE snapshot_id = ? ORDER BY relative_path, start_line", (b.snapshot_id,))
        imports: dict[str, list[dict]] = defaultdict(list)
        for row in import_rows:
            imports[row["relative_path"]].append(dict(row))
        symbols: dict[str, list[dict]] = defaultdict(list)
        for row in symbol_rows:
            symbols[row["relative_path"]].append(dict(row))
    b.imports_by_file, b.symbols_by_file = imports, symbols
    b.code_rows = [f for f in b.all_files
                   if PurePosixPath(f["relative_path"]).suffix.lower() in CODE_SUFFIXES
                   and f["status"] != "skipped"]


def _measure(b: _Build) -> None:
    """Read every code file once: lines of code and, for Python, the quality facts."""
    for index, row in enumerate(b.code_rows, start=1):
        path = row["relative_path"]
        language = CODE_SUFFIXES[PurePosixPath(path).suffix.lower()]
        text = _read_text(b.root, path)
        lines = text.splitlines() if text is not None else []
        b.lines[path] = lines
        quality = None
        if language == "Python" and text is not None and row["status"] == "analyzed":
            try:
                quality = analyze_python(text, lines)
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                quality = None  # a crafted file can't stop the whole analysis
        b.files[path] = {
            "path": path, "language": language, "status": row["status"],
            "failure": row["skip_reason"] if row["status"] == "failed" else None,
            "quality": quality, "loc": quality.loc if quality else _loc(lines, language),
            "imports": b.imports_by_file.get(path, []),
            "symbols": b.symbols_by_file.get(path, []), "packages": set(),
        }
        b.step("measure", index, len(b.code_rows), path)


def _map(b: _Build) -> None:
    b.step("graph", 0, 1, "Resolving imports")
    analyzed = [f["relative_path"] for f in b.code_rows if f["status"] == "analyzed"]
    type_only_lines = {path: info["quality"].type_checking_lines
                       for path, info in b.files.items() if info.get("quality")}
    deferred_lines = {path: info["quality"].deferred_lines
                      for path, info in b.files.items() if info.get("quality")}
    b.graph_result = build_graph(analyzed, b.imports_by_file, type_only_lines,
                                 js=_js_resolver(b, analyzed), deferred_lines=deferred_lines)
    for row in b.code_rows:
        if row["status"] == "failed":
            b.graph.add_node(row["relative_path"])
    for package, use in b.graph_result.externals.items():
        for path in use.files:
            b.files[path]["packages"].add(package)
    b.ranks = importance(b.graph)
    tests_of: dict[str, list[str]] = defaultdict(list)
    for edge in b.graph_result.edges.values():
        if edge.kind == "tests" and not edge.type_only:
            tests_of[edge.target].append(edge.source)
    b.tests_of = tests_of
    b.tested = _reached_through_packages(b.graph, set(tests_of))
    b.step("graph", 1, 1, f"{len(b.graph_result.edges)} connections")


def _js_resolver(b: _Build, analyzed: list[str]) -> JsResolver:
    """A JS/TS resolver that knows the project's tsconfig `paths` and its package names."""
    aliases = []
    manifests: dict[str, str] = {}
    for row in b.all_files:
        path = row["relative_path"]
        name = PurePosixPath(path).name
        if row["status"] == "skipped" or name not in {"tsconfig.json", "jsconfig.json",
                                                       "package.json"}:
            continue
        text = _read_text(b.root, path)
        if text is None:
            continue
        if name == "package.json":
            manifests[path] = text
        else:
            aliases.extend(js_aliases(path, text))
    js_files = {p for p in analyzed if not p.endswith((".py", ".pyi"))}
    return JsResolver(js_files, aliases, package_names(manifests))


def _is_package_entry(path: str) -> bool:
    name = PurePosixPath(path)
    return name.name == "__init__.py" or (name.stem == "index" and name.suffix in CODE_SUFFIXES)


def _reached_through_packages(graph, direct: set[str]) -> set[str]:
    """Files a test reaches: the ones it imports, plus everything a package entry file
    (`__init__.py`, `index.ts`) it imports re-exports. `import click` in a test exercises
    `click/core.py` even though no test names that file."""
    reached = set(direct)
    frontier = [p for p in direct if _is_package_entry(p)]
    while frontier:
        entry = frontier.pop()
        for target in graph.successors(entry):
            if target in reached or is_test_path(target):
                continue
            reached.add(target)
            if _is_package_entry(target):
                frontier.append(target)
    return reached


def _is_manifest(name: str) -> bool:
    return name in MANIFEST_NAMES or (name.lower().endswith(".txt")
                                      and "requirement" in name.lower())


def _project_files(b: _Build) -> None:
    """Dependency manifests, the README, CI and Docker."""
    for row in b.all_files:
        path = row["relative_path"]
        name = PurePosixPath(path).name
        if row["status"] == "skipped":
            continue
        if _is_manifest(name):
            raw = _read_bytes(b.root, path)
            text = decode_text(raw) if raw is not None else None
            if raw is not None and detect_encoding(raw) == "utf-16":
                b.utf16_files.append(path)
            manifest = parse_manifest(path, text) if text is not None else None
            if manifest is not None:
                b.manifests.append(manifest)
                b.lines.setdefault(path, text.splitlines())
        is_readme = name.lower() in {"readme.md", "readme.rst", "readme.txt", "readme"}
        shallower = b.readme_path is None or (
            len(PurePosixPath(path).parts) < len(PurePosixPath(b.readme_path).parts))
        if is_readme and shallower:
            text = _read_text(b.root, path)
            if text:
                b.readme_text, b.readme_path = text, path
    paths = b.all_paths
    b.has_ci = any(p.startswith(CI_MARKERS) or PurePosixPath(p).name in CI_MARKERS
                   for p in paths)
    b.has_docker = any(PurePosixPath(p).name.lower() in DOCKER_NAMES
                       or p.lower().endswith(".dockerfile") for p in paths)


def _stack(b: _Build) -> None:
    stack = b.stack
    for package, use in b.graph_result.externals.items():
        if package in STACK_CATALOG and (use.kind == "external" or package in STDLIB_STACK):
            display, category = STACK_CATALOG[package]
            entry = stack.setdefault(display, {"name": display, "category": category,
                                               "files": 0, "evidence": sorted(use.files)[0]})
            entry["files"] += len(use.files)
    # Declared-but-not-imported packages only count when the manifest is hand-written (a full
    # `pip freeze` lists every transitive package) and they're tools you run, not import.
    hand_written = [m for m in b.manifests if not m.is_freeze()]
    declared = {normalize_dist(r.name) for m in hand_written for r in m.requirements}
    for package in sorted(RUN_NOT_IMPORT):
        display, category = STACK_CATALOG[package]
        if display not in stack and normalize_dist(package) in declared:
            manifest = next(m for m in hand_written if normalize_dist(package) in m.names())
            stack[display] = {"name": display, "category": category, "files": 0,
                              "evidence": manifest.path}
    if b.has_docker:
        stack["Docker"] = {"name": "Docker", "category": "Infrastructure", "files": 0,
                           "evidence": next(p for p in b.all_paths if "docker" in p.lower())}
    if b.has_ci:
        ci_file = next(p for p in b.all_paths if p.startswith(CI_MARKERS)
                       or PurePosixPath(p).name in CI_MARKERS)
        label = "GitHub Actions" if ci_file.startswith(".github/") else "CI"
        stack[label] = {"name": label, "category": "Infrastructure", "files": 0,
                        "evidence": ci_file}


def _roles(b: _Build) -> None:
    for path, info in b.files.items():
        info["role"], info["role_reason"] = _classify(path, info, b.graph)
    b.entry_points = {p for p, i in b.files.items()
                      if i["role"] in ENTRY_ROLES and not is_test_path(p)}
    for path, info in b.files.items():
        info["description"] = _describe(path, info, b.graph, b.tests_of, STACK_NAMES)


def _insights(b: _Build) -> None:
    b.step("insights", 0, 1, "Checking for problems")
    context = insight_rules.InsightContext(
        files=b.files, graph=b.graph_result, manifests=b.manifests,
        has_tests=any(is_test_path(p) for p in b.files), has_ci=b.has_ci,
        entry_points=b.entry_points, lines=b.lines, utf16_files=b.utf16_files,
        tested=b.tested, supporting=b.supporting,
    )
    per_file: dict[str, list[str]] = defaultdict(list)
    for number, insight in enumerate(insight_rules.generate(context), start=1):
        insight_id = f"I{number:03d}"
        for path in insight.files:
            per_file[path].append(insight_id)
        redact = insight.rule == "hardcoded_secret"
        b.insights.append({
            "id": insight_id, "rule": insight.rule, "category": insight.category,
            "severity": insight.severity, "title": insight.title, "summary": insight.summary,
            "why": insight.why, "suggestion": insight.suggestion,
            "confidence": insight.confidence, "score": insight.score, "files": insight.files,
            "evidence": [
                {"path": e.path, "start": e.start, "end": e.end, "label": e.label,
                 "snippet": _snippet(b.lines.get(e.path), e.start, e.end, redact=redact)}
                for e in insight.evidence
            ],
        })
    b.per_file_insights = per_file
    b.step("insights", 1, 1, f"{len(b.insights)} insights")


def _area_description(b: _Build, members: list[str], key: str) -> str:
    roles = Counter(b.files[p]["role"] for p in members)
    loc = sum(b.files[p]["loc"] for p in members)
    libraries = sorted({STACK_NAMES[pkg] for p in members for pkg in b.files[p]["packages"]
                        if pkg in STACK_NAMES})
    role_text = human_list([plural(n, *ROLE_NOUNS.get(r, (r, r + "s")))
                            for r, n in roles.most_common()], 3)
    text = (f"{plural(len(members), 'file')}, {loc:,} lines of code: {role_text}. "
            f"Centre of gravity: {short_name(key)}.")
    if libraries:
        text += f" Uses {human_list(libraries, 4)}."
    return text


def _areas(b: _Build) -> None:
    depth = choose_area_depth(b.core or list(b.files))
    supporting = b.supporting
    # Each example/docs folder is one area, however many topics it holds (fastapi's
    # docs_src/* would otherwise be 80 areas around a handful of real ones).
    b.area_for = {p: ("tests" if is_test_path(p)
                      else support_area(p) if p in supporting else area_of(p, depth))
                  for p in b.files}
    support_areas = {b.area_for[p] for p in supporting}
    members_of: dict[str, list[str]] = defaultdict(list)
    for path, area in b.area_for.items():
        members_of[area].append(path)
    for (source, target), edge in b.graph_result.edges.items():
        if edge.type_only:
            continue
        a, c = b.area_for.get(source), b.area_for.get(target)
        if a and c and a != c:
            b.area_links[(a, c)] += 1
    # The project's own areas first, then examples and docs, then tests.
    ordered = sorted(members_of.items(), key=lambda kv: (
        kv[0] == "tests", kv[0] in support_areas, -len(kv[1]), kv[0]))
    for area, members in ordered:
        key = max(members, key=lambda p: (b.ranks.get(p, 0), b.files[p]["loc"]))
        b.areas.append({
            "name": area, "files": sorted(members), "supporting": area in support_areas,
            "loc": sum(b.files[p]["loc"] for p in members),
            "roles": dict(Counter(b.files[p]["role"] for p in members)),
            "key_file": key, "description": _area_description(b, members, key),
            "depends_on": [{"area": c, "edges": n} for (a, c), n in b.area_links.items()
                           if a == area],
            "insights": sum(len(b.per_file_insights.get(p, [])) for p in members),
        })


def _flow_steps(b: _Build, entry: str) -> tuple[list[dict], int]:
    """Breadth-first walk of what an entry point imports, three levels deep."""
    steps = [{"path": entry, "depth": 0, "parent": None, "line": None}]
    seen = {entry}
    frontier = [entry]
    for level in range(1, 4):
        following = []
        for node in frontier:
            for target in sorted(b.graph.successors(node), key=lambda t: -b.ranks.get(t, 0)):
                if target in seen or is_test_path(target):
                    continue
                seen.add(target)
                edge = b.graph_result.edges.get((node, target))
                steps.append({"path": target, "depth": level, "parent": node,
                              "line": edge.lines[0] if edge else None})
                following.append(target)
        frontier = following
        if len(steps) >= 30:
            break
    return steps[:30], len(seen) - 1


def _flows(b: _Build) -> None:
    graph = b.graph
    # A library is entered through its public API, not its release or docs scripts.
    b.entries = [] if b.declared_library else sorted(
        b.core_entry_points, key=lambda p: (-len(nx.descendants(graph, p)), p))
    if not b.entries:
        b.entries = sorted(
            [p for p in b.core if _is_package_entry(p) and graph.out_degree(p) >= 2],
            key=lambda p: (p.count("/"), -graph.out_degree(p), p),
        )[:3]
    for entry in b.entries[:8]:
        steps, reach = _flow_steps(b, entry)
        info = b.files.get(entry, {})
        b.flows.append({
            "entry": entry,
            "kind": ROLE_LABELS.get(info.get("role", "module"), "Module")
            if entry in b.entry_points else "Public API",
            "reason": info.get("role_reason", ""), "steps": steps, "reach": reach,
        })
    supporting = b.supporting
    for path, info in b.files.items():
        quality = info.get("quality")
        for route in (quality.routes if quality else []):
            b.routes.append({"method": route.method, "path": route.path,
                             "handler": route.handler, "file": path, "line": route.line,
                             "supporting": path in supporting})
    b.routes.sort(key=lambda r: r["supporting"])  # the project's own routes before tutorials'


def _coverage(b: _Build) -> dict:
    statuses = Counter(f["status"] for f in b.all_files)
    skip_reasons = Counter(f["skip_reason"] or "Not analyzed"
                           for f in b.all_files if f["status"] == "skipped")
    result = b.graph_result
    unresolved = len(result.unresolved)
    resolved = result.resolved_internal
    return {
        "discovered": len(b.all_files),
        "analyzed": statuses.get("analyzed", 0),
        "indexed": statuses.get("indexed", 0),
        "skipped": statuses.get("skipped", 0),
        "failed": statuses.get("failed", 0),
        "skipped_by_reason": [{"reason": r, "files": n} for r, n in skip_reasons.most_common()],
        "skipped_files": [{"path": f["relative_path"], "reason": f["skip_reason"]}
                          for f in b.all_files if f["status"] == "skipped"],
        "failed_files": [{"path": f["relative_path"], "reason": f["skip_reason"]}
                         for f in b.all_files if f["status"] == "failed"],
        # Text that isn't analysed code (READMEs, docs, config): browsable and searchable.
        "indexed_files": sorted(f["relative_path"] for f in b.all_files
                                if f["status"] == "indexed"),
        "imports": {
            "internal_resolved": resolved,
            "internal_unresolved": unresolved,
            "external": sum(len(u.files) for u in result.externals.values()),
            "resolution_rate": round(resolved / (resolved + unresolved), 3)
            if resolved + unresolved else 1.0,
            "unresolved": [{"path": p, "module": m, "line": n}
                           for p, m, n in result.unresolved[:50]],
        },
        "confirmed_edges": sum(e.tier == "confirmed" for e in result.edges.values()),
        "inferred_edges": sum(e.tier == "inferred" for e in result.edges.values()),
    }


def _kind(b: _Build) -> tuple[str, list[str]]:
    """What sort of project this is, plus the extra surfaces worth naming."""
    # Judged on the project's own code: a library's React demo doesn't make it an app.
    own = [b.files[p] for p in b.core]
    core = set(b.core)
    routes = [r for r in b.routes if r["file"] in core]
    packages = {p for info in own for p in info["packages"]}
    ui_kinds = sorted({info["role_reason"].split(" ")[0] for info in own
                       if info["role"] == "ui"})
    if any(info["role"] == "component" for info in own):
        ui_kinds.append("React" if "react" in packages else "web")
    if b.declared_library:
        kind = "library"  # the root manifest says so, whatever its examples look like
        ui_kinds = []
    elif packages & ML_PACKAGES:
        kind = "machine-learning project"
    elif routes:
        kind = "web service"
    elif ui_kinds:
        kind = "application"
    elif any(m.kind == "pyproject" for m in b.manifests) and not b.core_entry_points:
        kind = "library"
    elif b.core_entry_points:
        kind = "application"
    else:
        kind = "project"
    extras = []
    if routes and kind not in {"web service", "library"}:
        extras.append(f"an HTTP API ({plural(len(routes), 'route')})")
    if ui_kinds:
        extras.append(f"a {human_list(ui_kinds)} interface")
    return kind, extras


def _headline(b: _Build, kind: str, extras: list[str]) -> str:
    core = b.core
    name = b.repo.get("name") or b.snapshot_id
    core_areas = len([a for a in b.areas if a["name"] != "tests" and not a["supporting"]])
    supporting = len(b.supporting)
    if not b.files:
        return (f"{name} has no code EvidenceLoop can read: "
                f"{plural(len(b.all_files), 'file')}, none in Python, JavaScript or TypeScript.")
    return (f"{name} is a {_main_language(b)} {kind}"
            + (f" with {human_list(extras)}" if extras else "")
            + f": {plural(len(core), 'source file')}, "
            f"{sum(b.files[p]['loc'] for p in core):,} lines of code"
            + (f" in {plural(core_areas, 'area')}" if core_areas > 1 else "")
            + (f", plus {plural(supporting, 'example or docs file')}" if supporting else "")
            + ".")


def _main_language(b: _Build) -> str:
    languages = Counter(b.files[p]["language"] for p in b.core if b.files[p]["language"])
    languages = languages or Counter(info["language"] for info in b.files.values())
    return languages.most_common(1)[0][0] if languages else "Code"


def _facts(b: _Build, top: list[str]) -> list[str]:
    graph = b.graph
    facts = []
    if top and fan_in(graph, top[0]) >= 2:
        facts.append(f"{short_name(top[0])} is the most depended-on file "
                     f"(used by {plural(fan_in(graph, top[0]), 'file')}).")
    if b.core_entry_points and not b.declared_library:
        facts.append(
            f"It starts from {human_list(distinct_names(sorted(b.core_entry_points)))}.")
    elif b.entries:
        facts.append(f"Its public API is {human_list(distinct_names(b.entries))}.")
    test_files = [p for p in b.files if is_test_path(p)]
    if test_files:
        testable = [
            p for p in b.core
            if b.files[p]["role"] not in {"docs", "config", "build", "stub", "script"}
            and any(s["kind"] != "module" for s in b.files[p]["symbols"])
        ]
        reached = len(b.tested & set(testable))
        direct = len(set(b.tests_of) & set(testable))
        via = f" ({direct} directly, the rest through the package)" if reached > direct else ""
        tests = plural(len(test_files), "test file")
        if testable:
            facts.append(f"{tests} reach{'es' if len(test_files) == 1 else ''} "
                         f"{reached} of {plural(len(testable), 'module')}{via}.")
        else:
            facts.append(f"There {'is' if len(test_files) == 1 else 'are'} {tests}, but no "
                         "module with functions or classes for them to test.")
    else:
        facts.append("There are no automated tests.")
    severities = Counter(i["severity"] for i in b.insights)
    if b.insights:
        facts.append(f"EvidenceLoop found {plural(len(b.insights), 'issue')}"
                     + (f", {severities['high']} high severity" if severities["high"] else "")
                     + ".")
    return facts


def _summary(b: _Build, externals: list[dict]) -> dict:
    kind, extras = _kind(b)
    top = sorted(b.core, key=lambda p: -b.ranks.get(p, 0))
    languages = Counter(info["language"] for info in b.files.values())
    language_loc: Counter[str] = Counter()
    for info in b.files.values():
        language_loc[info["language"]] += info["loc"]
    key_files = []
    for path in top[:6]:
        info = b.files[path]
        reach = b.production_impact(path)
        key_files.append({
            "path": path, "role": info["role"], "fan_in": fan_in(b.graph, path),
            "impact": reach,
            "why": f"{info['role_reason']}; {plural(reach, 'file')} "
                   f"depend{'s' if reach == 1 else ''} on it directly or indirectly",
        })
    qualities = [i["quality"] for i in b.files.values() if i.get("quality")]
    return {
        "headline": _headline(b, kind, extras), "facts": _facts(b, top), "kind": kind,
        "readme": readme_summary(b.readme_text) if b.readme_text else None,
        "readme_path": b.readme_path,
        "languages": [{"name": n, "files": languages[n], "loc": language_loc[n]}
                      for n, _ in languages.most_common()],
        "stack": sorted(b.stack.values(), key=lambda s: (
            STACK_ORDER.index(s["category"]) if s["category"] in STACK_ORDER else 99,
            -s["files"], s["name"])),
        "totals": {
            "files": len(b.all_files), "source_files": len(b.core),
            "supporting_files": len(b.supporting),
            "test_files": sum(1 for p in b.files if is_test_path(p)),
            "loc": sum(i["loc"] for i in b.files.values()),
            "source_loc": sum(b.files[p]["loc"] for p in b.core),
            "functions": sum(len(q.functions) for q in qualities),
            "classes": sum(len(q.classes) for q in qualities),
            "connections": len(b.graph_result.edges),
            "packages": sum(1 for e in externals
                            if e["kind"] == "external" and e["scope"] == "runtime"),
            "dev_packages": sum(1 for e in externals
                                if e["kind"] == "external" and e["scope"] == "development"),
            "insights": len(b.insights),
            "by_severity": dict(Counter(i["severity"] for i in b.insights)),
        },
        "key_files": key_files,
    }


def _worst_severity(b: _Build) -> dict[str, str]:
    worst: dict[str, str] = {}
    for insight in b.insights:
        for path in insight["files"]:
            current = worst.get(path)
            if current is None or SEVERITY_RANK[insight["severity"]] > SEVERITY_RANK[current]:
                worst[path] = insight["severity"]
    return worst


def _files_json(b: _Build) -> list[dict]:
    graph = b.graph
    cycle_members = {p for cycle in b.graph_result.cycles for p in cycle}
    worst = _worst_severity(b)
    out = []
    for path in sorted(b.files):
        info = b.files[path]
        quality = info.get("quality")
        functions = quality.functions if quality else []
        out.append({
            "path": path, "name": short_name(path), "language": info["language"],
            "status": info["status"], "failure": info["failure"], "area": b.area_for[path],
            "role": info["role"], "role_label": ROLE_LABELS[info["role"]],
            "role_reason": info["role_reason"], "description": info["description"],
            "docstring": quality.module_docstring if quality else None,
            "loc": info["loc"], "functions": len(functions),
            "classes": len(quality.classes) if quality else 0,
            "max_complexity": max((f.complexity for f in functions), default=0),
            "fan_in": fan_in(graph, path), "fan_out": graph.out_degree(path),
            "impact": b.production_impact(path), "importance": round(b.ranks.get(path, 0), 4),
            "is_test": is_test_path(path), "is_entry": path in b.entry_points,
            "in_cycle": path in cycle_members, "tested_by": sorted(b.tests_of.get(path, [])),
            "used_by": sorted(graph.predecessors(path)), "uses": sorted(graph.successors(path)),
            "packages": sorted(info["packages"]),
            "insights": b.per_file_insights.get(path, []),
            "severity": worst.get(path),
            "function_metrics": [
                {"name": f.qualified_name, "start": f.start_line, "end": f.end_line,
                 "complexity": f.complexity, "lines": f.lines, "nesting": f.max_nesting}
                for f in functions
            ],
        })
    return out


def _graph_json(b: _Build, files_json: list[dict]) -> dict:
    node_keys = ("area", "role", "loc", "fan_in", "fan_out", "importance", "in_cycle", "status",
                 "is_test", "is_entry", "severity")
    return {
        "nodes": [{"id": f["path"], "name": f["name"], **{k: f[k] for k in node_keys},
                   "insights": len(f["insights"])} for f in files_json],
        "edges": [{"source": e.source, "target": e.target, "tier": e.tier, "kind": e.kind,
                   "type_only": e.type_only, "lines": e.lines}
                  for e in b.graph_result.edges.values()],
        "area_edges": [{"source": a, "target": c, "edges": n}
                       for (a, c), n in sorted(b.area_links.items())],
    }


def build_report(snapshot_id: str, progress: Progress | None = None) -> dict:
    """Run every stage over a parsed snapshot and assemble the report document."""
    started = time.perf_counter()
    initialize_database()
    snapshot = get_snapshot(snapshot_id)
    if snapshot is None:
        raise ReportError(f"Unknown snapshot: {snapshot_id}")
    with get_connection() as connection:
        repo = dict(connection.execute(
            "SELECT * FROM repositories WHERE repository_id = ?", (snapshot["repository_id"],)
        ).fetchone())
    b = _Build(snapshot_id, snapshot, repo, resolve_root(snapshot), progress)

    for stage in (_load, _measure, _map, _project_files, _stack, _roles, _insights, _areas,
                  _flows):
        stage(b)

    # A package is a runtime dependency only if production code imports it for real; one used
    # only by tests, docs or examples, or only for type hints, is for development.
    # The project's own code that runs when it's used: no tests, examples, docs, or build
    # scripts like setup.py (which import setuptools but aren't part of the package at runtime).
    production = {p for p in b.core if b.files[p]["role"] not in {"build", "config", "docs"}}
    externals = sorted(
        ({"package": u.package, "kind": u.kind, "files": len(u.files),
          "scope": "runtime" if u.runtime_files & production else "development",
          "display": STACK_CATALOG.get(u.package, (u.package, ""))[0]}
         for u in b.graph_result.externals.values()),
        key=lambda e: (e["kind"] != "external", e["scope"] != "runtime", -e["files"],
                       e["package"]),
    )
    files_json = _files_json(b)
    return {
        "version": REPORT_VERSION,
        "snapshot": {
            "id": snapshot_id, "commit": snapshot["commit_sha"],
            "created_at": snapshot["created_at"],
            "repo": {"name": repo.get("name") or snapshot_id, "owner": repo.get("owner"),
                     "url": repo.get("source_url"), "source": repo.get("source_type")},
        },
        "summary": _summary(b, externals),
        "coverage": _coverage(b),
        "architecture": {"areas": b.areas, "flows": b.flows, "routes": b.routes,
                         "cycles": b.graph_result.cycles},
        "files": files_json,
        "graph": _graph_json(b, files_json),
        "insights": b.insights,
        "dependencies": {
            "external": externals,
            "manifests": [{"path": m.path, "kind": m.kind,
                           "requirements": [{"name": r.name, "spec": r.spec, "line": r.line,
                                             "pinned": r.pinned} for r in m.requirements]}
                          for m in b.manifests],
        },
        "seconds": round(time.perf_counter() - started, 2),
    }


# --------------------------------------------------------------------------- storage


def save_report(snapshot_id: str, report: dict) -> None:
    initialize_database()
    with get_connection() as connection:
        connection.execute("DELETE FROM analysis_reports WHERE snapshot_id = ?", (snapshot_id,))
        connection.execute(
            "INSERT INTO analysis_reports (snapshot_id, version, report) VALUES (?, ?, ?)",
            (snapshot_id, report["version"], json.dumps(report)),
        )
        connection.execute("DELETE FROM graph_edges WHERE snapshot_id = ?", (snapshot_id,))
        connection.execute("DELETE FROM graph_nodes WHERE snapshot_id = ?", (snapshot_id,))
        connection.executemany(
            "INSERT INTO graph_nodes (node_id, snapshot_id, node_type, name, file_path, "
            "start_line, end_line) VALUES (?, ?, 'file', ?, ?, NULL, NULL)",
            [(f"{snapshot_id}::{n['id']}", snapshot_id, n["name"], n["id"])
             for n in report["graph"]["nodes"]],
        )
        connection.executemany(
            "INSERT INTO graph_edges (snapshot_id, source_node_id, target_node_id, relationship, "
            "source_path, start_line, end_line, extraction_method, evidence_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'import-resolution', ?)",
            [(snapshot_id, f"{snapshot_id}::{e['source']}", f"{snapshot_id}::{e['target']}",
              e["kind"], e["source"], min(e["lines"]), max(e["lines"]), e["tier"])
             for e in report["graph"]["edges"]],
        )


def load_report(snapshot_id: str) -> dict | None:
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT version, report FROM analysis_reports WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
    if row is None or row["version"] != REPORT_VERSION:
        return None
    return json.loads(row["report"])


def run_full_analysis(snapshot_id: str, progress: Progress | None = None) -> dict:
    """Facts (per-file parse) followed by the report. Returns the report."""
    from analysis.pipeline import analyze_snapshot

    def facts_progress(done: int, total: int, path: str) -> None:
        if progress is not None:
            progress("parse", done, total, path)

    analyze_snapshot(snapshot_id, facts_progress)
    report = build_report(snapshot_id, progress)
    save_report(snapshot_id, report)
    return report
