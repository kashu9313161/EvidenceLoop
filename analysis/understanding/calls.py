"""Function-level call graph for Python: which function calls which, with the line.

Calls are read from the `calls` table (callee as written, line, enclosing symbol) and resolved
to symbol ids (`path::qualified_name`) through the calling file's imports:

- confirmed: the name is defined in the same file (`helper()`, `Cls.make()`, `self.save()` inside
  `Cls`), or an import resolves to a file that defines it (`from shop.orders import place_order`,
  `pricing.total()` after `from shop import pricing`, re-exports followed through `__init__.py`).
- inferred: nothing resolves, but exactly one function, method or class in the repository has
  that name (`order.place()` when only one `place` exists). A best guess, shown as one.

Calls whose callee isn't a plain name chain (`handlers[k]()`, `getattr(x, n)()`) are dynamic
and stay unresolved, as do calls into the standard library and third-party packages.
"""

from __future__ import annotations

import builtins
from collections import defaultdict
from dataclasses import dataclass, field

from analysis.graph.resolver import PythonResolver, _module_stem

MODULE = "<module>"
DYNAMIC = "<dynamic>"
CALLABLE_KINDS = {"function", "method", "class"}
MAX_REEXPORT_HOPS = 3
# Names too common to guess from: builtins and the methods of built-in types. `items.append()`
# is a list method, not the one `append` a repository happens to define.
COMMON_NAMES = set(dir(builtins)) | {
    name for kind in (str, bytes, list, dict, set, tuple, int, float, object)
    for name in dir(kind)
} | {"read", "write", "close", "open", "send", "flush", "seek", "run", "main", "get", "set"}


@dataclass
class CallEdge:
    caller: str  # symbol id of the calling function, class body or module
    callee: str  # symbol id of what is called
    path: str  # file the call is in
    line: int
    tier: str  # confirmed | inferred


@dataclass
class CallGraph:
    edges: list[CallEdge] = field(default_factory=list)
    unresolved: int = 0  # plain names that matched nothing here (library calls, builtins...)
    dynamic: int = 0  # callee isn't a plain name chain

    def callers_of(self, symbol_id: str) -> list[CallEdge]:
        return [e for e in self.edges if e.callee == symbol_id]

    def callees_of(self, symbol_id: str) -> list[CallEdge]:
        return [e for e in self.edges if e.caller == symbol_id]

    def counts(self) -> dict[str, dict[str, int]]:
        """symbol id -> {"callers": distinct callers, "callees": distinct callees}."""
        callers: dict[str, set[str]] = defaultdict(set)
        callees: dict[str, set[str]] = defaultdict(set)
        for edge in self.edges:
            callers[edge.callee].add(edge.caller)
            callees[edge.caller].add(edge.callee)
        return {sid: {"callers": len(callers.get(sid, ())), "callees": len(callees.get(sid, ()))}
                for sid in set(callers) | set(callees)}


@dataclass
class _Binding:
    file: str
    qualified: str | None  # None: the name is the module itself
    tier: str


@dataclass
class _FileScope:
    defs: dict[str, str]  # qualified name -> symbol id (first one wins)
    kinds: dict[str, str]  # qualified name -> kind
    names: dict[str, _Binding]  # local name or dotted module name -> what it refers to
    outside: set[str]  # local names bound to the standard library or a package


