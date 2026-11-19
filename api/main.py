"""EvidenceLoop HTTP API and web app host.

Run from the repository root:  uvicorn api.main:app --port 8000
then open http://127.0.0.1:8000 (the built React app is served from web/dist).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from analysis.pipeline import AnalysisError
from analysis.understanding import explain
from analysis.understanding.explain import ExplainError
from analysis.understanding.report import ReportError, load_report, run_full_analysis
from core import config
from core.textio import decode_text
from ingestion.rules import source_rules
from ingestion.service import (
    IngestionError,
    NeedsScope,
    ingest_folder,
    ingest_github_repository,
    ingest_zip_path,
    parse_github_url,
)
from intelligence.ask import AskError, answer_lookup, load_asks
from intelligence.ask import ask as run_ask
from intelligence.evaluators.choice import judge_description
from intelligence.providers import Provider, ProviderError, ai_status, provider_for
from storage.database import (
    delete_snapshot,
    get_connection,
    get_snapshot,
    initialize_database,
    like_literal,
    list_snapshots,
    list_symbols,
    search_symbols,
    snapshot_root,
)

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_BYTES  # a ZIP or a folder, streamed to a temp file
UPLOAD_CHUNK_BYTES = 1024 * 1024
UNKNOWN_REPOSITORY = ("This repository isn't in EvidenceLoop any more. "
                      "Analyse it again from the home page.")

app = FastAPI(title="EvidenceLoop", version="2.0.0",
              description="Evidence-backed repository intelligence")
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}  # `npm run dev` (Vite)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(DEV_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)
# A big repository's report is megabytes of JSON; compressed it is about a tenth.
app.add_middleware(GZipMiddleware, minimum_size=2048)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hosts() -> set[str]:
    """Host names this server answers to. `serve --host <name>` adds its own through
    EVIDENCELOOP_ALLOWED_HOSTS (comma-separated), read per request so tests can set it."""
    extra = os.environ.get("EVIDENCELOOP_ALLOWED_HOSTS", "")
    return LOCAL_HOSTS | {h.strip().lower() for h in extra.split(",") if h.strip()}


def _hostname(value: str) -> str:
    """`127.0.0.1:8000` -> `127.0.0.1`, `[::1]:8000` -> `::1`, `http://x:5173` -> `x`."""
    return (urlsplit(value if "//" in value else f"//{value}").hostname or "").lower()


def trusted_origin(origin: str, host: str) -> bool:
    """The page that sent a request is this app: same scheme-host-port as the server (the
    port matters: another program on localhost:3000 is a different site), or the dev server."""
    if origin in DEV_ORIGINS:
        return True
    parts = urlsplit(origin)
    return parts.scheme in {"http", "https"} and parts.netloc.lower() == host.lower()


# What browsers say about who started a request (Fetch Metadata). Anything but the app's own
# pages ("same-origin") or the user typing the address ("none") was started by another page.
OWN_FETCH_SITES = {"same-origin", "none"}

MAX_BODY_BYTES = 1024 * 1024  # JSON requests are a few hundred bytes; uploads have their own cap
UPLOAD_PATH = "/api/analyze/upload"
FOLDER_UPLOAD_PATH = "/api/analyze/folder"
UPLOAD_PATHS = {UPLOAD_PATH, FOLDER_UPLOAD_PATH}


def _inline_script_hashes(index: Path) -> list[str]:
    """CSP hashes of the inline scripts in the built page (the theme set before first paint), so
    the policy can allow exactly those and no other inline script."""
    try:
        html = index.read_text(encoding="utf-8")
    except OSError:
        return []
    return [f"'sha256-{base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()}'"
            for body in re.findall(r"<script>(.*?)</script>", html, re.S)]


def content_security_policy() -> str:
    scripts = " ".join(["'self'", *_inline_script_hashes(WEB_DIST / "index.html")])
    return "; ".join([
        "default-src 'self'",
        f"script-src {scripts}",
        "style-src 'self' 'unsafe-inline'",  # React and the code highlighter set style attributes
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",  # no other site can put this app in a frame (clickjacking)
    ])


SECURITY_HEADERS = {
    "Content-Security-Policy": content_security_policy(),
    "X-Frame-Options": "DENY",  # the same, for browsers that ignore frame-ancestors
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


async def security_headers(request: Request, call_next):
    """Every response, refusals included: no framing by other sites, a content security
    policy, no content-type sniffing and no referrer. Bodies larger than any real request are
    refused before they are read (the upload routes allow MAX_UPLOAD_BYTES)."""
    length = request.headers.get("content-length", "")
    limit = MAX_UPLOAD_BYTES + MAX_BODY_BYTES if request.url.path in UPLOAD_PATHS \
        else MAX_BODY_BYTES
    if length.isdigit() and int(length) > limit:
        response = JSONResponse({"detail": "The request is too large."}, status_code=413)
    else:
        response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.middleware("http")
async def local_requests_only(request: Request, call_next):
    """This server holds the code of every analysed repository and has no login, so:
    - answer only to its own host names, which stops DNS rebinding (a web page whose domain
      is pointed at 127.0.0.1 reading the API);
    - refuse changes sent from any other page, including other programs on localhost. Browsers
      send a cross-site form upload without asking first, so CORS alone doesn't stop one;
    - refuse API requests a browser marks as started by another page, so an <img> pointing at
      /api/report can't make the server analyse a repository.
    Tools that aren't browsers (curl, scripts) send neither header and are unaffected.
    """
    host = request.headers.get("host", "")
    if _hostname(host) not in allowed_hosts():
        return JSONResponse({"detail": "Unknown host name."}, status_code=421)
    origin = request.headers.get("origin")
    dev = origin in DEV_ORIGINS
    if request.method not in SAFE_METHODS and origin is not None \
            and not trusted_origin(origin, host):
        return JSONResponse({"detail": "Requests from other sites are not accepted."},
                            status_code=403)
    site = request.headers.get("sec-fetch-site")
    if request.url.path.startswith("/api/") and site is not None \
            and site not in OWN_FETCH_SITES and not dev:
        return JSONResponse({"detail": "Requests from other sites are not accepted."},
                            status_code=403)
    return await call_next(request)


# Registered last, so it is the outermost layer and its headers reach every response, the
# host and origin refusals above included.
app.middleware("http")(security_headers)


# --------------------------------------------------------------------------- jobs

STAGES = ["fetch", "parse", "measure", "graph", "insights", "done"]
STAGE_LABELS = {
    "queued": "Waiting to start",
    "fetch": "Fetching and checking the repository",
    "parse": "Reading every file",
    "measure": "Measuring code",
    "graph": "Mapping how files connect",
    "insights": "Looking for problems",
    "done": "Ready",
    "error": "Failed",
    "needs_scope": "Choose what to analyse",
}
FINISHED_STAGES = {"done", "error", "needs_scope"}


@dataclass
class Job:
    id: str
    source: str
    stage: str = "queued"
    label: str = STAGE_LABELS["queued"]
    done: int = 0
    total: int = 0
    message: str = ""
    snapshot_id: str | None = None
    error: str | None = None
    # What the preflight found (sizes, skipped files by reason, folders); for needs_scope, the
    # folders to pick from.
    plan: dict | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _update(job: Job, **changes) -> None:
    with _lock:
        for key, value in changes.items():
            setattr(job, key, value)
        if "stage" in changes:
            job.label = STAGE_LABELS.get(job.stage, job.stage)


def _run(job: Job, ingest) -> None:
    try:
        _update(job, stage="fetch", message="Listing the repository and choosing what to read")
        result = ingest(lambda plan: _update(job, plan=plan, message="Fetching the source files"))
        _update(job, snapshot_id=result["snapshot_id"])
        unchanged = result.get("already_downloaded") and not result.get("reanalyze")
        if unchanged and load_report(result["snapshot_id"]) is not None:
            # Same commit or same ZIP as before, already analysed by this version.
            _update(job, stage="done", message="Already analysed", finished=time.time())
            return

        def progress(stage: str, done: int, total: int, message: str) -> None:
            _update(job, stage=stage, done=done, total=total, message=message)

        run_full_analysis(result["snapshot_id"], progress)
        _update(job, stage="done", message="Analysis complete", finished=time.time())
        _pregenerate_explanations(result["snapshot_id"])
    except NeedsScope as exc:
        _update(job, stage="needs_scope", plan=exc.plan.to_dict(), message=str(exc),
                finished=time.time())
    except (IngestionError, AnalysisError, ReportError) as exc:
        _update(job, stage="error", error=str(exc), finished=time.time())
    except Exception as exc:  # pragma: no cover - surfaced to the UI instead of a dead job
        _update(job, stage="error", error=f"Unexpected error: {type(exc).__name__}: {exc}",
                finished=time.time())


def _pregenerate_explanations(snapshot_id: str) -> None:
    """With an API key, explain the most important files in the background so the first
    clicks are instant. Without one, do nothing."""
    provider = explain.current_provider()
    if provider is not None:
        threading.Thread(target=explain.pregenerate_all, args=(snapshot_id, provider),
                         daemon=True).start()


MAX_RUNNING_JOBS = 4  # analyses at once; each holds a thread, and an upload up to 100 MB
FINISHED_JOB_TTL = 3600  # seconds a finished job's status stays available


def _running_jobs() -> int:
    with _lock:
        return sum(1 for j in _jobs.values() if j.stage not in FINISHED_STAGES)


def _check_capacity() -> None:
    if _running_jobs() >= MAX_RUNNING_JOBS:
        raise HTTPException(429, f"{MAX_RUNNING_JOBS} analyses are already running. Try again "
                                 "when one finishes.")


def _start(source: str, ingest, snapshot_id: str | None = None) -> Job:
    _check_capacity()
    # snapshot_id is known up front for re-runs, so a duplicate request can find this job.
    job = Job(id=uuid.uuid4().hex[:12], source=source, snapshot_id=snapshot_id)
    now = time.time()
    with _lock:
        for old in [k for k, j in _jobs.items()
                    if j.finished is not None and now - j.finished > FINISHED_JOB_TTL]:
            del _jobs[old]  # finished long ago: nobody polls it any more
        _jobs[job.id] = job
    threading.Thread(target=_run, args=(job, ingest), daemon=True).start()
    return job


class AnalyzeRequest(BaseModel):
    url: str | None = None
    snapshot_id: str | None = None
    # A folder of the repository to analyse, or "*": over the limits, take the most important
    # files. Sent after a job ended with "needs_scope".
    scope: str | None = None


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest) -> dict:
    if request.snapshot_id:
        if get_snapshot(request.snapshot_id) is None:
            raise HTTPException(404, UNKNOWN_REPOSITORY)
        snapshot_id = request.snapshot_id
        # An explicit re-run: analyse again even though a report exists.
        job = _start(snapshot_id,
                     lambda _plan: {"snapshot_id": snapshot_id, "reanalyze": True})
    elif request.url and request.url.strip():
        url = request.url.strip()
        if not url.startswith("http"):
            url = f"https://github.com/{url.removeprefix('github.com/')}"
        try:
            owner, name = parse_github_url(url)
            url = f"https://github.com/{owner}/{name}"  # what the progress box names
        except IngestionError:
            pass  # the job reports the error, so the page shows one message either way
        scope = request.scope
        job = _start(url, lambda on_plan: ingest_github_repository(url, scope=scope,
                                                                   on_plan=on_plan))
    else:
        raise HTTPException(422, "Give a GitHub URL (or owner/repo) to analyze.")
    return asdict(job)


def _upload_dir() -> Path:
    folder = config.TEMP_DIR / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _too_large() -> HTTPException:
    limit = MAX_UPLOAD_BYTES // (1024 * 1024)
    return HTTPException(413, f"The upload is larger than {limit:,} MB.")


@app.post("/api/analyze/upload")
async def analyze_upload(file: Annotated[UploadFile, File()]) -> dict:
    """A ZIP, streamed to a temp file (never held in memory); only eligible entries are
    extracted."""
    _check_capacity()  # before reading the upload
    name = PurePosixPath(file.filename or "upload.zip").stem or "upload"
    handle, temp_name = tempfile.mkstemp(suffix=".zip", dir=_upload_dir())
    received = 0
    try:
        with os.fdopen(handle, "wb") as out:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                received += len(chunk)
                if received > MAX_UPLOAD_BYTES:
                    raise _too_large()
                out.write(chunk)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise

    def ingest(_plan):
        try:
            return ingest_zip_path(Path(temp_name), name)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    job = _start(file.filename or "upload.zip", ingest)
    return asdict(job)


@app.post(FOLDER_UPLOAD_PATH)
async def analyze_folder(request: Request) -> dict:
    """A folder from the browser: `path` fields (relative paths) each followed by its `file`.
    The browser already left out what isn't analysed; the server checks every path and file
    again with the same rules before writing anything."""
    _check_capacity()
    form = await request.form(max_files=config.HARD_MAX_SOURCE_FILES,
                              max_fields=config.HARD_MAX_SOURCE_FILES + 10)
    paths = [str(p) for p in form.getlist("path")]
    uploads = [f for f in form.getlist("file") if hasattr(f, "file")]
    if not uploads or len(paths) != len(uploads):
        await form.close()
        raise HTTPException(422, "Send each file with its relative path.")
    total = sum(getattr(f, "size", 0) or 0 for f in uploads)
    if total > MAX_UPLOAD_BYTES:
        await form.close()
        raise _too_large()
    name = str(form.get("name") or "").strip()[:100]
    # Copy the parts out of the request (Starlette closes them when the request ends).
    workdir = Path(tempfile.mkdtemp(dir=_upload_dir()))
    files = []
    try:
        for index, upload in enumerate(uploads):
            target = workdir / str(index)
            with target.open("wb") as out:
                shutil.copyfileobj(upload.file, out, UPLOAD_CHUNK_BYTES)
            files.append((paths[index], target))
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    finally:
        await form.close()

    def ingest(_plan):
        handles = [(path, target.open("rb")) for path, target in files]
        try:
            return ingest_folder(handles, name)
        finally:
            for _, handle in handles:
                handle.close()
            shutil.rmtree(workdir, ignore_errors=True)

    job = _start(name or "folder", ingest)
    return asdict(job)


@app.get("/api/config")
def ingest_config() -> dict:
    """The rules for what is analysed (so a folder upload is filtered in the browser the same
    way) and the limits."""
    return {
        "rules": source_rules(),
        "limits": {"source_files": config.MAX_SOURCE_FILES, "source_bytes": config.MAX_SOURCE_BYTES,
                   "upload_bytes": MAX_UPLOAD_BYTES},
    }


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        return asdict(job)


# --------------------------------------------------------------------------- repositories


@app.get("/api/health")
def health() -> dict:
    return {"status": "healthy", "version": app.version}


@app.get("/api/repos")
def repos() -> list[dict]:
    out = []
    for row in list_snapshots():
        report = load_report(row["snapshot_id"])
        entry = {
            "snapshot_id": row["snapshot_id"], "name": row["name"], "owner": row["owner"],
            "source": row["source_type"], "url": row["source_url"],
            "commit": row["commit_sha"], "created_at": row["created_at"],
            "files": row["total_files"], "analyzed": report is not None,
            "scope": row["scope"],
        }
        if report is not None:
            summary = report["summary"]
            entry.update({
                "headline": summary["headline"], "kind": summary["kind"],
                "languages": summary["languages"], "totals": summary["totals"],
                "stack": [s["name"] for s in summary["stack"]][:6],
            })
        out.append(entry)
    return out


def _require_snapshot(snapshot_id: str):
    snapshot = get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(404, UNKNOWN_REPOSITORY)
    return snapshot


class RemoveRequest(BaseModel):
    snapshot: str


@app.post("/api/repos/remove")
def remove_repo(request: RemoveRequest) -> dict:
    """Delete a repository's analysis from this computer: its rows (report, files, symbols,
    explanations, questions, send log) and its extracted files. Nothing on GitHub changes. A
    POST, so other sites can't send it."""
    _require_snapshot(request.snapshot)
    with _lock:
        running = any(j.snapshot_id == request.snapshot and j.stage not in {"done", "error"}
                      for j in _jobs.values())
    if running:
        raise HTTPException(409, "This repository is still being analysed. Remove it when "
                                 "that finishes.")
    delete_snapshot(request.snapshot)
    return {"removed": request.snapshot}


