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
class Parameter:
    name: str
    # positional_only | positional | keyword_only | var_positional (*args) | var_keyword (**kw)
    kind: str
    annotation: str | None = None
    default: str | None = None


@dataclass
class SideEffect:
    # file | network | database | env | subprocess | log
    kind: str
    call: str  # what was called or read, as written: "open", "os.environ", "requests.get"
    line: int


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
    parameters: list[Parameter] = field(default_factory=list)  # without self / cls
    returns: str | None = None  # the return annotation, as written
    return_lines: list[int] = field(default_factory=list)
    raises: list[tuple[str, int]] = field(default_factory=list)  # (exception name, line)
    side_effects: list[SideEffect] = field(default_factory=list)
    docstring: str | None = None
    body_lines: int = 0  # lines of the body, not counting the docstring
    body_start: int = 0  # the `def` line (after decorators)

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


def _deliberate_fallbacks(tree: ast.AST) -> set[int]:
    """Handlers of `try` blocks that only assign variables the code reads afterwards, e.g.

        try:
            ts = parse(raw)
        except Exception:
            pass
        ...
        if ts is None: raise ...

    The failure isn't swallowed: it leaves the variable at its fallback, and the code checks it.
    Returns the ids of those handlers."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.handlers:
            continue
        assigned: set[str] = set()
        for statement in node.body:
            if isinstance(statement, ast.Assign):
                targets = statement.targets
            elif isinstance(statement, ast.AnnAssign | ast.AugAssign):
                targets = [statement.target]
            else:
                assigned = set()
                break
            if not all(isinstance(t, ast.Name) for t in targets):
                assigned = set()
                break
            assigned.update(t.id for t in targets if isinstance(t, ast.Name))
        if not assigned:
            continue
        scope = parents.get(node)
        while scope is not None and not isinstance(
                scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
            scope = parents.get(scope)
        end = node.end_lineno or node.lineno
        if scope is not None and any(
                isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in assigned
                and n.lineno > end for n in ast.walk(scope)):
            found.update(id(handler) for handler in node.handlers)
    return found


# --------------------------------------------------------------------------- function facts

_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
# Path methods only: `rename` and `replace` are left out, since `str.replace` is far more common.
FILE_METHODS = {"read_text", "read_bytes", "write_text", "write_bytes", "unlink", "mkdir",
                "rmdir", "touch"}
FILE_CALLS = {"open", "io.open", "os.open", "codecs.open", "os.remove", "os.unlink", "os.rename",
              "os.replace", "os.makedirs", "os.mkdir", "os.rmdir", "os.listdir", "os.scandir",
              "os.walk", "glob.glob", "tempfile.mkstemp", "tempfile.NamedTemporaryFile"}
NETWORK_PREFIXES = ("requests.", "httpx.", "urllib.request.", "urllib3.", "aiohttp.", "socket.",
                    "http.client.", "smtplib.", "ftplib.", "websockets.")
DATABASE_METHODS = {"execute", "executemany", "executescript", "commit", "rollback",
                    "fetchone", "fetchall", "fetchmany"}
SESSION_METHODS = {"add", "add_all", "query", "delete", "merge", "flush", "scalars", "scalar",
                   "get", "commit", "rollback", "refresh"}
ENV_CALLS = {"os.getenv", "os.putenv", "os.unsetenv", "os.environ.get", "os.environ.setdefault",
             "os.environ.pop", "os.environ.update", "os.environ.copy"}
SUBPROCESS_CALLS = {"os.system", "os.popen", "os.execv", "os.execvp", "os.spawnv",
                    "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
LOGGER_NAMES = {"logger", "log", "_logger", "_log", "LOGGER", "LOG", "logging"}


def import_names(tree: ast.AST) -> dict[str, str]:
    """Local name -> full dotted name, from every import in the file:
    `import subprocess as sp` -> {"sp": "subprocess"}, `from os import getenv` ->
    {"getenv": "os.getenv"}. Used to read `sp.run(...)` as `subprocess.run(...)`."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _full_name(node: ast.AST, imported: dict[str, str]) -> str | None:
    name = _dotted(node)
    if name is None:
        return None
    head, _, rest = name.partition(".")
    if head in imported:
        return imported[head] + (f".{rest}" if rest else "")
    return name


