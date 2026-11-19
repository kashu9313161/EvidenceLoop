"""Resolve import statements to files inside the snapshot.

Tiers, shown to users as-is:
- confirmed: the import resolves to exactly one file in the repository (relative imports,
  JS relative paths, or an absolute module path that only one candidate root can satisfy).
- inferred: more than one file could satisfy the import (ambiguous roots, `@/` aliases,
  or a local module that shadows a standard-library name); the nearest one is used.

An import that resolves to nothing is classified as standard library, third-party package,
or internal-but-unresolved (a name that looks like one of the repository's own packages).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

from analysis.javascript.shallow import blank_comments

PY_SUFFIXES = (".py", ".pyi")
JS_PROBE = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts",
            "/index.ts", "/index.tsx", "/index.js", "/index.jsx", "/index.mjs")
# TypeScript's ESM convention: `import "./util.js"` names the file that compiles to util.js,
# which in the repository is util.ts (or .tsx, .mts, .cts).
TS_SOURCES_FOR = {".js": (".ts", ".tsx", ".d.ts"), ".jsx": (".tsx",), ".mjs": (".mts",),
                  ".cjs": (".cts",)}
# Where a repository keeps the source of its own package, most common first.
OWN_SOURCE_DIRS = ("src", "source", "", "lib")
ASSET_SUFFIXES = {".css", ".scss", ".sass", ".less", ".svg", ".png", ".jpg", ".jpeg", ".gif",
                  ".webp", ".ico", ".woff", ".woff2", ".ttf", ".json", ".md", ".html", ".wasm"}
# Plus modules newer than the running Python (3.14 added these), so code written for a newer
# version isn't told it has undeclared dependencies.
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__", "annotationlib",
                                                         "compression"}
# Node's own modules (`import path from 'path'`): part of the runtime, never a dependency.
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants",
    "crypto", "dgram", "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2",
    "https", "inspector", "module", "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
    "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
}


@dataclass(frozen=True)
class Alias:
    """One tsconfig `paths` entry: `"zustand/*": ["./src/*.ts"]`, targets as repo paths."""

    pattern: str
    targets: tuple[str, ...]


def _module_stem(path: str) -> str:
    """`a/b/__init__.py` -> `a/b`, `a/b.py` -> `a/b`."""
    stem = str(PurePosixPath(path).with_suffix(""))
    return stem[: -len("/__init__")] if stem.endswith("/__init__") else stem


@dataclass(frozen=True)
class Resolution:
    kind: str  # internal | stdlib | external | unresolved | asset
    target: str | None = None  # file path for internal
    package: str | None = None  # top-level name for stdlib/external/unresolved
    tier: str = "confirmed"  # confirmed | inferred (internal only)


class PythonResolver:
    def __init__(self, python_files: set[str]):
        self.files = python_files
        dirs: set[str] = set()
        for path in python_files:
            parent = PurePosixPath(path).parent
            while str(parent) not in ("", "."):
                dirs.add(str(parent))
                parent = parent.parent
        self.dirs = dirs
        # Standard import roots: the repo root and every `src` directory.
        self.standard_roots = [""] + sorted(d for d in dirs if PurePosixPath(d).name == "src")
        # Names that look like the repo's own top-level packages/modules (for "unresolved").
        own: set[str] = set()
        for path in python_files:
            parts = PurePosixPath(path).with_suffix("").parts
            own.update(p for p in parts if p not in {"src", "__init__"})
        self.own_names = own

    def _module_file(self, base: str, dotted: str) -> str | None:
        rel = dotted.replace(".", "/")
        prefix = f"{base}/{rel}" if base else rel
        for candidate in (*(prefix + s for s in PY_SUFFIXES),
                          *(f"{prefix}/__init__{s}" for s in PY_SUFFIXES)):
            if candidate in self.files:
                return candidate
        return None

    def _in_package(self, importer: str) -> bool:
        folder = PurePosixPath(importer).parent
        return str(folder) != "." and any(f"{folder}/__init__{s}" in self.files
                                          for s in PY_SUFFIXES)

    def _roots_for(self, importer: str) -> list[str]:
        """Nearest first: the importer's own directory and its ancestors, then standard roots."""
        roots: list[str] = []
        parent = PurePosixPath(importer).parent
        while True:
            text = "" if str(parent) == "." else str(parent)
            roots.append(text)
            if text == "":
                break
            parent = parent.parent
        for root in self.standard_roots:
            if root not in roots:
                roots.append(root)
        return roots

    def _absolute(self, importer: str, dotted: str) -> tuple[str | None, str]:
        """Resolve `a.b.c` (deepest existing prefix). Returns (file, tier)."""
        parts = dotted.split(".")
        roots = self._roots_for(importer)
        if parts[0] in STDLIB and self._in_package(importer):
            # Python 3 never searches a package module's own folder for absolute imports:
            # `from types import X` in click/_compat.py is the standard library, not
            # click/types.py. (A loose script's folder is on sys.path, so it can shadow.)
            roots = self.standard_roots
        for cut in range(len(parts), 0, -1):
            module = ".".join(parts[:cut])
            hits: list[str] = []
            for root in roots:
                found = self._module_file(root, module)
                if found and found not in hits:
                    hits.append(found)
            if hits:
                shadows_stdlib = parts[0] in STDLIB
                tier = "confirmed" if len(hits) == 1 and not shadows_stdlib else "inferred"
                return hits[0], tier
        return None, "confirmed"

    def resolve(self, importer: str, module: str, name: str | None, level: int) -> Resolution:
        if level > 0:
            base = PurePosixPath(importer).parent
            for _ in range(level - 1):
                base = base.parent
            base_text = "" if str(base) == "." else str(base)
            if module and name and name != "*":
                found = self._module_file(base_text, f"{module}.{name}")
                if found:
                    return Resolution("internal", found)
            if not module and name and name != "*":
                found = self._module_file(base_text, name)
                if found:
                    return Resolution("internal", found)
            if module:
                found = self._module_file(base_text, module)
            else:
                init = [f"{base_text}/__init__{s}" if base_text else f"__init__{s}"
                        for s in PY_SUFFIXES]
                found = next((c for c in init if c in self.files), None)
            if found:
                return Resolution("internal", found)
            return Resolution("unresolved", package=f"{'.' * level}{module}")

        top = module.split(".")[0]
        if name and name != "*":
            found, tier = self._absolute(importer, f"{module}.{name}")
            if found and _module_stem(found).endswith(f"{module}.{name}".replace(".", "/")):
                return Resolution("internal", found, tier=tier)
        found, tier = self._absolute(importer, module)
        if found:
            return Resolution("internal", found, tier=tier)
        if top in STDLIB:
            return Resolution("stdlib", package=top)
        if top in self.own_names:
            return Resolution("unresolved", package=module)
        return Resolution("external", package=top)