def _repo_label(snapshot_id: str) -> str:
    """`pallets/click`, or a ZIP's name, for progress messages."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT r.owner, r.name FROM snapshots s JOIN repositories r "
            "ON r.repository_id = s.repository_id WHERE s.snapshot_id = ?",
            (snapshot_id,)).fetchone()
    if row is None or not row["name"]:
        return snapshot_id
    return f"{row['owner']}/{row['name']}" if row["owner"] else row["name"]


@app.get("/api/report")
def report(snapshot: str = Query(...), background: bool = False):
    """The analysis report. One saved by an older version is rebuilt: with `background=1` that
    happens in a job (202 with the job, so the page can show progress; a second request joins
    the same job), otherwise right away."""
    _require_snapshot(snapshot)
    found = load_report(snapshot)
    if found is not None:
        return found
    if background:
        with _lock:
            job = next((j for j in _jobs.values() if j.snapshot_id == snapshot
                        and j.stage not in FINISHED_STAGES), None)
        if job is None:
            job = _start(_repo_label(snapshot),
                         lambda _plan: {"snapshot_id": snapshot, "reanalyze": True},
                         snapshot_id=snapshot)
        return JSONResponse({"rebuilding": True, "job": asdict(job)}, status_code=202)
    try:
        return run_full_analysis(snapshot)
    except (AnalysisError, ReportError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/file")
def file_detail(snapshot: str = Query(...), path: str = Query(...)) -> dict:
    row = _require_snapshot(snapshot)
    with get_connection() as connection:
        record = connection.execute(
            "SELECT relative_path, status, skip_reason, size_bytes FROM files "
            "WHERE snapshot_id = ? AND relative_path = ?", (snapshot, path),
        ).fetchone()
    if record is None:
        raise HTTPException(404, "No such file in this snapshot")
    root = snapshot_root(row)
    content = None
    if root is not None and record["size_bytes"] <= MAX_FILE_BYTES:
        target = (root / path).resolve()
        if target.is_relative_to(root) and target.is_file():
            content = decode_text(target.read_bytes())
    symbols = [
        {"kind": s["kind"], "name": s["qualified_name"], "start": s["start_line"],
         "end": s["end_line"]}
        for s in list_symbols(snapshot, path) if s["kind"] != "module"
    ]
    return {"path": path, "status": record["status"], "reason": record["skip_reason"],
            "size": record["size_bytes"], "content": content, "symbols": symbols}


@app.get("/api/search")
def search(snapshot: str = Query(...), q: str = Query(..., min_length=1, max_length=200)) -> dict:
    _require_snapshot(snapshot)
    initialize_database()
    needle = q.strip()
    terms = [t for t in needle.replace('"', " ").split() if t]
    with get_connection() as connection:
        paths = [r["relative_path"] for r in connection.execute(
            "SELECT relative_path FROM files WHERE snapshot_id = ? AND status != 'skipped' "
            "AND relative_path LIKE ? ESCAPE '\\' ORDER BY length(relative_path) LIMIT 12",
            (snapshot, f"%{like_literal(needle)}%"))]
        content = []
        if terms:
            match = " ".join(f'"{t}"*' for t in terms)
            content = [dict(r) for r in connection.execute(
                "SELECT relative_path, snippet(files_fts, 2, '[[', ']]', ' … ', 12) AS snippet "
                "FROM files_fts WHERE files_fts MATCH ? AND snapshot_id = ? ORDER BY rank "
                "LIMIT 12", (match, snapshot))]
    symbols = [
        {"name": r["qualified_name"], "path": r["path"], "start": r["start_line"],
         "end": r["end_line"]}
        for r in search_symbols(snapshot, needle, limit=15)
    ] if terms else []
    return {"files": paths, "symbols": symbols, "content": content}


# --------------------------------------------------------------------------- ask


def ask_providers() -> tuple[Provider | None, Provider | None]:
    """The models that answer and judge (EL_MODEL_ASK, EL_MODEL_JUDGE). Tests replace this."""
    return provider_for("ask"), provider_for("judge")


class AskRequest(BaseModel):
    snapshot: str
    question: str


@app.post("/api/ask")
def ask_question(request: AskRequest) -> dict:
    """Answer a question with claims that cite checked evidence. Lookups use no AI; open
    questions use the configured provider, within the per-question token and cost limits.
    A POST, so other sites can't spend the key."""
    _require_snapshot(request.snapshot)
    answer, judge = ask_providers()
    try:
        return run_ask(request.snapshot, request.question, answer_provider=answer,
                       judge_provider=judge)
    except AskError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/lookup")
