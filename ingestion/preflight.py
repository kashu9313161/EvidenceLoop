"""Decide what to analyse in a GitHub repository before downloading any of it.

1. Resolve the default branch to a commit SHA.
2. List every file at that commit with its size (the git tree API; folder by folder when the
   recursive listing is truncated).
3. Classify each path with `ingestion.rules`, then apply the scope and the source limits.

The result is a `Plan`: what will be fetched, what is skipped and why, and the top-level folders
with their source-file counts so a person can pick one when the source is too big.
"""

from __future__ import annotations

import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from core import config
from core.config import HTTP_TIMEOUT_SECONDS, MAX_LISTED_ENTRIES
from ingestion import rules
from ingestion.rules import IngestionError, human_bytes, safe_relative_path

GITHUB_API = "https://api.github.com"

# `scope` value meaning "everything, but if it's over the limit pick the most important files".
PRIORITY = "*"

MANIFEST_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile", "package.json",
    "tsconfig.json", "dockerfile", "docker-compose.yml", "docker-compose.yaml", "makefile",
    "environment.yml", "manage.py",
}
ENTRY_STEMS = {"main", "app", "cli", "manage", "__main__", "wsgi", "asgi", "server", "index"}


class NeedsScope(IngestionError):
    """The repository's source is over the limits: the person picks a folder (or "anyway")."""

    def __init__(self, plan: Plan):
        self.plan = plan
        super().__init__(plan.describe())


@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    kind: str = "file"  # file | symlink | submodule | folder (a folder not listed inside)


@dataclass
class Plan:
    owner: str | None
    name: str
    commit_sha: str | None
    scope: str | None
    entries_listed: int
    repo_bytes: int
    eligible: list[Entry] = field(default_factory=list)  # all source files, before the scope
    selected: list[Entry] = field(default_factory=list)  # what is fetched and analysed
    skipped: list[tuple[str, int, str]] = field(default_factory=list)  # path, size, reason
    status: str = "ok"  # ok | needs_scope
    max_files: int = 0
    max_bytes: int = 0

    @property
    def selected_bytes(self) -> int:
        return sum(e.size for e in self.selected)

    def in_scope(self) -> list[Entry]:
        return [e for e in self.eligible if _within(e.path, self.scope)]

    def folders(self) -> list[dict]:
        """The folders one level below the scope, with their source files: the picker."""
        base = "" if self.scope in (None, PRIORITY) else self.scope
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for entry in self.in_scope():
            rest = entry.path[len(base) + 1:] if base else entry.path
            head = rest.split("/")[0] if "/" in rest else ""
            key = f"{base}/{head}" if base and head else (head or base)
            counts[key][0] += 1
            counts[key][1] += entry.size
        rows = [{"folder": k, "files": n, "bytes": b, "top_level_files": k == base}
                for k, (n, b) in counts.items()]
        return sorted(rows, key=lambda r: (-r["files"], r["folder"]))

    def skipped_by_reason(self) -> list[dict]:
        files: Counter[str] = Counter()
        size: Counter[str] = Counter()
        for _, n, reason in self.skipped:
            files[reason] += 1
            size[reason] += n
        return [{"reason": r, "files": c, "bytes": size[r]} for r, c in files.most_common()]

    def describe(self) -> str:
        """One sentence: "1.4 GB repository, 312 source files (4.1 MB); 21,522 files skipped:
        datasets, model weights, a virtual environment."""
        source = self.in_scope()
        reasons = [r["reason"] for r in self.skipped_by_reason()
                   if r["reason"] not in rules.LEFT_OUT_REASONS][:3]
        text = (f"{human_bytes(self.repo_bytes)} repository, {len(source):,} source files "
                f"({human_bytes(sum(e.size for e in source))})")
        skipped = sum(1 for _, _, r in self.skipped if r not in rules.LEFT_OUT_REASONS)
        if skipped:
            text += f"; {skipped:,} files skipped: " + ", ".join(_plural_reason(r)
                                                                  for r in reasons)
        if self.status == "needs_scope":
            text += (f". That is over the limit of {self.max_files:,} source files or "
                     f"{human_bytes(self.max_bytes)}: pick a folder, or analyse the most "
                     "important files.")
        return text + ("" if text.endswith(".") else ".")

    def to_dict(self) -> dict:
        source = self.in_scope()
        return {
            "status": self.status,
            "scope": self.scope,
            "summary": self.describe(),
            "repo_bytes": self.repo_bytes,
            "total_files": self.entries_listed,
            "source_files": len(source),
            "source_bytes": sum(e.size for e in source),
            "selected_files": len(self.selected),
            "selected_bytes": self.selected_bytes,
            "skipped_by_reason": self.skipped_by_reason(),
            "folders": self.folders()[:60],
            "limits": {"files": self.max_files, "bytes": self.max_bytes},
        }

    def records(self) -> list[dict]:
        """The coverage rows: selected files pending (sha filled in after the fetch), the rest
        skipped with their reason."""
        out = [_record(e.path, e.size, "pending", None) for e in self.selected]
        out += [_record(p, n, "skipped", r) for p, n, r in self.skipped]
        return out


