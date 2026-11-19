"""File-level dependency graph: edges with evidence lines, plus the metrics the UI shows.

Direction: an edge `A -> B` means "A imports B" (A depends on B). So:
- fan-in of B  = distinct files that import B (how many depend on it)
- impact of B  = everything that can reach B (what may break if B changes)

Imports that exist only for type hints (Python `if TYPE_CHECKING:` blocks, TypeScript
`import type`) never run. They are kept as edges, marked `type_only`, but left out of the
networkx graph, so they don't create import cycles or inflate fan-in and impact.

Imports inside a Python function body run when it's called, not when the module loads. They
are real dependencies (kept in the graph), but cannot make an import cycle, so cycles are
found among load-time imports only (`Edge.deferred` marks the others).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import networkx as nx

from analysis.graph.resolver import JsResolver, PythonResolver, Resolution


@dataclass
class Edge:
    source: str
    target: str
    tier: str  # confirmed | inferred
    lines: list[int] = field(default_factory=list)
    kind: str = "imports"  # imports | tests
    type_only: bool = False  # every import behind this edge is for type hints only
    deferred: bool = False  # every runtime import behind this edge is inside a function


@dataclass
class ExternalUse:
    package: str
    kind: str  # stdlib | external
    files: set[str] = field(default_factory=set)
    runtime_files: set[str] = field(default_factory=set)  # imported for real, not for type hints
    first_line: dict[str, int] = field(default_factory=dict)
    lines: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class GraphResult:
    graph: nx.DiGraph
    edges: dict[tuple[str, str], Edge]
    externals: dict[str, ExternalUse]
    unresolved: list[tuple[str, str, int]]  # importer, module, line
    resolved_internal: int
    cycles: list[list[str]]


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name.lower()
    parts = {part.lower() for part in p.parts[:-1]}
    return (
        bool(parts & {"tests", "test", "__tests__", "spec", "testing"})
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".test.js", ".test.jsx",
                          ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx"))
        or name == "conftest.py"
    )


def build_graph(
    code_files: list[str],
    imports_by_file: dict[str, list[dict]],
    type_only_lines: dict[str, set[int]] | None = None,
    js: JsResolver | None = None,
    deferred_lines: dict[str, set[int]] | None = None,
) -> GraphResult:
    """`imports_by_file[path]` rows carry kind, module, name, level, line (from the DB).

    `type_only_lines[path]` lists Python import lines inside `if TYPE_CHECKING:` blocks; rows
    of kind "type" (TypeScript `import type`) are type-only too. `js` lets the caller pass a
    resolver that knows the project's tsconfig aliases and package names.
    `deferred_lines[path]` lists Python import lines inside function bodies.
    """
    python_files = {p for p in code_files if p.endswith((".py", ".pyi"))}
    js_files = set(code_files) - python_files
    py = PythonResolver(python_files)
    js = js or JsResolver(js_files)
    type_only_lines = type_only_lines or {}
    deferred_lines = deferred_lines or {}
    runtime: set[tuple[str, str]] = set()
    load_time: set[tuple[str, str]] = set()

    graph = nx.DiGraph()
    graph.add_nodes_from(code_files)
    edges: dict[tuple[str, str], Edge] = {}
    externals: dict[str, ExternalUse] = {}
    unresolved: list[tuple[str, str, int]] = []
    resolved_internal = 0

    for importer in code_files:
        for row in imports_by_file.get(importer, []):
            if importer in python_files:
                res: Resolution = py.resolve(importer, row["module"], row.get("name"),
                                             row.get("level") or 0)
            else:
                res = js.resolve(importer, row["module"])
            line = row["line"]

            if res.kind == "internal" and res.target and res.target != importer:
                resolved_internal += 1
                key = (importer, res.target)
                edge = edges.get(key)
                if edge is None:
                    edge = Edge(importer, res.target, res.tier)
                    edges[key] = edge
                elif res.tier == "confirmed":
                    edge.tier = "confirmed"
                if line not in edge.lines:
                    edge.lines.append(line)
                if not (row.get("kind") == "type" or line in type_only_lines.get(importer, ())):
                    runtime.add(key)
                    if line not in deferred_lines.get(importer, ()):
                        load_time.add(key)
            elif res.kind in ("stdlib", "external") and res.package:
                use = externals.setdefault(res.package, ExternalUse(res.package, res.kind))
                use.files.add(importer)
                if not (row.get("kind") == "type" or line in type_only_lines.get(importer, ())):
                    use.runtime_files.add(importer)
                use.first_line.setdefault(importer, line)
                use.lines.setdefault(importer, []).append(line)
            elif res.kind == "unresolved":
                unresolved.append((importer, res.package or row["module"], line))

    for (source, target), edge in edges.items():
        if is_test_path(source) and not is_test_path(target):
            edge.kind = "tests"
        edge.type_only = (source, target) not in runtime
        edge.deferred = not edge.type_only and (source, target) not in load_time
        if not edge.type_only:
            graph.add_edge(source, target)

    loading = graph.edge_subgraph(load_time)
    cycles = [
        sorted(component)
        for component in nx.strongly_connected_components(loading)
        if len(component) > 1
    ]
    cycles.sort(key=lambda c: (-len(c), c))
    return GraphResult(graph, edges, externals, unresolved, resolved_internal, cycles)


def fan_in(graph: nx.DiGraph, node: str, exclude_tests: bool = True) -> int:
    return sum(
        1 for source in graph.predecessors(node) if not (exclude_tests and is_test_path(source))
    )


def impact(graph: nx.DiGraph, node: str) -> set[str]:
    """Files that (transitively) depend on `node`: reverse reachability."""
    return nx.ancestors(graph, node)


def importance(graph: nx.DiGraph, damping: float = 0.85, rounds: int = 100) -> dict[str, float]:
    """PageRank along the dependency direction: files many others rely on score high.

    Plain power iteration (no SciPy); test files are left out so they don't inflate scores.
    """
    nodes = [n for n in graph.nodes if not is_test_path(n)]
    if not nodes or graph.number_of_edges() == 0:
        return {node: 0.0 for node in graph.nodes}
    count = len(nodes)
    members = set(nodes)
    out = {n: [t for t in graph.successors(n) if t in members] for n in nodes}
    rank = dict.fromkeys(nodes, 1.0 / count)
    for _ in range(rounds):
        dangling = sum(rank[n] for n in nodes if not out[n])
        new = dict.fromkeys(nodes, (1 - damping) / count + damping * dangling / count)
        for node in nodes:
            if out[node]:
                share = damping * rank[node] / len(out[node])
                for target in out[node]:
                    new[target] += share
        delta = sum(abs(new[n] - rank[n]) for n in nodes)
        rank = new
        if delta < 1e-9:
            break
    return {node: rank.get(node, 0.0) for node in graph.nodes}


# Folders of code that illustrates or measures the project rather than being it.
SUPPORT_DIRS = {"examples", "example", "docs", "doc", "docs_src", "demo", "demos", "samples",
                "sample", "playground", "benchmarks", "benchmark", "bench", "tutorial",
                "tutorials", "cookbook"}


def support_area(path: str) -> str | None:
    """`docs_src/security/tutorial001.py` -> `docs_src`: the outermost example/docs folder
    holding the file, or None for the project's own code."""
    parts = PurePosixPath(path).parts[:-1]
    for index, part in enumerate(parts):
        if part.lower() in SUPPORT_DIRS:
            return "/".join(parts[: index + 1])
    return None


def area_of(path: str, depth: int) -> str:
    """Folder used to group files into areas; `src/` is transparent."""
    parts = [p for p in PurePosixPath(path).parts[:-1]]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return "(root)"
    return "/".join(parts[:depth])


def choose_area_depth(paths: list[str]) -> int:
    """Descend while one folder holds most of the code (e.g. a monorepo's single project)."""
    depth = 1
    for _ in range(3):
        groups: dict[str, int] = defaultdict(int)
        for path in paths:
            groups[area_of(path, depth)] += 1
        biggest = max(groups.values(), default=0)
        deeper = {area_of(p, depth + 1) for p in paths}
        if paths and biggest / len(paths) >= 0.7 and len(deeper) > len(groups):
            depth += 1
        else:
            break
    return depth
