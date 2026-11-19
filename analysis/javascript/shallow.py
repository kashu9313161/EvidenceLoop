"""Shallow JS/TS facts: import, require and export statements, found with regular expressions.

This is deliberately shallow (no full parser): it finds module dependencies and exported
names with line numbers. Comments are blanked first so commented-out imports don't count.
"""

import bisect
import re
from pathlib import PurePosixPath

from analysis.models import FileFacts, ImportRef, Symbol

JS_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXTENSIONS = {".ts", ".tsx"}
SHALLOW_EXTENSIONS = JS_EXTENSIONS | TS_EXTENSIONS

_Q = r"(?P<q>['\"])(?P<mod>[^'\"\n]+)(?P=q)"
_START = r"(?<![.\w$])"

# The clause is tokens separated by whitespace, each token atomic, so a run of spaces can be
# matched only one way. (The old `[\w$*{}\s,]+?\s*from` let the clause and `\s*` split the same
# spaces every possible way: `import` and a few thousand spaces took seconds — a ReDoS a
# repository could trigger with one crafted file.) A clause never contains the word `import`, so
# it stops at the next one: `import` repeated without `from` costs one step per occurrence
# instead of scanning ahead from each. The 400-token cap, far more than any real import list,
# bounds the rest. (`from` can't be excluded the same way: `import { from } from 'rxjs'`.)
IMPORT_FROM = re.compile(
    _START + r"import\s+(?P<type>type\s+)?"
    r"(?P<clause>(?!import\b)(?>[\w$*{},]+)(?:\s+(?!import\b)(?>[\w$*{},]+)){0,400}?)"
    r"\s*from\s*" + _Q
)
SIDE_EFFECT = re.compile(_START + r"import\s*" + _Q)
DYNAMIC = re.compile(_START + r"import\s*\(\s*" + _Q + r"\s*\)")
REQUIRE = re.compile(_START + r"require\s*\(\s*" + _Q + r"\s*\)")
REEXPORT = re.compile(
    _START
    + r"export\s+(?P<type>type\s+)?(?P<clause>\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s*from\s*"
    + _Q
)
EXPORT_DECL = re.compile(
    _START
    + r"export\s+(?P<default>default\s+)?(?:declare\s+)?(?:async\s+)?(?:abstract\s+)?"
    r"(?:function\s*\*?|class|const|let|var|interface|type|enum|namespace)\s+(?P<name>[\w$]+)"
)
EXPORT_DEFAULT = re.compile(_START + r"export\s+default\b")
EXPORT_LIST = re.compile(_START + r"export\s+(?:type\s+)?\{(?P<names>[^}]*)\}(?!\s*from\b)")


def blank_comments(source: str) -> str:
    """Replace // and /* */ comments with spaces, keeping strings and line numbers intact."""
    out: list[str] = []
    i, n = 0, len(source)
    quote: str | None = None

    while i < n:
        char = source[i]

        if quote:
            out.append(char)
            if char == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if char == quote or (char == "\n" and quote != "`"):
                quote = None
            i += 1
            continue

        if char in "'\"`":
            quote = char
            out.append(char)
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("".join("\n" if c == "\n" else " " for c in source[i:end]))
            i = end
        else:
            out.append(char)
            i += 1

    return "".join(out)


def _split_names(text: str) -> list[tuple[str, str | None]]:
    """`a, b as c, type D` -> [(a, None), (b, c), (D, None)]."""
    names = []

    for part in text.split(","):
        part = re.sub(r"^\s*type\s+", "", part).strip()
        if not part:
            continue
        pieces = re.split(r"\s+as\s+", part)
        names.append((pieces[0].strip(), pieces[1].strip() if len(pieces) > 1 else None))

    return names


def _clause_imports(clause: str) -> list[tuple[str | None, str | None]]:
    """Import clause -> (name, alias) pairs. Default import is name='default'."""
    clause = clause.strip()
    pairs: list[tuple[str | None, str | None]] = []

    braces = re.search(r"\{([^}]*)\}", clause)
    if braces:
        pairs += [(name, alias) for name, alias in _split_names(braces.group(1))]
        clause = (clause[: braces.start()] + clause[braces.end() :]).strip()

    for part in (p.strip() for p in clause.split(",")):
        if not part:
            continue
        namespace = re.fullmatch(r"\*\s+as\s+([\w$]+)", part)
        if namespace:
            pairs.append(("*", namespace.group(1)))
        elif re.fullmatch(r"[\w$]+", part):
            pairs.append(("default", part))

    return pairs or [(None, None)]


def extract_file(relative_path: str, source: str) -> FileFacts:
    """Imports and exports for one JS/TS file. Never fails: unmatched syntax is just skipped."""
    text = blank_comments(source)
    newlines = [i for i, c in enumerate(text) if c == "\n"]

    def line_at(offset: int) -> int:
        return bisect.bisect_right(newlines, offset - 1) + 1

    imports: list[ImportRef] = []
    symbols: list[Symbol] = []

    for match in IMPORT_FROM.finditer(text):
        line = line_at(match.start())
        for name, alias in _clause_imports(match.group("clause")):
            imports.append(
                ImportRef(kind="type" if match.group("type") else "import",
                          module=match.group("mod"), name=name, alias=alias, line=line)
            )

    for pattern, kind in ((SIDE_EFFECT, "side_effect"), (DYNAMIC, "dynamic"),
                          (REQUIRE, "require")):
        for match in pattern.finditer(text):
            imports.append(
                ImportRef(kind=kind, module=match.group("mod"), line=line_at(match.start()))
            )

    for match in REEXPORT.finditer(text):
        line = line_at(match.start())
        clause = match.group("clause").strip()
        if clause.startswith("*"):
            alias = clause.split()[-1] if " as " in f" {clause} " else None
            pairs: list[tuple[str, str | None]] = [("*", alias)]
        else:
            pairs = _split_names(clause.strip("{}"))
        for name, alias in pairs:
            imports.append(
                ImportRef(kind="type" if match.group("type") else "reexport",
                          module=match.group("mod"), name=name, alias=alias, line=line)
            )
            exported = alias or name
            if exported != "*":
                symbols.append(_export(exported, line))

    named_default_offsets = set()
    for match in EXPORT_DECL.finditer(text):
        if match.group("name") in {"extends", "implements"}:
            continue  # `export default class extends Base` is an anonymous default
        if match.group("default"):
            named_default_offsets.add(match.start())
        symbols.append(_export(match.group("name"), line_at(match.start())))

    for match in EXPORT_DEFAULT.finditer(text):
        if match.start() not in named_default_offsets:
            symbols.append(_export("default", line_at(match.start())))

    for match in EXPORT_LIST.finditer(text):
        line = line_at(match.start())
        for name, alias in _split_names(match.group("names")):
            symbols.append(_export(alias or name, line))

    imports.sort(key=lambda ref: ref.line)
    symbols.sort(key=lambda symbol: symbol.start_line)

    suffix = PurePosixPath(relative_path).suffix.lower()
    lines = source.splitlines()
    module_symbol = Symbol(
        kind="module",
        name=PurePosixPath(relative_path).stem,
        qualified_name="<module>",
        start_line=1,
        end_line=max(1, len(lines)),
        text="",
    )

    return FileFacts(
        relative_path=relative_path,
        language="typescript" if suffix in TS_EXTENSIONS else "javascript",
        module=str(PurePosixPath(relative_path).with_suffix("")),
        symbols=[module_symbol, *symbols],
        imports=imports,
    )


def _export(name: str, line: int) -> Symbol:
    return Symbol(kind="export", name=name, qualified_name=name, start_line=line, end_line=line)