def lookup(snapshot: str = Query(...), q: str = Query(...)) -> dict:
    """A lookup answered from the index as you type: no AI, nothing stored. Anything else
    comes back as {"kind": "analytical"}."""
    _require_snapshot(snapshot)
    try:
        return answer_lookup(snapshot, q) or {"kind": "analytical"}
    except AskError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/asks")
def recent_asks(snapshot: str = Query(...), limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    """The repository's recent questions and answers, newest first."""
    _require_snapshot(snapshot)
    return load_asks(snapshot, limit)


@app.get("/api/ai")
def ai_info() -> dict:
    """Which models answer and judge, for the chat header. Never includes a key."""
    answer, judge = ask_providers()
    if answer is None:
        return {"available": False, "reason": ai_status()[1] or "AI isn't available."}
    return {"available": True, "provider": answer.name, "model": answer.model,
            "judge": judge_description(judge)}


# --------------------------------------------------------------------------- explanations


def _no_ai() -> dict:
    return {"available": False, "reason": ai_status()[1]}


def _file_facts(snapshot: str, path: str) -> dict | None:
    try:
        return explain.file_facts_for(snapshot, path)
    except ExplainError:
        return None


@app.get("/api/explain")
def explanation(snapshot: str = Query(...), path: str = Query(...)) -> dict:
    """The five questions about a file answered from the code (`facts`), plus the cached AI
    explanation if there is one. Never calls the model: writing one costs money, so it takes a
    POST, which other sites can't send."""
    _require_snapshot(snapshot)
    facts = _file_facts(snapshot, path)
    provider = explain.current_provider()
    if provider is None:
        return {**_no_ai(), "facts": facts}
    try:
        found = explain.cached_explanation(snapshot, path, provider.model)
    except ExplainError as exc:
        return {"available": True, "status": "unavailable", "reason": str(exc), "facts": facts}
    return {"available": True, "status": "ready" if found else "missing", "explanation": found,
            "facts": facts}


class ExplainRequest(BaseModel):
    snapshot: str
    path: str
    refresh: bool = False


@app.post("/api/explain")
def write_explanation(request: ExplainRequest) -> dict:
    """Explain a file now (or return the cached explanation), or rewrite it with refresh."""
    _require_snapshot(request.snapshot)
    provider = explain.current_provider()
    if provider is None:
        return _no_ai()
    try:
        found = explain.explain_file(request.snapshot, request.path, provider,
                                     refresh=request.refresh)
    except ExplainError as exc:
        return {"available": True, "status": "unavailable", "reason": str(exc)}
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"available": True, "status": "ready", "explanation": found,
            "facts": _file_facts(request.snapshot, request.path)}


