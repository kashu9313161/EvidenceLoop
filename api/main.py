"""EvidenceLoop HTTP API and web app host.

Run from the repository root:  uvicorn api.main:app --port 8000
then open http://127.0.0.1:8000 (the built React app is served from web/dist).
"""

from __future__ import annotations

import os
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
from core.textio import decode_text
from ingestion.service import (
    IngestionError,
    ingest_github_repository,
    ingest_zip_bytes,
    parse_github_url,
)
from intelligence.ask import AskError, answer_lookup, load_asks
from intelligence.ask import ask as run_ask
from intelligence.providers import Provider, ProviderError, ai_status, provider_for
from storage.database import (
    get_connection,
    get_snapshot,
    initialize_database,
    list_snapshots,
    list_symbols,
    search_symbols,
    snapshot_root,
)

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
UNKNOWN_REPOSITORY = ("This repository isn't in EvidenceLoop any more. "
                      "Analyse it again from the home page.")

app = FastAPI(title="EvidenceLoop", version="2.0.0",
              description="Evidence-backed repository intelligence")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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


@app.middleware("http")
async def local_requests_only(request: Request, call_next):
    """This server holds the code of every analysed repository and has no login, so:
    - answer only to its own host names, which stops DNS rebinding (a web page whose domain
      is pointed at 127.0.0.1 reading the API);
    - refuse changes sent from another site's page. Browsers send a cross-site form upload
      without asking first, so CORS alone doesn't stop one.
    """
    hosts = allowed_hosts()
    if _hostname(request.headers.get("host", "")) not in hosts:
        return JSONResponse({"detail": "Unknown host name."}, status_code=421)
    origin = request.headers.get("origin")
    if request.method not in SAFE_METHODS and origin is not None \
            and _hostname(origin) not in hosts:
        return JSONResponse({"detail": "Requests from other sites are not accepted."},
                            status_code=403)
    return await call_next(request)


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
}


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
        _update(job, stage="fetch", message="Downloading and validating every file")
        result = ingest()
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
        threading.Thread(target=explain.pregenerate, args=(snapshot_id, provider),
                         daemon=True).start()


def _start(source: str, ingest) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], source=source)
    with _lock:
        _jobs[job.id] = job
    threading.Thread(target=_run, args=(job, ingest), daemon=True).start()
    return job


class AnalyzeRequest(BaseModel):
    url: str | None = None
    snapshot_id: str | None = None


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest) -> dict:
    if request.snapshot_id:
        if get_snapshot(request.snapshot_id) is None:
            raise HTTPException(404, UNKNOWN_REPOSITORY)
        snapshot_id = request.snapshot_id
        # An explicit re-run: analyse again even though a report exists.
        job = _start(snapshot_id, lambda: {"snapshot_id": snapshot_id, "reanalyze": True})
    elif request.url and request.url.strip():
        url = request.url.strip()
        if not url.startswith("http"):
            url = f"https://github.com/{url.removeprefix('github.com/')}"
        try:
            owner, name = parse_github_url(url)
            url = f"https://github.com/{owner}/{name}"  # what the progress box names
        except IngestionError:
            pass  # the job reports the error, so the page shows one message either way
        job = _start(url, lambda: ingest_github_repository(url))
    else:
        raise HTTPException(422, "Give a GitHub URL (or owner/repo) to analyze.")
    return asdict(job)


@app.post("/api/analyze/upload")
async def analyze_upload(file: Annotated[UploadFile, File()]) -> dict:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The ZIP is larger than 100 MB.")
    name = PurePosixPath(file.filename or "upload.zip").stem or "upload"
    job = _start(file.filename or "upload.zip", lambda: ingest_zip_bytes(data, name))
    return asdict(job)


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


@app.get("/api/report")
def report(snapshot: str = Query(...)) -> dict:
    _require_snapshot(snapshot)
    found = load_report(snapshot)
    if found is None:
        # Snapshots ingested before this version: analyze now (fast, local, no network).
        try:
            found = run_full_analysis(snapshot)
        except (AnalysisError, ReportError) as exc:
            raise HTTPException(409, str(exc)) from exc
    return found


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
def search(snapshot: str = Query(...), q: str = Query(..., min_length=1)) -> dict:
    _require_snapshot(snapshot)
    initialize_database()
    needle = q.strip()
    terms = [t for t in needle.replace('"', " ").split() if t]
    with get_connection() as connection:
        paths = [r["relative_path"] for r in connection.execute(
            "SELECT relative_path FROM files WHERE snapshot_id = ? AND status != 'skipped' "
            "AND relative_path LIKE ? ORDER BY length(relative_path) LIMIT 12",
            (snapshot, f"%{needle}%"))]
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
            "judge": {"kind": "llm", "provider": judge.name if judge else None,
                      "model": judge.model if judge else None}}


# --------------------------------------------------------------------------- explanations


def _no_ai() -> dict:
    return {"available": False, "reason": ai_status()[1]}


@app.get("/api/explain")
def explanation(snapshot: str = Query(...), path: str = Query(...)) -> dict:
    """The cached AI explanation of a file, if there is one. Never calls the model: writing
    one costs money, so it takes a POST, which other sites can't send."""
    _require_snapshot(snapshot)
    provider = explain.current_provider()
    if provider is None:
        return _no_ai()
    try:
        found = explain.cached_explanation(snapshot, path, provider.model)
    except ExplainError as exc:
        return {"available": True, "status": "unavailable", "reason": str(exc)}
    return {"available": True, "status": "ready" if found else "missing", "explanation": found}


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
    return {"available": True, "status": "ready", "explanation": found}


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
