"""Deterministic insights: real problems found by rules, each with exact evidence lines.

Every insight states its confidence honestly:
- confirmed: the rule matched the code exactly (e.g. a mutable default argument).
- heuristic: a strong signal, but a human should confirm (e.g. "nothing imports this file").
Insights in files that many others depend on rank higher, because problems there spread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from analysis.graph.builder import GraphResult, fan_in, is_test_path
from analysis.understanding.profile import Manifest, dist_matches, plural, short_name

SEVERITY_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}

COMPLEXITY_HIGH = 15
COMPLEXITY_MEDIUM = 10
LONG_FUNCTION = 60
VERY_LONG_FUNCTION = 100
LARGE_MODULE_LOC = 500
GOD_CLASS_METHODS = 20
HUB_FAN_IN = 3
DEEP_NESTING = 5


@dataclass
class Evidence:
    path: str
    start: int
    end: int
    label: str = ""


@dataclass
class Insight:
    rule: str
    # Architecture | Reliability | Maintainability | Testing | Security | Dependencies
    category: str
    severity: str  # high | medium | low
    title: str
    summary: str
    why: str
    suggestion: str
    confidence: str  # confirmed | heuristic
    evidence: list[Evidence] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class InsightContext:
    files: dict[str, dict[str, Any]]  # path -> file info (see report.py)
    graph: GraphResult
    manifests: list[Manifest]
    has_tests: bool
    has_ci: bool
    entry_points: set[str]
    lines: dict[str, list[str]]
    utf16_files: list[str] = field(default_factory=list)


def _rank(insight: Insight, ctx: InsightContext) -> None:
    reach = max((fan_in(ctx.graph.graph, f) for f in insight.files if f in ctx.graph.graph),
                default=0)
    insight.score = round(SEVERITY_WEIGHT[insight.severity] * (1 + 0.35 * reach), 2)


def _cycles(ctx: InsightContext) -> list[Insight]:
    out = []
    for cycle in ctx.graph.cycles:
        members = set(cycle)
        evidence = []
        for (source, target), edge in sorted(ctx.graph.edges.items()):
            if source in members and target in members:
                line = edge.lines[0]
                evidence.append(Evidence(source, line, line,
                                         f"{short_name(source)} imports {short_name(target)}"))
        names = [short_name(p) for p in cycle]
        out.append(Insight(
            rule="import_cycle", category="Architecture",
            severity="high" if len(cycle) > 2 else "medium",
            title=f"Import cycle between {len(cycle)} files",
            summary=f"{', '.join(names)} import each other in a loop.",
            why="Circular imports make load order fragile (partially initialised modules) and "
                "mean none of these files can be understood, tested or changed alone.",
            suggestion="Move the shared pieces into a module both can import, or invert one "
                       "dependency (pass the object in instead of importing it).",
            confidence="confirmed", evidence=evidence[:8], files=cycle,
        ))
    return out


def _functions(ctx: InsightContext) -> list[Insight]:
    out = []
    for path, info in ctx.files.items():
        quality = info.get("quality")
        if quality is None or is_test_path(path):
            continue
        for fn in quality.functions:
            complex_ = fn.complexity >= COMPLEXITY_MEDIUM
            long_ = fn.lines >= LONG_FUNCTION
            deep = fn.max_nesting >= DEEP_NESTING
            if not (complex_ or long_ or deep):
                continue
            severity = "low"
            if fn.complexity >= COMPLEXITY_HIGH or fn.lines >= VERY_LONG_FUNCTION:
                severity = "high" if fn.complexity >= COMPLEXITY_HIGH and long_ else "medium"
            elif complex_ or (long_ and deep):
                severity = "medium"
            facts = []
            if complex_:
                facts.append(f"{fn.complexity} independent paths")
            if long_:
                facts.append(f"{fn.lines} lines")
            if deep:
                facts.append(f"nesting {fn.max_nesting} levels deep")
            out.append(Insight(
                rule="complex_function", category="Maintainability", severity=severity,
                title=f"`{fn.qualified_name}` is hard to follow",
                summary=f"{fn.qualified_name}() has {', '.join(facts)}.",
                why="Each independent path is a case to understand and test. Functions this "
                    "size are where bugs hide and where changes most often break something.",
                suggestion="Split it into smaller named steps (extract the branches into helper "
                           "functions) and add a test for each path you keep.",
                confidence="confirmed",
                evidence=[Evidence(path, fn.start_line, fn.end_line, f"{fn.qualified_name}()")],
                files=[path],
            ))
    return out


def _modules(ctx: InsightContext) -> list[Insight]:
    out = []
    for path, info in ctx.files.items():
        quality = info.get("quality")
        if is_test_path(path):
            continue
        loc = info.get("loc", 0)
        if loc >= LARGE_MODULE_LOC:
            out.append(Insight(
                rule="large_module", category="Maintainability",
                severity="medium" if loc >= 2 * LARGE_MODULE_LOC else "low",
                title=f"{short_name(path)} is large ({loc} lines of code)",
                summary=f"{path} holds {loc} lines of code in one file.",
                why="Large files usually mix several responsibilities, which makes them hard to "
                    "navigate, review and test in isolation.",
                suggestion="Group related functions and classes and move each group into its own "
                           "module behind the same public names.",
                confidence="confirmed", evidence=[Evidence(path, 1, 1, path)], files=[path],
            ))
        if quality is None:
            continue
        for name, start, _end, methods in quality.classes:
            if methods >= GOD_CLASS_METHODS:
                out.append(Insight(
                    rule="god_class", category="Maintainability", severity="medium",
                    title=f"Class `{name}` does a lot ({methods} methods)",
                    summary=f"{name} in {short_name(path)} defines {methods} methods.",
                    why="A class with this many methods usually owns several jobs, so changes to "
                        "one job risk the others.",
                    suggestion="Identify the separate jobs and split them into collaborating "
                               "classes or plain functions.",
                    confidence="heuristic", evidence=[Evidence(path, start, start, name)],
                    files=[path],
                ))
    return out


SMELL_TEXT = {
    "mutable_default": (
        "Reliability", "medium", "Mutable default argument",
        "The default list/dict is created once and shared by every call, so values leak "
        "between calls.",
        "Default to None and create the list or dict inside the function.",
    ),
    "swallowed_exception": (
        "Reliability", "medium", "Errors are silently swallowed",
        "Catching every exception and doing nothing hides real failures: bugs inside the try "
        "block (a typo, a None) are silenced along with the error you meant to ignore.",
        "Catch only the exception you expect (e.g. `except (ValueError, binascii.Error):`). If "
        "ignoring it is deliberate, say why in a comment.",
    ),
    "bare_except": (
        "Reliability", "medium", "Bare `except:`",
        "A bare except also catches KeyboardInterrupt and SystemExit, so the program can't be "
        "stopped cleanly and unrelated bugs are masked.",
        "Name the exceptions you handle, e.g. `except (ValueError, KeyError):`.",
    ),
    "wildcard_import": (
        "Maintainability", "low", "Wildcard import",
        "`import *` hides where names come from and can silently overwrite local names.",
        "Import the names you use explicitly.",
    ),
    "hardcoded_secret": (
        "Security", "high", "Possible hard-coded credential",
        "Credentials in source code end up in every clone and in git history, where they are "
        "easy to leak and hard to rotate.",
        "Read it from an environment variable or a secrets manager, and rotate the value if it "
        "was real.",
    ),
}


def _smells(ctx: InsightContext) -> list[Insight]:
    grouped: dict[tuple[str, str], list] = {}
    for path, info in ctx.files.items():
        quality = info.get("quality")
        if quality is None:
            continue
        for smell in quality.smells:
            if smell.rule == "hardcoded_secret" and is_test_path(path):
                continue
            grouped.setdefault((smell.rule, path), []).append(smell)
    out = []
    for (rule, path), smells in sorted(grouped.items()):
        category, severity, title, why, suggestion = SMELL_TEXT[rule]
        count = len(smells)
        out.append(Insight(
            rule=rule, category=category, severity=severity,
            title=f"{title} in {short_name(path)}" + (f" ({count}×)" if count > 1 else ""),
            summary="; ".join(dict.fromkeys(s.detail for s in smells[:3]))
            + ("…" if count > 3 else "."),
            why=why, suggestion=suggestion,
            confidence="heuristic" if rule == "hardcoded_secret" else "confirmed",
            evidence=[Evidence(path, s.line, s.end_line, s.detail) for s in smells[:6]],
            files=[path],
        ))
    return out


def _testing(ctx: InsightContext) -> list[Insight]:
    out = []
    source = [p for p in ctx.files if not is_test_path(p) and ctx.files[p]["language"]]
    if not ctx.has_tests:
        if len(source) >= 3:
            out.append(Insight(
                rule="no_tests", category="Testing", severity="high",
                title="No automated tests found",
                summary=f"None of the {len(source)} source files has a test file.",
                why="Without tests, every change has to be checked by hand, and regressions reach "
                    "users before anyone notices.",
                suggestion="Start with tests for the most-used modules and the main entry points "
                           "(a handful of pytest cases is enough to begin).",
                confidence="confirmed", files=[],
            ))
        return out
    tested = {edge.target for edge in ctx.graph.edges.values() if edge.kind == "tests"}
    graph = ctx.graph.graph
    for path in source:
        if path.endswith(("__init__.py", ".d.ts")):
            continue
        hub = fan_in(graph, path)
        if hub >= HUB_FAN_IN and path not in tested:
            dependents = [short_name(p) for p in graph.predecessors(path) if not is_test_path(p)]
            out.append(Insight(
                rule="untested_hub", category="Testing", severity="medium",
                title=f"{short_name(path)} is widely used but no test imports it",
                summary=f"{plural(hub, 'file')} depend on {path} "
                        f"({', '.join(dependents[:4])}), and no test file imports it directly.",
                why="Changes here ripple into every file that depends on it; without a direct "
                    "test, those regressions surface somewhere else.",
                suggestion=f"Add a focused test module for {short_name(path)} covering its public "
                           "functions.",
                confidence="heuristic", evidence=[Evidence(path, 1, 1, path)], files=[path],
            ))
    return out


def _dependencies(ctx: InsightContext) -> list[Insight]:
    out = []
    for manifest in ctx.manifests:
        if manifest.kind != "requirements":
            continue
        loose = [r for r in manifest.requirements if not r.pinned]
        if loose and len(loose) >= max(1, len(manifest.requirements) // 2):
            out.append(Insight(
                rule="unpinned_dependencies", category="Dependencies",
                severity="medium" if len(loose) >= 3 else "low",
                title=f"{plural(len(loose), 'dependency', 'dependencies')} not pinned in "
                      f"{short_name(manifest.path)}",
                summary=f"{', '.join(r.name for r in loose[:5])}"
                        + (" …" if len(loose) > 5 else "") + " have no exact version.",
                why="Unpinned requirements install whatever is newest, so the same code can break "
                    "on a fresh install months later without any change.",
                suggestion="Pin exact versions (`pip freeze` or a lock file such as uv.lock / "
                           "poetry.lock) and upgrade deliberately.",
                confidence="confirmed",
                evidence=[Evidence(manifest.path, r.line, r.line, r.name) for r in loose[:6]],
                files=[manifest.path],
            ))

    if not ctx.manifests:
        return out
    by_package: dict[str, list[tuple[str, int]]] = {}
    for use in ctx.graph.externals.values():
        if use.kind != "external":
            continue
        for path in sorted(use.files):
            name = PurePosixPath(path).name
            if is_test_path(path) or name in {"setup.py", "noxfile.py", "conf.py", "bench.py"}:
                continue
            quality = ctx.files.get(path, {}).get("quality")
            if quality is not None and all(
                line in quality.type_checking_lines for line in use.lines.get(path, [])
            ):
                continue  # only imported for type hints, never at run time
            scope = [m for m in ctx.manifests if path.startswith(m.directory + "/")
                     or m.directory == ""]
            same_ecosystem = [m for m in scope if (m.kind == "package.json")
                              == (not path.endswith((".py", ".pyi")))]
            if not same_ecosystem:
                continue
            declared: set[str] = set()
            for manifest in same_ecosystem:
                declared |= manifest.names()
            if not dist_matches(use.package, declared):
                by_package.setdefault(use.package, []).append((path, use.first_line[path]))
    for package, uses in sorted(by_package.items()):
        out.append(Insight(
            rule="undeclared_dependency", category="Dependencies", severity="medium",
            title=f"`{package}` is imported but not declared",
            summary=f"{plural(len(uses), 'file')} import {package}, but no dependency file "
                    "next to them lists it.",
            why="A fresh install (CI, Docker, a teammate) will fail with ModuleNotFoundError, "
                "or pick up whatever version happens to be installed.",
            suggestion=f"Add {package} (with a version) to the project's dependency file.",
            confidence="heuristic",
            evidence=[Evidence(p, line, line, f"import {package}") for p, line in uses[:5]],
            files=[p for p, _ in uses],
        ))
    return out


def _orphans(ctx: InsightContext) -> list[Insight]:
    graph = ctx.graph.graph
    orphans = []
    for path, info in ctx.files.items():
        name = PurePosixPath(path).name
        if (
            is_test_path(path) or path in ctx.entry_points or info.get("role") in
            {"entry", "api", "ui", "config", "docs", "script", "component", "build", "stub"}
            or name in {"__init__.py", "__main__.py", "setup.py", "conftest.py", "manage.py"}
            or name.endswith((".d.ts", ".config.js", ".config.ts", ".config.mjs"))
            or not path.endswith((".py", ".ts", ".tsx", ".js", ".jsx"))
            or path.startswith(("docs/", "examples/", "scripts/"))
        ):
            continue
        if graph.in_degree(path) == 0 and graph.out_degree(path) == 0:
            orphans.append(path)
    if len(orphans) < 1:
        return []
    return [Insight(
        rule="orphan_module", category="Architecture", severity="low",
        title=f"{plural(len(orphans), 'file')} not connected to anything",
        summary=f"{', '.join(short_name(p) for p in orphans[:5])}"
                + (" …" if len(orphans) > 5 else "")
                + " import no project module and nothing imports them.",
        why="Unconnected modules are often dead code or scripts nobody runs any more; they still "
            "cost reading time and can drift out of date.",
        suggestion="Delete what is unused, or document how each one is meant to be run.",
        confidence="heuristic", evidence=[Evidence(p, 1, 1, p) for p in orphans[:8]],
        files=orphans,
    )]


def _project(ctx: InsightContext) -> list[Insight]:
    out = []
    for path in ctx.utf16_files:
        out.append(Insight(
            rule="utf16_manifest", category="Dependencies", severity="low",
            title=f"{short_name(path)} is saved as UTF-16",
            summary=f"{path} starts with a UTF-16 byte-order mark (the encoding Windows "
                    "PowerShell uses for `pip freeze > requirements.txt`).",
            why="pip copes, but many tools don't: GitHub's dependency graph and Dependabot, "
                "diff views and some CI/Docker steps read it as binary or garbage.",
            suggestion="Re-save it as UTF-8, or regenerate it with "
                       "`pip freeze | Out-File -Encoding utf8 requirements.txt`.",
            confidence="confirmed", evidence=[Evidence(path, 1, 1, path)], files=[path],
        ))
    if not ctx.has_ci and len(ctx.files) >= 5:
        out.append(Insight(
            rule="no_ci", category="Testing", severity="low",
            title="No continuous integration",
            summary="No CI workflow (.github/workflows, .gitlab-ci.yml, …) was found.",
            why="Without CI, tests and linting only run when someone remembers to run them, so "
                "broken changes get merged.",
            suggestion="Add a small GitHub Actions workflow that installs the project and runs "
                       "the tests on every push.",
            confidence="confirmed",
        ))
    return out


_SECRET_VALUE = re.compile(r"""(['"])([^'"]{4})[^'"]*(['"])""")


def redact_line(line: str) -> str:
    return _SECRET_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}••••{m.group(3)}", line)


def generate(ctx: InsightContext) -> list[Insight]:
    insights: list[Insight] = []
    for rule in (_cycles, _functions, _modules, _smells, _testing, _dependencies, _orphans,
                 _project):
        insights.extend(rule(ctx))
    for insight in insights:
        _rank(insight, ctx)
    insights.sort(key=lambda i: (-i.score, i.title))
    return insights