@app.get("/api/explain/function")
def function_explanation(snapshot: str = Query(...), symbol: str = Query(...)) -> dict:
    """A function's card: the code facts, plus the cached AI answer if there is one. Never
    calls the model (writing takes a POST). Without a key the card has the code rows only."""
    _require_snapshot(snapshot)
    provider = explain.current_provider()
    try:
        card = explain.function_card(snapshot, symbol, provider,
                                     reason=ai_status()[1] if provider is None else "")
    except ExplainError as exc:
        return {"available": provider is not None, "status": "unavailable", "reason": str(exc)}
    return {"available": provider is not None, "status": card["ai"]["status"], "card": card}


class FunctionExplainRequest(BaseModel):
    snapshot: str
    symbol: str
    refresh: bool = False


@app.post("/api/explain/function")
def write_function_explanation(request: FunctionExplainRequest) -> dict:
    """Write a function's AI answer now (or return the cached one), or rewrite it."""
    _require_snapshot(request.snapshot)
    provider = explain.current_provider()
    if provider is None:
        return function_explanation(request.snapshot, request.symbol)
    try:
        card = explain.explain_function(request.snapshot, request.symbol, provider,
                                        refresh=request.refresh)
    except ExplainError as exc:
        return {"available": True, "status": "unavailable", "reason": str(exc)}
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"available": True, "status": card["ai"]["status"], "card": card}


# --------------------------------------------------------------------------- web app

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        candidate = (WEB_DIST / full_path).resolve()
        if full_path and candidate.is_relative_to(WEB_DIST) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def home() -> dict:
        return {"project": "EvidenceLoop", "status": "running",
                "hint": "Build the web app: cd web && npm install && npm run build"}
