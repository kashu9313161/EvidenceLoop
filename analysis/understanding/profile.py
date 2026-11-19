"""Turn raw facts into understanding: what the stack is, what each file is for, what the
repository says about itself. Deterministic: no LLM, every statement traceable to a file."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# Top-level import / package name -> (display name, category)
STACK_CATALOG: dict[str, tuple[str, str]] = {
    "fastapi": ("FastAPI", "Web"), "flask": ("Flask", "Web"), "django": ("Django", "Web"),
    "starlette": ("Starlette", "Web"), "aiohttp": ("aiohttp", "Web"), "sanic": ("Sanic", "Web"),
    "tornado": ("Tornado", "Web"), "uvicorn": ("Uvicorn", "Web"), "gunicorn": ("Gunicorn", "Web"),
    "werkzeug": ("Werkzeug", "Web"), "jinja2": ("Jinja", "Web"),
    "streamlit": ("Streamlit", "UI"), "gradio": ("Gradio", "UI"), "dash": ("Dash", "UI"),
    "tkinter": ("Tkinter", "UI"), "PyQt5": ("PyQt", "UI"), "PySide6": ("PySide", "UI"),
    "react": ("React", "UI"), "react-dom": ("React", "UI"), "next": ("Next.js", "UI"),
    "vue": ("Vue", "UI"), "svelte": ("Svelte", "UI"), "@angular/core": ("Angular", "UI"),
    "express": ("Express", "Web"), "koa": ("Koa", "Web"), "@nestjs/core": ("NestJS", "Web"),
    "vite": ("Vite", "Tooling"), "tailwindcss": ("Tailwind CSS", "UI"),
    "numpy": ("NumPy", "Data & ML"), "pandas": ("pandas", "Data & ML"),
    "polars": ("Polars", "Data & ML"), "scipy": ("SciPy", "Data & ML"),
    "sklearn": ("scikit-learn", "Data & ML"), "xgboost": ("XGBoost", "Data & ML"),
    "lightgbm": ("LightGBM", "Data & ML"), "catboost": ("CatBoost", "Data & ML"),
    "torch": ("PyTorch", "Data & ML"), "tensorflow": ("TensorFlow", "Data & ML"),
    "keras": ("Keras", "Data & ML"), "jax": ("JAX", "Data & ML"),
    "matplotlib": ("Matplotlib", "Data & ML"), "seaborn": ("seaborn", "Data & ML"),
    "plotly": ("Plotly", "Data & ML"), "joblib": ("joblib", "Data & ML"),
    "mlflow": ("MLflow", "Data & ML"), "cv2": ("OpenCV", "Data & ML"),
    "transformers": ("Transformers", "AI"), "langchain": ("LangChain", "AI"),
    "langgraph": ("LangGraph", "AI"), "openai": ("OpenAI SDK", "AI"),
    "anthropic": ("Anthropic SDK", "AI"), "llama_index": ("LlamaIndex", "AI"),
    "sentence_transformers": ("Sentence Transformers", "AI"), "chromadb": ("Chroma", "AI"),
    "sqlalchemy": ("SQLAlchemy", "Data store"), "sqlite3": ("SQLite", "Data store"),
    "psycopg2": ("PostgreSQL", "Data store"), "psycopg": ("PostgreSQL", "Data store"),
    "pymongo": ("MongoDB", "Data store"), "redis": ("Redis", "Data store"),
    "mongoose": ("MongoDB", "Data store"), "prisma": ("Prisma", "Data store"),
    "@prisma/client": ("Prisma", "Data store"),
    "pydantic": ("Pydantic", "Core"), "httpx": ("HTTPX", "Core"), "requests": ("Requests", "Core"),
    "celery": ("Celery", "Core"), "click": ("Click", "CLI"), "typer": ("Typer", "CLI"),
    "argparse": ("argparse", "CLI"),
    "pytest": ("pytest", "Testing"), "unittest": ("unittest", "Testing"),
    "hypothesis": ("Hypothesis", "Testing"), "jest": ("Jest", "Testing"),
    "vitest": ("Vitest", "Testing"),
}

# import name -> distribution name, where they differ
IMPORT_TO_DIST = {
    "sklearn": "scikit-learn", "cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml",
    "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "jwt": "pyjwt",
    "dateutil": "python-dateutil", "attr": "attrs", "Crypto": "pycryptodome",
    "skimage": "scikit-image", "multipart": "python-multipart", "jose": "python-jose",
    "docx": "python-docx", "pptx": "python-pptx", "fitz": "pymupdf", "serial": "pyserial",
    "OpenSSL": "pyopenssl", "MySQLdb": "mysqlclient", "zmq": "pyzmq", "git": "gitpython",
    "magic": "python-magic", "telegram": "python-telegram-bot", "sentence_transformers":
    "sentence-transformers", "llama_index": "llama-index",
}

STACK_ORDER = ["Web", "UI", "AI", "Data & ML", "Data store", "CLI", "Core", "Testing",
               "Infrastructure", "Tooling"]

ROLE_LABELS = {
    "test": "Test", "docs": "Docs config", "api": "API", "ui": "UI", "entry": "Entry point",
    "config": "Configuration", "model": "Data models", "package": "Package", "core": "Core",
    "script": "Script", "module": "Module", "component": "Component", "build": "Build",
    "stub": "Type stubs",
}


def normalize_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dist_matches(import_name: str, declared: set[str]) -> bool:
    candidates = {normalize_dist(import_name)}
    if import_name in IMPORT_TO_DIST:
        candidates.add(normalize_dist(IMPORT_TO_DIST[import_name]))
    for candidate in candidates:
        if candidate in declared:
            return True
        if any(d.startswith(candidate + "-") or d == candidate + "-binary" for d in declared):
            return True
    return False


@dataclass
class Requirement:
    name: str
    spec: str
    line: int
    pinned: bool


@dataclass
class Manifest:
    path: str
    kind: str  # requirements | pyproject | package.json | setup.py
    requirements: list[Requirement] = field(default_factory=list)
    # Declares a reusable library: a "Libraries" classifier in pyproject.toml, or a
    # package.json with `exports`/`types` (or a `main` on a package that isn't private).
    library: bool = False

    @property
    def directory(self) -> str:
        parent = str(PurePosixPath(self.path).parent)
        return "" if parent == "." else parent

    def is_freeze(self) -> bool:
        """A `pip freeze` dump: many requirements, nearly all pinned exactly."""
        reqs = self.requirements
        return len(reqs) >= 25 and sum(r.pinned for r in reqs) >= 0.9 * len(reqs)

    def names(self) -> set[str]:
        return {normalize_dist(r.name) for r in self.requirements}


_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _line_of(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return 1


def _pep508(entry: str) -> tuple[str, str] | None:
    match = _REQ_LINE.match(entry.split(";")[0])
    if not match:
        return None
    return match.group(1), (match.group(3) or "").strip()


def parse_manifest(path: str, text: str) -> Manifest | None:
    name = PurePosixPath(path).name.lower()
    if name.endswith(".txt") and "requirement" in name:
        manifest = Manifest(path, "requirements")
        for number, raw in enumerate(text.splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith(("-", "git+", "http")):
                continue
            parsed = _pep508(line)
            if parsed:
                dep, spec = parsed
                pinned = "==" in spec or "===" in spec
                manifest.requirements.append(Requirement(dep, spec, number, pinned))
        return manifest
    if name == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return None
        manifest = Manifest(path, "pyproject")
        classifiers = data.get("project", {}).get("classifiers", [])
        manifest.library = any(isinstance(c, str) and "Software Development :: Libraries" in c
                               for c in classifiers)
        entries: list[str] = list(data.get("project", {}).get("dependencies", []))
        for group in data.get("project", {}).get("optional-dependencies", {}).values():
            entries.extend(group)
        for group in data.get("dependency-groups", {}).values():
            entries.extend(e for e in group if isinstance(e, str))
        entries.extend(data.get("build-system", {}).get("requires", []))
        poetry = data.get("tool", {}).get("poetry", {})
        for section in ("dependencies", "dev-dependencies"):
            for dep, spec in poetry.get(section, {}).items():
                if dep.lower() != "python":
                    entries.append(f"{dep}{spec if isinstance(spec, str) else ''}")
        for entry in entries:
            parsed = _pep508(entry)
            if parsed:
                dep, spec = parsed
                manifest.requirements.append(
                    Requirement(dep, spec, _line_of(text, dep), "==" in spec)
                )
        return manifest
    if name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        manifest = Manifest(path, "package.json")
        if isinstance(data, dict):
            manifest.library = bool(
                data.get("exports") or data.get("types") or data.get("typings")
                or (data.get("main") and data.get("private") is not True)
            )
        else:
            return None
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for dep, spec in (data.get(section) or {}).items():
                if isinstance(spec, str):
                    pinned = bool(re.match(r"^\d", spec))
                    manifest.requirements.append(
                        Requirement(dep, spec, _line_of(text, f'"{dep}"'), pinned)
                    )
        return manifest
    if name == "setup.py":
        manifest = Manifest(path, "setup.py")
        block = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.S)
        if block:
            for entry in re.findall(r"['\"]([^'\"]+)['\"]", block.group(1)):
                parsed = _pep508(entry)
                if parsed:
                    manifest.requirements.append(
                        Requirement(parsed[0], parsed[1], _line_of(text, entry),
                                    "==" in parsed[1])
                    )
        return manifest
    return None


_BADGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _clean(text: str) -> str:
    text = _BADGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_`]{1,3}([^*_`]+)[*_`]{1,3}", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def readme_summary(text: str) -> str | None:
    """The first descriptive paragraph of a README (taglines, badges and headings skipped),
    cut at a sentence end."""
    paragraphs: list[str] = []
    current: list[str] = []
    in_code = False
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
        underline = bool(line) and set(line) <= set("=-~^*#")
        is_heading = line.startswith("#") or underline or (
            bool(nxt) and set(nxt) <= set("=-~^*") and len(nxt) >= 3)
        noise = line.startswith(("<", "[!", "![", "|", ">", ".. ", ":", "- ", "* ", "\t"))
        if not line or is_heading or noise or "\t" in raw:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))

    for paragraph in paragraphs:
        cleaned = _clean(paragraph)
        words = cleaned.split()
        if len(words) >= 8 and len(cleaned) >= 50 and not cleaned.startswith("..."):
            if len(cleaned) > 320:
                cut = cleaned[:320]
                end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
                cleaned = cut[: end + 1] if end > 80 else cut.rstrip() + "…"
            return cleaned
    return None


def first_sentence(text: str | None, limit: int = 180) -> str | None:
    if not text:
        return None
    flat = re.sub(r"\s+", " ", text.strip().split("\n\n")[0])
    match = re.match(r"(.+?[.!?])(\s|$)", flat)
    sentence = match.group(1) if match else flat
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


@dataclass
class FileProfile:
    path: str
    language: str
    role: str = "module"
    role_reason: str = "Module"
    description: str = ""
    docstring: str | None = None


def human_list(items: list[str], limit: int = 3) -> str:
    shown = items[:limit]
    rest = len(items) - len(shown)
    if not shown:
        return ""
    if rest > 0:
        return ", ".join(shown) + f" and {rest} more"
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else (many or word + 's')}"


def short_name(path: str) -> str:
    p = PurePosixPath(path)
    if p.stem in {"__init__", "index"} and len(p.parts) > 1:
        return f"{p.parent.name}/{p.name}"
    return p.name