class CallResolver:
    def __init__(self, python_files: set[str], symbols_by_file: dict[str, list[dict]],
                 imports_by_file: dict[str, list[dict]]):
        self.files = python_files
        self.symbols_by_file = symbols_by_file
        self.imports_by_file = imports_by_file
        self.resolver = PythonResolver(python_files)
        self._scopes: dict[str, _FileScope] = {}
        by_name: dict[str, list[str]] = defaultdict(list)
        for path in python_files:
            for row in symbols_by_file.get(path, []):
                if row["kind"] in CALLABLE_KINDS:
                    by_name[row["name"]].append(row["symbol_id"])
        self.by_short_name = by_name

    # -- scopes ------------------------------------------------------------

    def scope(self, path: str) -> _FileScope:
        found = self._scopes.get(path)
        if found is not None:
            return found
        defs: dict[str, str] = {}
        kinds: dict[str, str] = {}
        for row in self.symbols_by_file.get(path, []):
            defs.setdefault(row["qualified_name"], row["symbol_id"])
            kinds.setdefault(row["qualified_name"], row["kind"])
        names: dict[str, _Binding] = {}
        outside: set[str] = set()
        for row in self.imports_by_file.get(path, []):
            module, name, alias = row["module"], row.get("name"), row.get("alias")
            level = row.get("level") or 0
            if row["kind"] == "from" and name and name != "*":
                local = alias or name
                res = self.resolver.resolve(path, module, name, level)
                if res.kind != "internal" or not res.target:
                    outside.add(local)
                    continue
                dotted = f"{module}.{name}" if module else name
                is_module = _module_stem(res.target).endswith(dotted.replace(".", "/"))
                names[local] = _Binding(res.target, None if is_module else name, res.tier)
            elif row["kind"] == "import":
                res = self.resolver.resolve(path, module, None, 0)
                if res.kind != "internal" or not res.target:
                    outside.add(alias or module.split(".")[0])
                    continue
                if alias:
                    names[alias] = _Binding(res.target, None, res.tier)
                    continue
                # `import a.b` binds `a`, and `a.b.f()` reaches into a/b.py.
                parts = module.split(".")
                for cut in range(1, len(parts) + 1):
                    prefix = ".".join(parts[:cut])
                    hit = self.resolver.resolve(path, prefix, None, 0)
                    if hit.kind == "internal" and hit.target and \
                            _module_stem(hit.target).endswith(prefix.replace(".", "/")):
                        names[prefix] = _Binding(hit.target, None, hit.tier)
        scope = _FileScope(defs, kinds, names, outside)
        self._scopes[path] = scope
        return scope

    def _in_file(self, path: str, qualified: str, hops: int = 0) -> tuple[str, str] | None:
        """The symbol `qualified` names in `path`: defined there, or imported into it and
        defined elsewhere (a re-export). Returns (symbol id, tier)."""
        scope = self.scope(path)
        if qualified in scope.defs and scope.kinds.get(qualified) in CALLABLE_KINDS:
            return scope.defs[qualified], "confirmed"
        if hops >= MAX_REEXPORT_HOPS:
            return None
        head, _, rest = qualified.partition(".")
        binding = scope.names.get(head)
        if binding is None or binding.file == path:
            return None
        target = f"{binding.qualified}.{rest}" if binding.qualified and rest else (
            binding.qualified or rest)
        if not target:
            return None
        found = self._in_file(binding.file, target, hops + 1)
        if found is None:
            return None
        return found[0], "confirmed" if binding.tier == "confirmed" == found[1] else "inferred"

    # -- calls -------------------------------------------------------------

    def _enclosing_class(self, path: str, enclosing: str | None) -> str | None:
        kinds = self.scope(path).kinds
        parts = (enclosing or "").split(".")
        for cut in range(len(parts), 0, -1):
            prefix = ".".join(parts[:cut])
            if kinds.get(prefix) == "class":
                return prefix
        return None

    def _function_scopes(self, path: str, enclosing: str | None) -> list[str]:
        """Enclosing function scopes, innermost first (class bodies don't enclose methods)."""
        kinds = self.scope(path).kinds
        parts = (enclosing or "").split(".") if enclosing else []
        out = []
        for cut in range(len(parts), 0, -1):
            prefix = ".".join(parts[:cut])
            if kinds.get(prefix) in {"function", "method"}:
                out.append(prefix)
        return out

    def _guess(self, name: str) -> tuple[str, str] | None:
        if name in COMMON_NAMES or name.startswith("__"):
            return None
        candidates = self.by_short_name.get(name, [])
        return (candidates[0], "inferred") if len(candidates) == 1 else None

    def resolve(self, path: str, callee: str, enclosing: str | None) -> tuple[str, str] | None:
        """(symbol id, tier) for one call, or None when it stays unresolved."""
        if callee == DYNAMIC or not callee:
            return None
        scope = self.scope(path)
        parts = callee.split(".")
        head = parts[0]

        if head in {"self", "cls"} and len(parts) == 2:
            owner = self._enclosing_class(path, enclosing)
            if owner:
                found = self._in_file(path, f"{owner}.{parts[1]}")
                if found:
                    return found
            return self._guess(parts[1])  # inherited from a class elsewhere, perhaps

        if len(parts) == 1:
            for prefix in self._function_scopes(path, enclosing):
                nested = f"{prefix}.{head}"
                if scope.kinds.get(nested) in CALLABLE_KINDS:
                    return scope.defs[nested], "confirmed"
            found = self._in_file(path, head)
            if found:
                return found
            if head in scope.outside or head in scope.names:
                return None
            return self._guess(head)

        # Dotted: a module reached by name (`pricing.total`, `a.b.f`), a class here or imported
        # (`Cls.make`), or a method on some object (`order.place`).
        for cut in range(len(parts) - 1, 0, -1):
            binding = scope.names.get(".".join(parts[:cut]))
            if binding is not None and binding.qualified is None:
                found = self._in_file(binding.file, ".".join(parts[cut:]))
                if found:
                    tier = "confirmed" if binding.tier == "confirmed" == found[1] else "inferred"
                    return found[0], tier
                return None
        found = self._in_file(path, callee)
        if found:
            return found
        if head in scope.outside or head in scope.names:
            return None
        return self._guess(parts[-1])


def build_call_graph(python_files: set[str], symbols_by_file: dict[str, list[dict]],
                     imports_by_file: dict[str, list[dict]],
                     calls_by_file: dict[str, list[dict]]) -> CallGraph:
    """Resolve every call in the Python files. `symbols_by_file` rows need symbol_id, kind,
    name and qualified_name; `calls_by_file` rows need callee, line and enclosing_symbol."""
    resolver = CallResolver(python_files, symbols_by_file, imports_by_file)
    graph = CallGraph()
    seen: set[tuple[str, str, int]] = set()
    for path in sorted(python_files):
        scope = resolver.scope(path)
        module_id = scope.defs.get(MODULE, f"{path}::{MODULE}")
        for call in calls_by_file.get(path, []):
            callee = call["callee"]
            if callee == DYNAMIC:
                graph.dynamic += 1
                continue
            found = resolver.resolve(path, callee, call.get("enclosing_symbol"))
            if found is None:
                graph.unresolved += 1
                continue
            enclosing = call.get("enclosing_symbol")
            caller = scope.defs.get(enclosing, module_id) if enclosing else module_id
            callee_id, tier = found
            key = (caller, callee_id, call["line"])
            if callee_id == caller or key in seen:
                continue
            seen.add(key)
            graph.edges.append(CallEdge(caller, callee_id, path, call["line"], tier))
    return graph
