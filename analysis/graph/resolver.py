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

import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

PY_SUFFIXES = (".py", ".pyi")
JS_PROBE = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts",
            "/index.ts", "/index.tsx", "/index.js", "/index.jsx", "/index.mjs")
ASSET_SUFFIXES = {".css", ".scss", ".sass", ".less", ".svg", ".png", ".jpg", ".jpeg", ".gif",
                  ".webp", ".ico", ".woff", ".woff2", ".ttf", ".json", ".md", ".html", ".wasm"}
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


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
        for cut in range(len(parts), 0, -1):
            module = ".".join(parts[:cut])
            hits: list[str] = []
            for root in self._roots_for(importer):
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
    def __init__(self, files: set[str]):
        self.files = files

    def _probe(self, base: str) -> str | None:
        for suffix in JS_PROBE:
            candidate = base + suffix
            if candidate in self.files:
                return candidate
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
        if specifier.startswith(("@/", "~/")):
            rest = specifier[2:]
            for base in (f"src/{rest}", rest):
                found = self._probe(base)
                if found:
                    return Resolution("internal", found, tier="inferred")
            return Resolution("unresolved", package=specifier)
        if specifier.startswith("node:"):
            return Resolution("stdlib", package=specifier[5:].split("/")[0])
        parts = specifier.split("/")
        package = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
        return Resolution("external", package=package)
