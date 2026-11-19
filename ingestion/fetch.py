"""Fetch only the files a preflight plan selected, into a snapshot folder published atomically.

Three ways, tried in order:

1. A zipball, when the whole repository is smaller than MAX_ARCHIVE_DOWNLOAD_BYTES: one
   request, then only the selected entries are extracted.
2. `git` with a blobless, sparse, shallow clone: only the selected files' contents are
   downloaded. Hardened: no symlinks, no hooks, no local or ext transports, no submodules, no
   LFS filters, no prompts, a timeout. `.git` is deleted afterwards.
3. One request per file from raw.githubusercontent.com (8 at a time), when git is missing or
   fails.

Whatever fetched them, the files on disk are checked again (paths, case clashes, symlinks,
sizes, exactly the selected set) before the folder is published with one os.replace.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

from core import config
from core.config import (
    DOWNLOAD_CHUNK_SIZE,
    GIT_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    RAW_FETCH_CONCURRENCY,
)
from ingestion import rules
from ingestion.archive import safe_extract_archive
from ingestion.preflight import GITHUB_API, Entry, Plan, github_errors, open_client
from ingestion.rules import IngestionError, safe_relative_path

RAW_HOST = "https://raw.githubusercontent.com"
NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"

Runner = Callable[..., subprocess.CompletedProcess]


# --------------------------------------------------------------------------- git


def git_config_flags() -> list[str]:
    """`-c` settings that apply to every git command, whatever the user's own config says."""
    settings = [
        "core.symlinks=false",           # a symlink becomes a small text file, never a link
        f"core.hooksPath={NULL_DEVICE}",  # no hook can run
        "protocol.file.allow=never",     # no local-path remotes or submodules
        "protocol.ext.allow=never",      # no `ext::` command transports
        "submodule.recurse=false",
        "core.autocrlf=false",           # files on disk match the blob sizes in the tree
        "core.fsmonitor=false",
        "filter.lfs.smudge=",            # leave LFS pointers alone (no big downloads)
        "filter.lfs.process=",
        "filter.lfs.required=false",
        "core.longpaths=true",
        "advice.detachedHead=false",
    ]
    flags: list[str] = []
    for setting in settings:
        flags += ["-c", setting]
    return flags


def git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",  # never wait for a username or password
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GCM_INTERACTIVE": "never",
        "GIT_LFS_SKIP_SMUDGE": "1",
    })
    return env


def sparse_pattern(path: str) -> str:
    """A non-cone sparse-checkout pattern matching exactly this one file."""
    escaped = "".join("\\" + c if c in "\\*?[" else c for c in path)
    return "/" + escaped


def sparse_checkout_commands(owner: str, name: str, sha: str, workdir: Path) -> list[list[str]]:
    """The git commands, in order. The sparse-checkout one reads its patterns from stdin."""
    url = f"https://github.com/{owner}/{name}.git"
    base = ["git", *git_config_flags()]
    here = [*base, "-C", str(workdir)]
    return [
        [*base, "clone", "--filter=blob:none", "--depth", "1", "--no-checkout", "--no-tags",
         "--no-recurse-submodules", "--", url, str(workdir)],
        # The branch may have moved since the preflight: fetch the exact commit (a no-op when
        # the clone already has it).
        [*here, "fetch", "--depth", "1", "--filter=blob:none", "--no-tags",
         "--no-recurse-submodules", "origin", sha],
        [*here, "sparse-checkout", "set", "--no-cone", "--stdin"],
        [*here, "checkout", "--detach", sha],
    ]


def fetch_with_git(plan: Plan, workdir: Path, run: Runner = subprocess.run,
                   timeout: int = GIT_TIMEOUT_SECONDS) -> None:
    assert plan.owner and plan.commit_sha
    patterns_file = workdir.parent / f"{workdir.name}.sparse"
    patterns_file.write_text("".join(sparse_pattern(e.path) + "\n" for e in plan.selected),
                             encoding="utf-8")
    try:
        for command in sparse_checkout_commands(plan.owner, plan.name, plan.commit_sha,
                                                workdir):
            with patterns_file.open("rb") as patterns:
                stdin = patterns if "sparse-checkout" in command else subprocess.DEVNULL
                try:
                    run(command, stdin=stdin, capture_output=True, timeout=timeout,
                        env=git_env(), check=True)
                except subprocess.TimeoutExpired as exc:
                    raise IngestionError("git took too long.") from exc
                except subprocess.CalledProcessError as exc:
                    detail = (exc.stderr or b"").decode("utf-8", "replace").strip()[-300:]
                    raise IngestionError(f"git failed: {detail or exc.returncode}") from exc
                except OSError as exc:
                    raise IngestionError(f"git could not run: {exc}") from exc
    finally:
        patterns_file.unlink(missing_ok=True)
    remove_tree(workdir / ".git")


def remove_tree(path: Path) -> None:
    """rmtree that also removes read-only files (git's packs on Windows)."""
    def make_writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=make_writable)


# --------------------------------------------------------------------------- raw files


