"""EvidenceLoop HTTP API and web app host.

Run from the repository root:  uvicorn api.main:app --port 8000
then open http://localhost:8000 (the built React app is served from web/dist).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from analysis.pipeline import AnalysisError
from analysis.understanding.report import ReportError, load_report, run_full_analysis
from core.textio import decode_text
from ingestion.service import IngestionError, ingest_github_repository, ingest_zip_bytes
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

app = FastAPI(title="EvidenceLoop", version="2.0.0",
              description="Evidence-backed repository intelligence")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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

        def progress(stage: str, done: int, total: int, message: str) -> None:
            _update(job, stage=stage, done=done, total=total, message=message)

        run_full_analysis(result["snapshot_id"], progress)
        _update(job, stage="done", message="Analysis complete", finished=time.time())
    except (IngestionError, AnalysisError, ReportError) as exc:
        _update(job, stage="error", error=str(exc), finished=time.time())
    except Exception as exc:  # pragma: no cover - surfaced to the UI instead of a dead job
        _update(job, stage="error", error=f"Unexpected error: {type(exc).__name__}: {exc}",
                finished=time.time())


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
            raise HTTPException(404, "Unknown snapshot")
        snapshot_id = request.snapshot_id
        job = _start(snapshot_id, lambda: {"snapshot_id": snapshot_id})
    elif request.url and request.url.strip():
        url = request.url.strip()
        if not url.startswith("http"):
            url = f"https://github.com/{url.removeprefix('github.com/')}"
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
        raise HTTPException(404, "Unknown snapshot")
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