class JsResolver:
    def __init__(self, files: set[str], aliases: list[Alias] | None = None,
                 packages: dict[str, str] | None = None):
        self.files = files
        # Longest pattern first, as TypeScript picks the most specific `paths` entry.
        self.aliases = sorted(aliases or [], key=lambda a: -len(a.pattern))
        self.packages = packages or {}  # the repository's own package names -> directory

    def _from_alias(self, specifier: str) -> str | None:
        for alias in self.aliases:
            head, star, tail = alias.pattern.partition("*")
            if star:
                if not (specifier.startswith(head) and specifier.endswith(tail)
                        and len(specifier) >= len(head) + len(tail)):
                    continue
                middle = specifier[len(head): len(specifier) - len(tail)]
            elif specifier != alias.pattern:
                continue
            else:
                middle = ""
            for target in alias.targets:
                normal = self._normalize(target.replace("*", middle, 1))
                found = self._probe(normal) if normal is not None else None
                if found:
                    return found
        return None

    def _from_own_package(self, specifier: str) -> str | None:
        """`zustand/vanilla` inside the zustand repository: its own source, not node_modules."""
        for name, directory in sorted(self.packages.items(), key=lambda p: -len(p[0])):
            if specifier != name and not specifier.startswith(name + "/"):
                continue
            rest = specifier[len(name):].lstrip("/")
            prefix = f"{directory}/" if directory else ""
            folders = [f"{prefix}{d}/" if d else prefix for d in OWN_SOURCE_DIRS]
            short = PurePosixPath(name).name
            bases = ([f"{f}{rest}" for f in folders] if rest
                     else [f"{f}index" for f in folders]
                     + [f"{prefix}src/{short}", f"{prefix}source/{short}"])
            for base in bases:
                found = self._probe(base)
                if found:
                    return found
        return None

    def _probe(self, base: str) -> str | None:
        for suffix in JS_PROBE:
            # The repository root itself (`require("..")` from a top-level folder): its
            # index file is "index.js", not "/index.js".
            candidate = base + suffix if base else suffix.lstrip("/")
            if candidate and candidate in self.files:
                return candidate
        stem, dot, extension = base.rpartition(".")
        if dot and "/" not in extension:
            for source in TS_SOURCES_FOR.get(f".{extension.lower()}", ()):
                if stem + source in self.files:
                    return stem + source
        return None

    @staticmethod
    def _normalize(path: str) -> str | None:
        parts: list[str] = []
        for part in path.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part)
        return "/".join(parts)

    def resolve(self, importer: str, specifier: str) -> Resolution:
        if PurePosixPath(specifier.split("?")[0]).suffix.lower() in ASSET_SUFFIXES:
            return Resolution("asset", package=specifier)
        if specifier.startswith((".", "/")):
            joined = str(PurePosixPath(importer).parent / specifier)
            normal = self._normalize(joined)
            found = self._probe(normal) if normal is not None else None
            if found:
                return Resolution("internal", found)
            return Resolution("unresolved", package=specifier)
        found = self._from_alias(specifier)
        if found:  # declared in tsconfig/jsconfig `paths`, so not a guess
            return Resolution("internal", found)
        if specifier.startswith(("@/", "~/")):
            rest = specifier[2:]
            for base in (f"src/{rest}", rest):
                found = self._probe(base)
                if found:
                    return Resolution("internal", found, tier="inferred")
            return Resolution("unresolved", package=specifier)
        if specifier.startswith("node:"):
            return Resolution("stdlib", package=specifier[5:].split("/")[0])
        found = self._from_own_package(specifier)
        if found:  # matched by package name and folder layout: a best guess
            return Resolution("internal", found, tier="inferred")
        parts = specifier.split("/")
        package = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
        if package in NODE_BUILTINS:
            return Resolution("stdlib", package=package)
        return Resolution("external", package=package)