def fetch_raw(plan: Plan, workdir: Path, transport: httpx.BaseTransport | None = None,
              concurrency: int = RAW_FETCH_CONCURRENCY) -> None:
    """Download each selected file from raw.githubusercontent.com, checking its size against
    the tree's (exact for a blob) and the per-file limit while it streams."""
    assert plan.owner and plan.commit_sha
    base = f"{RAW_HOST}/{plan.owner}/{plan.name}/{plan.commit_sha}"
    limit = rules.MAX_FILE_ANALYSIS_BYTES

    def one(client: httpx.Client, entry: Entry) -> None:
        target = workdir.joinpath(*entry.path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with client.stream("GET", f"{base}/{quote(entry.path, safe='/')}") as response:
            response.raise_for_status()
            with target.open("xb") as out:
                for chunk in response.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                    written += len(chunk)
                    if written > entry.size or written > limit:
                        raise IngestionError(f"{entry.path} is larger than GitHub said.")
                    out.write(chunk)
        if written != entry.size:
            raise IngestionError(f"{entry.path} was cut short ({written} of {entry.size} bytes).")

    with github_errors(), httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                                       headers={"User-Agent": "EvidenceLoop-Local"},
                                       transport=transport) as client:
        # Folders first, from this thread, so workers never race to create them.
        for entry in plan.selected:
            workdir.joinpath(*entry.path.split("/")).parent.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            for future in [pool.submit(one, client, e) for e in plan.selected]:
                future.result()


# --------------------------------------------------------------------------- zipball


def fetch_zipball(plan: Plan, workdir: Path, transport: httpx.BaseTransport | None = None) -> None:
    """Download the commit's zipball to a temp file (aborting past the download limit) and
    extract only the selected files."""
    assert plan.owner and plan.commit_sha
    url = f"{GITHUB_API}/repos/{plan.owner}/{plan.name}/zipball/{plan.commit_sha}"
    archive_path = workdir.parent / f"{workdir.name}.zip"
    limit = config.MAX_ARCHIVE_DOWNLOAD_BYTES
    try:
        with github_errors(), open_client(transport) as client, \
                client.stream("GET", url) as response:
            response.raise_for_status()
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > limit:
                raise IngestionError("The zipball is bigger than expected.")
            received = 0
            with archive_path.open("wb") as out:
                for chunk in response.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                    received += len(chunk)
                    if received > limit:
                        raise IngestionError("The zipball is bigger than expected.")
                    out.write(chunk)
        safe_extract_archive(archive_path, workdir, only={e.path for e in plan.selected})
    finally:
        archive_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- check & publish


def verify_files(root: Path, expected: list[Entry]) -> dict[str, tuple[int, str]]:
    """Walk what was written and check it again: safe paths, no case clashes, no symlinks,
    exactly the expected files, sizes within limits. Returns path -> (size, sha256)."""
    wanted = {e.path for e in expected}
    seen: set[str] = set()
    found: dict[str, tuple[int, str]] = {}
    total = 0
    file_cap = 2 * rules.MAX_FILE_ANALYSIS_BYTES  # line-ending changes are the only slack
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(folder, name)
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise IngestionError(f"A link appeared on disk: {name}")
        for name in files:
            relative = Path(folder, name).relative_to(root).as_posix()
            relative = safe_relative_path(relative)
            key = relative.casefold()
            if key in seen:
                raise IngestionError(f"Two files differ only in case: {relative}")
            seen.add(key)
            if relative not in wanted:
                raise IngestionError(f"An unexpected file appeared: {relative}")
            data = Path(folder, name).read_bytes()
            if len(data) > file_cap:
                raise IngestionError(f"{relative} is larger than the limit.")
            total += len(data)
            if total > config.HARD_MAX_SOURCE_BYTES:
                raise IngestionError("The fetched files exceed the total size limit.")
            found[relative] = (len(data), hashlib.sha256(data).hexdigest())
    missing = wanted - found.keys()
    if missing:
        raise IngestionError(f"{len(missing)} selected files did not arrive, "
                             f"for example {sorted(missing)[0]}.")
    return found


def manifest_digest(hashes: dict[str, tuple[int, str]]) -> str:
    digest = hashlib.sha256()
    for path in sorted(hashes):
        digest.update(f"{path}\0{hashes[path][1]}\n".encode())
    return digest.hexdigest()


def _empty(folder: Path) -> None:
    remove_tree(folder)
    folder.mkdir()


def fetch_plan(plan: Plan, destination: Path, *, transport: httpx.BaseTransport | None = None,
               run: Runner = subprocess.run, git: str | None | bool = True,
               log: Callable[[str], None] = lambda _: None) -> tuple[list[dict], str, str]:
    """Fetch the plan's selected files into `destination` (atomically). Returns the coverage
    records, the method that worked and a digest of what was fetched. `git=True` looks git up
    on PATH; None or False skips it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise IngestionError("Snapshot destination already exists.")
    if git is True:
        git = shutil.which("git")
    workdir = Path(tempfile.mkdtemp(prefix=".evidenceloop-", dir=destination.parent))
    attempts: list[tuple[str, Callable[[], None]]] = []
    if plan.repo_bytes < config.MAX_ARCHIVE_DOWNLOAD_BYTES:
        attempts.append(("zipball", lambda: fetch_zipball(plan, workdir, transport)))
    if git:
        attempts.append(("git", lambda: fetch_with_git(plan, workdir, run)))
    attempts.append(("raw", lambda: fetch_raw(plan, workdir, transport)))

    try:
        failures: list[str] = []
        for method, attempt in attempts:
            try:
                attempt()
                hashes = verify_files(workdir, plan.selected)
                break
            except IngestionError as exc:
                failures.append(f"{method}: {exc}")
                log(f"{method} failed ({exc}); trying the next way")
                _empty(workdir)
        else:
            raise IngestionError("Couldn't fetch the repository's files. "
                                 + failures[-1].split(": ", 1)[-1])
        os.replace(workdir, destination)
    except Exception:
        remove_tree(workdir)
        raise

    records = plan.records()
    for record in records:
        if record["relative_path"] in hashes:
            record["size_bytes"], record["content_sha256"] = hashes[record["relative_path"]]
    return records, method, manifest_digest(hashes)