def _plural_reason(reason: str) -> str:
    return {
        "Dataset": "datasets",
        "Model weights": "model weights",
        "Media or document": "media and documents",
        "Archive": "archives",
        "Virtual environment": "a committed virtual environment",
        "Installed dependencies": "installed dependencies",
        "Build output": "build output",
        "Cache folder": "caches",
        "Unsupported file type": "other file types",
    }.get(reason, reason.lower())


def _record(path: str, size: int, status: str, reason: str | None) -> dict:
    return {
        "relative_path": path,
        "extension": PurePosixPath(path).suffix.lower(),
        "size_bytes": size,
        "content_sha256": None,
        "status": status,
        "skip_reason": reason,
    }


def _within(path: str, scope: str | None) -> bool:
    return scope in (None, PRIORITY) or path == scope or path.startswith(scope + "/")


def normalise_scope(scope: str | None) -> str | None:
    if scope is None:
        return None
    scope = scope.strip().strip("/")
    if scope in ("", "."):
        return None
    if scope == PRIORITY:
        return PRIORITY
    return safe_relative_path(scope)


def priority_key(path: str) -> tuple[int, int, str]:
    """Manifests and READMEs first, then entry points, then shallow paths; ties by path."""
    p = PurePosixPath(path)
    name = p.name.lower()
    if name in MANIFEST_NAMES or name.startswith(("readme", "requirements")):
        tier = 0
    elif p.stem.lower() in ENTRY_STEMS:
        tier = 1
    else:
        tier = 2
    return tier, len(p.parts), path


def select_by_priority(entries: list[Entry], max_files: int, max_bytes: int) -> list[Entry]:
    chosen: list[Entry] = []
    total = 0
    for entry in sorted(entries, key=lambda e: priority_key(e.path)):
        if len(chosen) >= max_files:
            break
        if total + entry.size > max_bytes:
            continue  # a smaller file further down may still fit
        chosen.append(entry)
        total += entry.size
    return sorted(chosen, key=lambda e: e.path)


