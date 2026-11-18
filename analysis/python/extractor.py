"""Python facts from the standard-library syntax tree. Repository code is parsed, never run."""

import ast
from pathlib import PurePosixPath

from analysis.models import AnalysisFailure, CallRef, FileFacts, ImportRef, Symbol

MODULE_QUALNAME = "<module>"
DYNAMIC_CALLEE = "<dynamic>"


def module_name_for(relative_path: str) -> str:
    """Best-guess dotted module name: `src/pkg/a.py` -> `pkg.a`, `pkg/__init__.py` -> `pkg`.

    M3 does the real import resolution; this is a readable label.
    """
    parts = list(PurePosixPath(relative_path).with_suffix("").parts)

    if parts and parts[-1] == "__init__" and len(parts) > 1:
        parts = parts[:-1]
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]

    return ".".join(parts)


def decode_source(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AnalysisFailure(f"Not valid UTF-8 (byte {exc.start})") from exc


def _dotted(node: ast.AST) -> str | None:
    """`a.b.c` for a plain Name/Attribute chain, else None."""
    parts: list[str] = []

    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value

    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))

    return None


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse handles every parsed node in practice
        return "<unparseable>"


def _start_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])


def _text(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


class _Visitor(ast.NodeVisitor):
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.stack: list[tuple[str, str]] = []  # (kind, qualified_name)
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRef] = []
        self.calls: list[CallRef] = []

    # -- helpers -----------------------------------------------------------

    def _qualify(self, name: str) -> str:
        return f"{self.stack[-1][1]}.{name}" if self.stack else name

    def _enclosing(self) -> str | None:
        return self.stack[-1][1] if self.stack else None

    # -- definitions -------------------------------------------------------

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        start, end = _start_line(node), node.end_lineno or node.lineno
        in_class = bool(self.stack) and self.stack[-1][0] == "class"
        qualified = self._qualify(node.name)

        self.symbols.append(
            Symbol(
                kind="method" if in_class else "function",
                name=node.name,
                qualified_name=qualified,
                parent=self._enclosing(),
                start_line=start,
                end_line=end,
                decorators=[_unparse(d) for d in node.decorator_list],
                has_docstring=ast.get_docstring(node) is not None,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                text=_text(self.lines, start, end),
            )
        )

        # Decorators and defaults run in the enclosing scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)

        self.stack.append(("function", qualified))
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        start, end = _start_line(node), node.end_lineno or node.lineno
        qualified = self._qualify(node.name)

        # Chunking rule: a class's own text is its header, up to the first method.
        methods = [
            n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        header_end = _start_line(methods[0]) - 1 if methods else end

        self.symbols.append(
            Symbol(
                kind="class",
                name=node.name,
                qualified_name=qualified,
                parent=self._enclosing(),
                start_line=start,
                end_line=end,
                decorators=[_unparse(d) for d in node.decorator_list],
                bases=[_unparse(b) for b in node.bases]
                + [_unparse(k) for k in node.keywords],
                has_docstring=ast.get_docstring(node) is not None,
                text=_text(self.lines, start, max(start, header_end)),
            )
        )

        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)

        self.stack.append(("class", qualified))
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    # -- imports -----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                ImportRef(kind="import", module=alias.name, alias=alias.asname, line=node.lineno)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.imports.append(
                ImportRef(
                    kind="from",
                    module=node.module or "",
                    name=alias.name,
                    alias=alias.asname,
                    level=node.level or 0,
                    line=node.lineno,
                )
            )

    # -- calls -------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(
            CallRef(
                callee=_dotted(node.func) or DYNAMIC_CALLEE,
                line=node.lineno,
                enclosing_symbol=self._enclosing(),
            )
        )
        self.generic_visit(node)


def _module_text(lines: list[str], tree: ast.Module) -> str:
    """Module-level lines outside top-level defs and classes (imports, constants, scripts)."""
    covered: set[int] = set()

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            covered.update(range(_start_line(node), (node.end_lineno or node.lineno) + 1))

    return "\n".join(
        line for number, line in enumerate(lines, start=1) if number not in covered
    ).strip()


def extract_file(relative_path: str, source: str) -> FileFacts:
    """Symbols, imports and calls for one Python file. Raises AnalysisFailure on bad syntax."""
    try:
        tree = ast.parse(source, filename=relative_path, type_comments=False)
    except SyntaxError as exc:
        where = f"line {exc.lineno}" if exc.lineno else "unknown line"
        raise AnalysisFailure(f"Syntax error at {where}: {exc.msg}") from exc
    except (ValueError, RecursionError, MemoryError) as exc:
        raise AnalysisFailure(f"Could not parse: {type(exc).__name__}") from exc

    lines = source.splitlines()
    visitor = _Visitor(lines)

    try:
        visitor.visit(tree)
    except RecursionError as exc:
        raise AnalysisFailure("Nesting too deep to analyze") from exc

    module_symbol = Symbol(
        kind="module",
        name=PurePosixPath(relative_path).stem,
        qualified_name=MODULE_QUALNAME,
        start_line=1,
        end_line=max(1, len(lines)),
        has_docstring=ast.get_docstring(tree) is not None,
        text=_module_text(lines, tree),
    )

    return FileFacts(
        relative_path=relative_path,
        language="python",
        module=module_name_for(relative_path),
        symbols=[module_symbol, *visitor.symbols],
        imports=visitor.imports,
        calls=visitor.calls,
    )