def _side_effect(call: ast.Call, imported: dict[str, str]) -> SideEffect | None:
    """What a call reaches outside the program, judged from its name only."""
    written = _dotted(call.func)
    name = _full_name(call.func, imported) or ""
    attr = call.func.attr if isinstance(call.func, ast.Attribute) else None
    receiver = (_dotted(call.func.value) or "") if isinstance(call.func, ast.Attribute) else ""
    shown = written or (f".{attr}" if attr else "call")
    line = call.lineno
    if name in FILE_CALLS or name.startswith(("shutil.", "pathlib.Path.")) or (
            attr in FILE_METHODS):
        return SideEffect("file", shown, line)
    if name.startswith(NETWORK_PREFIXES) or name.endswith("urlopen"):
        return SideEffect("network", shown, line)
    if name.startswith(("sqlite3.", "psycopg", "pymysql.", "sqlalchemy.create_engine")) or (
            attr in DATABASE_METHODS) or (
            attr in SESSION_METHODS and "session" in receiver.split(".")[-1].lower()):
        return SideEffect("database", shown, line)
    if name in ENV_CALLS or name == "getenv":
        return SideEffect("env", shown, line)
    if name.startswith("subprocess.") or name in SUBPROCESS_CALLS:
        return SideEffect("subprocess", shown, line)
    if name == "print" or name.startswith("logging.") or name.startswith("warnings.warn") or (
            attr in LOG_METHODS and receiver.split(".")[-1] in LOGGER_NAMES):
        return SideEffect("log", shown, line)
    return None


def _own_nodes(func: ast.AST):
    """Every node in a function's body, not descending into nested defs, classes or lambdas."""
    stack = [n for n in reversed(getattr(func, "body", [])) if not isinstance(n, _NESTED)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed([c for c in ast.iter_child_nodes(node)
                               if not isinstance(c, _NESTED)]))


def _parameters(args: ast.arguments, in_class: bool) -> list[Parameter]:
    def text(node: ast.AST | None) -> str | None:
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:  # pragma: no cover - unparse handles every parsed node
            return None

    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.AST | None] = [None] * (len(positional) - len(args.defaults))
    defaults += list(args.defaults)
    out: list[Parameter] = []
    for index, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
        if index == 0 and in_class and arg.arg in {"self", "cls"}:
            continue
        kind = "positional_only" if index < len(args.posonlyargs) else "positional"
        out.append(Parameter(arg.arg, kind, text(arg.annotation), text(default)))
    if args.vararg:
        out.append(Parameter(args.vararg.arg, "var_positional", text(args.vararg.annotation)))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        out.append(Parameter(arg.arg, "keyword_only", text(arg.annotation), text(default)))
    if args.kwarg:
        out.append(Parameter(args.kwarg.arg, "var_keyword", text(args.kwarg.annotation)))
    return out


def _raised_name(node: ast.Raise) -> str | None:
    exc = node.exc
    if exc is None:
        return "(re-raise)"
    if isinstance(exc, ast.Call):
        exc = exc.func
    return _dotted(exc)


def function_facts(node: ast.FunctionDef | ast.AsyncFunctionDef, in_class: bool,
                   imported: dict[str, str]) -> dict:
    """Parameters, return annotation and lines, raised exceptions and side effects of one
    function, from its own body (nested functions and classes are theirs, not its)."""
    returns: list[int] = []
    raises: list[tuple[str, int]] = []
    effects: list[SideEffect] = []
    seen_effects: set[tuple[str, int]] = set()

    def add(effect: SideEffect | None) -> None:
        if effect and (effect.kind, effect.line) not in seen_effects:
            seen_effects.add((effect.kind, effect.line))
            effects.append(effect)

    for child in _own_nodes(node):
        if isinstance(child, ast.Return):
            returns.append(child.lineno)
        elif isinstance(child, ast.Raise):
            name = _raised_name(child)
            if name:
                raises.append((name, child.lineno))
        elif isinstance(child, ast.Call):
            add(_side_effect(child, imported))
        elif isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load) and \
                _full_name(child, imported) in {"os.environ"}:
            add(SideEffect("env", "os.environ", child.lineno))
        elif isinstance(child, ast.Name) and imported.get(child.id) == "os.environ":
            add(SideEffect("env", child.id, child.lineno))

    body = node.body
    docstring = ast.get_docstring(node)
    first = body[1] if docstring and len(body) > 1 else body[0]
    if docstring and len(body) == 1:
        body_lines = 0
    else:
        body_lines = (node.end_lineno or node.lineno) - first.lineno + 1
    return {
        "parameters": _parameters(node.args, in_class),
        "returns": ast.unparse(node.returns) if node.returns is not None else None,
        "return_lines": sorted(returns),
        "raises": raises,
        "side_effects": sorted(effects, key=lambda e: e.line),
        "docstring": docstring.strip() if docstring else None,
        "body_lines": body_lines,
        "body_start": node.lineno,
    }


def _too_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    name = _dotted(handler.type) or ""
    return name in {"Exception", "BaseException"}


def analyze_python(source: str, lines: list[str] | None = None) -> PythonQuality:
    """Parse once and collect every quality fact. Raises SyntaxError on bad source."""
    tree = ast.parse(source)
    fallbacks = _deliberate_fallbacks(tree)
    imported = import_names(tree)
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
                        **function_facts(child, in_class, imported),
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
                if isinstance(child, ast.ExceptHandler) and _too_broad(child) \
                        and id(child) not in fallbacks:
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