def build_plan(entries: list[Entry], *, owner: str | None = None, name: str = "",
               commit_sha: str | None = None, scope: str | None = None,
               max_files: int | None = None, max_bytes: int | None = None,
               auto_priority: bool = False) -> Plan:
    """Classify `entries` and decide what to fetch. Over the limits, the plan's status is
    "needs_scope" unless the scope is PRIORITY (or `auto_priority`, used for uploads, where
    asking again would mean uploading again), which picks the most important files."""
    scope = normalise_scope(scope)
    max_files = max_files or config.MAX_SOURCE_FILES
    max_bytes = max_bytes or config.MAX_SOURCE_BYTES
    plan = Plan(owner=owner, name=name, commit_sha=commit_sha, scope=scope,
                entries_listed=len(entries), repo_bytes=sum(e.size for e in entries),
                max_files=max_files, max_bytes=max_bytes)
    seen: set[str] = set()
    for entry in entries:
        try:
            path = safe_relative_path(entry.path)
        except IngestionError:
            plan.skipped.append((entry.path, entry.size, rules.UNSAFE_NAME))
            continue
        if entry.kind == "symlink":
            reason: str | None = rules.SYMLINK
        elif entry.kind == "submodule":
            reason = rules.SUBMODULE
        elif entry.kind == "folder":
            reason = rules.ignored_folder_reason(path) or rules.UNSUPPORTED
        else:
            reason = rules.skip_reason(path, entry.size)
        if reason is None:
            key = path.casefold()
            if key in seen:
                reason = rules.CASE_DUPLICATE
            seen.add(key)
        if reason:
            plan.skipped.append((path, entry.size, reason))
        else:
            plan.eligible.append(Entry(path, entry.size))

    plan.eligible.sort(key=lambda e: e.path)
    in_scope = plan.in_scope()
    if scope not in (None, PRIORITY) and not in_scope:
        raise IngestionError(f"There are no source files in {scope}/. Pick another folder.")
    over = len(in_scope) > max_files or sum(e.size for e in in_scope) > max_bytes
    if not over:
        plan.selected = in_scope
    elif scope == PRIORITY or auto_priority:
        plan.selected = select_by_priority(in_scope, max_files, max_bytes)
    else:
        plan.status = "needs_scope"
        return plan

    chosen = {e.path for e in plan.selected}
    for entry in plan.eligible:
        if entry.path not in chosen:
            reason = rules.OUTSIDE_SCOPE if not _within(entry.path, scope) else rules.LEFT_OUT
            plan.skipped.append((entry.path, entry.size, reason))
    return plan


# --------------------------------------------------------------------------- GitHub API


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "EvidenceLoop-Local"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:  # optional: 5,000 API requests an hour instead of 60
        headers["Authorization"] = f"Bearer {token}"
    return headers


def has_token() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN", "").strip())


def open_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                        headers=github_headers(), transport=transport)


def github_refusal(response: httpx.Response, token: bool) -> str:
    """Explain a 403/429 from GitHub: an exhausted hourly limit says when it resets."""
    if response.headers.get("x-ratelimit-remaining") == "0":
        reset = response.headers.get("x-ratelimit-reset", "")
        when = ""
        if reset.isdigit():
            minutes = max(1, round((int(reset) - time.time()) / 60))
            when = f" Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
        advice = "" if token else (
            " Setting a GITHUB_TOKEN environment variable raises the limit from 60 to "
            "5,000 requests an hour.")
        return f"GitHub's hourly request limit is used up.{when}{advice}"
    if response.status_code == 429 or response.headers.get("retry-after"):
        return "GitHub is limiting requests right now. Wait a minute and try again." + (
            "" if token else " Setting a GITHUB_TOKEN environment variable raises the limit.")
    return "GitHub refused access to this repository."


@contextmanager
def github_errors() -> Iterator[None]:
    """Turn httpx failures into IngestionError with a sentence a person can act on."""
    try:
        yield
    except IngestionError:
        raise
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            raise IngestionError("Repository not found, or it is private. Only public "
                                 "repositories can be analysed.") from exc
        if status in (403, 429):
            raise IngestionError(github_refusal(exc.response, has_token())) from exc
        raise IngestionError(
            f"GitHub returned an error (HTTP {status}). Try again in a moment.") from exc
    except httpx.TimeoutException as exc:
        raise IngestionError("GitHub took too long to respond. Try again in a moment.") from exc
    except httpx.TransportError as exc:
        raise IngestionError(
            "Couldn't reach GitHub. Check your internet connection and try again.") from exc
    except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        raise IngestionError(f"GitHub request failed: {exc}") from exc


