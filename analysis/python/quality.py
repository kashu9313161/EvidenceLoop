"""Deeper Python facts for the understanding layer: complexity, smells, routes, entry points.

Everything here is computed from the syntax tree. Repository code is parsed, never run.
Each fact carries the line range it came from, so it can be shown as evidence.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

# Branching constructs that add a path through a function (McCabe-style count).
_BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler, ast.Assert)
_BLOCKS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
if hasattr(ast, "TryStar"):  # Python 3.11+
    _BLOCKS = (*_BLOCKS, ast.TryStar)
if hasattr(ast, "Match"):
    _BLOCKS = (*_BLOCKS, ast.Match)

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route",
                "api_route", "websocket"}
APP_FACTORIES = {
    "FastAPI": "FastAPI app",
    "Flask": "Flask app",
    "APIRouter": "FastAPI router",
    "Blueprint": "Flask blueprint",
    "Typer": "Typer CLI",
    "Celery": "Celery app",
    "Sanic": "Sanic app",
    "Starlette": "Starlette app",
}
SECRET_NAME = re.compile(
    r"(pass(word|wd)?|secret|api[_-]?key|access[_-]?key|token|private[_-]?key)", re.IGNORECASE
)
PLACEHOLDER_VALUE = re.compile(r"^(<.*>|\*+|x+|changeme|your[-_ ].*|example.*|test.*|dummy.*)$",
                               re.IGNORECASE)


@dataclass
class FunctionMetrics:
    qualified_name: str
    start_line: int
    end_line: int
    complexity: int
    max_nesting: int
    params: int
    is_public: bool
    has_docstring: bool

    @property
    def lines(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class Smell:
    rule: str
    line: int
    end_line: int
    detail: str


@dataclass
class Route:
    method: str
    path: str
    handler: str
    line: int


@dataclass
class PythonQuality:
    loc: int = 0  # non-blank, non-comment lines
    total_lines: int = 0
    module_docstring: str | None = None
    functions: list[FunctionMetrics] = field(default_factory=list)
    # (qualified name, start line, end line, method count)
    classes: list[tuple[str, int, int, int]] = field(default_factory=list)
    smells: list[Smell] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    has_main_guard: int | None = None  # line of `if __name__ == "__main__":`
    app_objects: list[tuple[str, str, int]] = field(default_factory=list)  # var, kind, line
    top_level_statements: int = 0  # executable statements at module level (script-ness)
    type_checking_lines: set[int] = field(default_factory=set)  # imports only for type hints
    # Imports inside a function body: they run when it's called, not when the module loads,
    # which is the usual way to break an import cycle.
    deferred_lines: set[int] = field(default_factory=set)
    model_classes: int = 0  # classes deriving from a data/ORM base or dataclass


def _complexity(func: ast.AST) -> tuple[int, int]:
    """(cyclomatic complexity, max block nesting) of a function, not counting nested defs."""
    complexity = 1
    max_depth = 0

    def walk(node: ast.AST, depth: int) -> None:
        nonlocal complexity, max_depth
        for child in ast.iter_child_nodes(node):
            nested = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
            if isinstance(child, nested):
                continue
            if isinstance(child, _BRANCHES):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
            elif isinstance(child, ast.comprehension):
                complexity += 1 + len(child.ifs)
            elif hasattr(ast, "match_case") and isinstance(child, ast.match_case):
                complexity += 1
            next_depth = depth + 1 if isinstance(child, _BLOCKS) else depth
            max_depth = max(max_depth, next_depth)
            walk(child, next_depth)

    walk(func, 0)
    return complexity, max_depth


def _start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    sides = [test.left, *test.comparators]
    names = {s.id for s in sides if isinstance(s, ast.Name)}
    consts = {s.value for s in sides if isinstance(s, ast.Constant)}
    return "__name__" in names and "__main__" in consts


def _route_from_decorator(decorator: ast.AST) -> tuple[str, str] | None:
    """`@app.get("/x")` -> ("GET", "/x"); `@bp.route("/y", methods=["POST"])` -> ("POST", "/y")."""
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    method = decorator.func.attr
    if method not in HTTP_METHODS:
        return None
    if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
        return None
    path = decorator.args[0].value
    if not isinstance(path, str) or not (path.startswith("/") or path == ""):
        return None
    verb = method.upper()
    if method in {"route", "api_route"}:
        verb = "GET"
        for keyword in decorator.keywords:
            if keyword.arg == "methods" and isinstance(keyword.value, ast.List | ast.Tuple):
                verbs = [e.value for e in keyword.value.elts if isinstance(e, ast.Constant)]
                if verbs:
                    verb = "/".join(str(v).upper() for v in verbs)
    elif method == "websocket":
        verb = "WS"
    return verb, path or "/"


_SCRIPT_STATEMENTS = (ast.Expr, ast.For, ast.While, ast.With, ast.If, ast.Try)
_MODEL_BASES = {"BaseModel", "Model", "Base", "DeclarativeBase", "SQLModel", "Schema",
                "TypedDict", "NamedTuple", "Document", "Enum", "IntEnum", "StrEnum"}


def _is_model_class(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        name = _dotted(decorator.func if isinstance(decorator, ast.Call) else decorator) or ""
        if name.split(".")[-1] in {"dataclass", "dataclasses.dataclass", "attrs", "define"}:
            return True
    for base in node.bases:
        name = _dotted(base) or ""
        if name.split(".")[-1] in _MODEL_BASES:
            return True
    return False


def _is_empty_handler(handler: ast.ExceptHandler) -> bool:
    body = handler.body
    return all(
        isinstance(s, ast.Pass | ast.Continue)
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        for s in body
    )


def _too_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    name = _dotted(handler.type) or ""
    return name in {"Exception", "BaseException"}


def analyze_python(source: str, lines: list[str] | None = None) -> PythonQuality:
    """Parse once and collect every quality fact. Raises SyntaxError on bad source."""
    tree = ast.parse(source)
    lines = lines if lines is not None else source.splitlines()
    quality = PythonQuality(total_lines=len(lines))
    quality.loc = sum(
        1 for line in lines if line.strip() and not line.strip().startswith("#")
    )
    docstring = ast.get_docstring(tree)
    if docstring:
        quality.module_docstring = docstring.strip()

    for node in ast.walk(tree):
        if isinstance(node, ast.If) and (_dotted(node.test) or "").split(".")[-1] == (
            "TYPE_CHECKING"
        ):
            for inner in node.body:
                for sub_node in ast.walk(inner):
                    if isinstance(sub_node, ast.Import | ast.ImportFrom):
                        quality.type_checking_lines.add(sub_node.lineno)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for sub_node in ast.walk(node):
                if isinstance(sub_node, ast.Import | ast.ImportFrom):
                    quality.deferred_lines.add(sub_node.lineno)

    for statement in tree.body:
        if _is_main_guard(statement):
            quality.has_main_guard = statement.lineno
        elif isinstance(statement, _SCRIPT_STATEMENTS) or (
            isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
        ):
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)):
                quality.top_level_statements += 1
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            factory = (_dotted(statement.value.func) or "").split(".")[-1]
            if factory in APP_FACTORIES:
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        quality.app_objects.append(
                            (target.id, APP_FACTORIES[factory], statement.lineno)
                        )

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualified = f"{prefix}{child.name}"
                complexity, nesting = _complexity(child)
                params = child.args
                count = len(params.posonlyargs) + len(params.args) + len(params.kwonlyargs)
                if in_class and count and params.args and params.args[0].arg in {"self", "cls"}:
                    count -= 1
                quality.functions.append(
                    FunctionMetrics(
                        qualified_name=qualified,
                        start_line=_start(child),
                        end_line=child.end_lineno or child.lineno,
                        complexity=complexity,
                        max_nesting=nesting,
                        params=count,
                        is_public=not child.name.startswith("_"),
                        has_docstring=ast.get_docstring(child) is not None,
                    )
                )
                for default in [*params.defaults, *[d for d in params.kw_defaults if d]]:
                    if isinstance(default, ast.List | ast.Dict | ast.Set) or (
                        isinstance(default, ast.Call)
                        and (_dotted(default.func) or "") in {"list", "dict", "set"}
                    ):
                        quality.smells.append(
                            Smell("mutable_default", default.lineno,
                                  default.end_lineno or default.lineno,
                                  f"{qualified}() has a mutable default argument")
                        )
                for decorator in child.decorator_list:
                    route = _route_from_decorator(decorator)
                    if route:
                        quality.routes.append(Route(route[0], route[1], qualified,
                                                    decorator.lineno))
                visit(child, f"{qualified}.", False)
            elif isinstance(child, ast.ClassDef):
                qualified = f"{prefix}{child.name}"
                methods = sum(
                    isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) for n in child.body
                )
                quality.classes.append(
                    (qualified, _start(child), child.end_lineno or child.lineno, methods)
                )
                if _is_model_class(child):
                    quality.model_classes += 1
                visit(child, f"{qualified}.", True)
            else:
                if isinstance(child, ast.ExceptHandler) and _too_broad(child):
                    if _is_empty_handler(child):
                        quality.smells.append(
                            Smell("swallowed_exception", child.lineno,
                                  child.end_lineno or child.lineno,
                                  "An exception handler catches everything and does nothing")
                        )
                    elif child.type is None:
                        quality.smells.append(
                            Smell("bare_except", child.lineno, child.lineno,
                                  "A bare `except:` also catches KeyboardInterrupt and SystemExit")
                        )
                if isinstance(child, ast.ImportFrom) and any(a.name == "*" for a in child.names):
                    quality.smells.append(
                        Smell("wildcard_import", child.lineno, child.lineno,
                              f"`from {'.' * child.level}{child.module or ''} import *`")
                    )
                if isinstance(child, ast.Assign | ast.AnnAssign):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    value = child.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        literal = value.value
                        for target in targets:
                            name = _dotted(target) or ""
                            if (
                                SECRET_NAME.search(name.split(".")[-1])
                                and len(literal) >= 8
                                and " " not in literal
                                and not PLACEHOLDER_VALUE.match(literal)
                                and not literal.startswith(("http://", "https://", "/"))
                            ):
                                quality.smells.append(
                                    Smell("hardcoded_secret", child.lineno, child.lineno,
                                          f"`{name}` is assigned a literal that looks like a "
                                          "credential")
                                )
                visit(child, prefix, in_class)

    visit(tree, "", False)
    return quality