def _loads_jsonc(text: str):
    """tsconfig/jsconfig allow comments and trailing commas; plain json.loads does not."""
    cleaned = re.sub(r",(\s*[}\]])", r"\1", blank_comments(text))
    try:
        return json.loads(cleaned)
    except (ValueError, RecursionError, MemoryError):  # bad JSON, or nested to crash the parser
        return None


def js_aliases(config_path: str, text: str) -> list[Alias]:
    """`compilerOptions.paths` from one tsconfig.json/jsconfig.json, as repository paths.

    Targets are relative to `baseUrl` (itself relative to the config file), or to the config
    file when there is no `baseUrl`. `extends` is not followed.
    """
    data = _loads_jsonc(text)
    options = data.get("compilerOptions") if isinstance(data, dict) else None
    paths = options.get("paths") if isinstance(options, dict) else None
    if not isinstance(paths, dict):
        return []
    config_dir = PurePosixPath(config_path).parent
    base = config_dir / str(options.get("baseUrl") or ".")
    aliases = []
    for pattern, targets in paths.items():
        if not isinstance(pattern, str) or not isinstance(targets, list):
            continue
        resolved = tuple(str(base / t) for t in targets if isinstance(t, str))
        if resolved:
            aliases.append(Alias(pattern, resolved))
    return aliases


def package_names(manifests: dict[str, str]) -> dict[str, str]:
    """`name` of every package.json in the repository -> its directory (`""` for the root)."""
    names: dict[str, str] = {}
    for path, text in manifests.items():
        try:
            data = json.loads(text)
        except (ValueError, RecursionError, MemoryError):  # unreadable: skip, don't fail
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and re.fullmatch(r"(@[\w.-]+/)?[\w.-]+", name):
            parent = str(PurePosixPath(path).parent)
            names.setdefault(name, "" if parent == "." else parent)
    return names