def _get_json(client: httpx.Client, path: str) -> dict:
    response = client.get(f"{GITHUB_API}{path}")
    response.raise_for_status()
    return response.json()


def resolve_commit(client: httpx.Client, owner: str, name: str) -> tuple[dict, str]:
    """The repository's metadata and the commit SHA its default branch points at."""
    with github_errors():
        metadata = _get_json(client, f"/repos/{owner}/{name}")
        if metadata.get("private") is not False:
            raise IngestionError("Only public repositories are supported.")
        branch = metadata.get("default_branch")
        if not branch:
            raise IngestionError("Default branch not found.")
        # The branch name comes from the repository's owner: encode it, so a `#` or `?` in it
        # can't change which URL is requested.
        commit = _get_json(client, f"/repos/{owner}/{name}/commits/{quote(str(branch), safe='/')}")
        sha = commit.get("sha")
        if not sha or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise IngestionError("GitHub did not return a valid commit SHA.")
        return metadata, sha


def _entries_from(items: list[dict], prefix: str) -> tuple[list[Entry], list[tuple[str, str]]]:
    """Files in one tree listing, and the (path, sha) of subtrees still to list."""
    files: list[Entry] = []
    folders: list[tuple[str, str]] = []
    for item in items:
        path = prefix + str(item.get("path", ""))
        kind = item.get("type")
        mode = str(item.get("mode", ""))
        if kind == "blob":
            entry_kind = "symlink" if mode == "120000" else "file"
            files.append(Entry(path, int(item.get("size") or 0), entry_kind))
        elif kind == "commit":
            files.append(Entry(path, 0, "submodule"))
        elif kind == "tree":
            folders.append((path, str(item.get("sha", ""))))
    return files, folders


def fetch_tree(client: httpx.Client, owner: str, name: str, sha: str) -> list[Entry]:
    """Every path at `sha` with its size. GitHub truncates a recursive listing of a very big
    tree; then the tree is listed folder by folder, without descending into folders that are
    skipped anyway (node_modules, venv): those become one "folder" entry each."""
    base = f"/repos/{owner}/{name}/git/trees"
    with github_errors():
        data = _get_json(client, f"{base}/{sha}?recursive=1")
        if not data.get("truncated"):
            files, _ = _entries_from(data.get("tree", []), "")
            _check_listed(len(files))
            return files

        # Each subfolder is first asked for recursively (one request for most of them); only
        # a subfolder that is itself truncated is listed level by level.
        out: list[Entry] = []
        pending: list[tuple[str, str, bool]] = [("", sha, False)]
        while pending:
            prefix, tree_sha, recursive = pending.pop()
            if prefix and rules.ignored_folder_reason(prefix.rstrip("/")):
                out.append(Entry(prefix.rstrip("/"), 0, "folder"))
                continue
            if recursive:
                listing = _get_json(client, f"{base}/{tree_sha}?recursive=1")
                if not listing.get("truncated"):
                    out += _entries_from(listing.get("tree", []), prefix)[0]
                    _check_listed(len(out))
                    continue
            listing = _get_json(client, f"{base}/{tree_sha}")
            files, folders = _entries_from(listing.get("tree", []), prefix)
            out += files
            pending += [(f"{path}/", child, True) for path, child in folders]
            _check_listed(len(out))
        return sorted(out, key=lambda e: e.path)


def _check_listed(count: int) -> None:
    if count > MAX_LISTED_ENTRIES:
        raise IngestionError(f"This repository lists more than {MAX_LISTED_ENTRIES:,} files, "
                             "which is more than EvidenceLoop can check safely.")


def preflight(client: httpx.Client, owner: str, name: str, sha: str,
              scope: str | None = None) -> Plan:
    entries = fetch_tree(client, owner, name, sha)
    return build_plan(entries, owner=owner, name=name, commit_sha=sha, scope=scope)
