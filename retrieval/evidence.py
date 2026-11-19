"""The evidence package for a question: numbered, hashed, secret-redacted code ranges.

Sources, in order of weight: functions and classes whose names match the question's words,
lines that contain them (full-text index), the imports linking the chosen files to their
neighbours (graph), and issues already found in those files. Each item records its path,
lines and a SHA-256 of the exact text, so a citation can be checked against the file later.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass

from analysis.understanding.explain import redact_secrets
from retrieval.source import SnapshotFiles, range_sha256
from storage.database import get_connection, search_symbols

DEFAULT_MAX_ITEMS = 12
MAX_ITEM_LINES = 60
CONTEXT_LINES = 6
MAX_GRAPH_ITEMS = 2
MAX_ISSUE_ITEMS = 2

STOPWORDS = set("""
a an and are as at be but by can do does did for from has have how i if in into is it its
me my of on or so that the their them then there these this those to was we what when where
which who why will with would you your about any all also each get got just like make more
most need only other out over should some such than use used uses using want way work works
code file files function functions class classes method methods project repo repository app
explain tell show find happens happen handled handle called call calls""".split())


# Files that usually hold credentials rather than code: never sent to an AI provider as
# evidence, whatever the redaction patterns would catch. Code named after secrets
# (secret_manager.py) is not matched; data and config files named after them are.
_SECRET_FILE = re.compile(
    r"(?:^|/)(?:"
    r"\.env(?:\.[^/]*)?|\.npmrc|\.pypirc|\.netrc|\.htpasswd|\.git-credentials"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?"
    r"|[^/]*\.(?:pem|key|p12|pfx|jks|keystore)"
    r"|[^/]*(?:secret|credential|password)s?[^/]*\.(?:json|ya?ml|toml|ini|cfg|conf|env|txt"
    r"|properties|xml)"
    r")$",
    re.IGNORECASE,
)


def is_secret_file(path: str) -> bool:
    return bool(_SECRET_FILE.search(path))


def max_items() -> int:
    try:
        return max(1, int(os.environ.get("EL_MAX_EVIDENCE_ITEMS", "") or DEFAULT_MAX_ITEMS))
    except ValueError:
        return DEFAULT_MAX_ITEMS


@dataclass
class EvidenceItem:
    id: str
    path: str
    start: int
    end: int
    kind: str  # "code", "import" or "issue"
    label: str
    text: str  # what is shown and sent: the lines, with likely secrets redacted
    sha256: str  # of the original lines, to check the citation against the file

    def to_dict(self) -> dict:
        return asdict(self)


def make_item(files: SnapshotFiles, item_id: str, path: str, start: int, end: int, kind: str,
              label: str) -> EvidenceItem | None:
    lines = files.lines(path)
    if not lines:
        return None
    start = max(1, min(start, len(lines)))
    end = max(start, min(end, len(lines), start + MAX_ITEM_LINES - 1))
    text, _ = redact_secrets("\n".join(lines[start - 1:end]))
    return EvidenceItem(item_id, path, start, end, kind, label, text,
                        range_sha256(lines, start, end))


_DELIMITER = re.compile(r"</?\s*evidence\b", re.IGNORECASE)


def format_evidence(items: list[EvidenceItem]) -> str:
    """Evidence for a prompt: each item headed by its id, lines numbered as in the file, and the
    block delimited so repository text can't pass for instructions or close the block early."""
    blocks = []
    for item in items:
        width = len(str(item.end))
        body = "\n".join(f"{item.start + i:>{width}}| {line}"
                         for i, line in enumerate(item.text.split("\n")))
        body = _DELIMITER.sub(lambda m: m.group(0).replace("<", "&lt;"), body)
        blocks.append(f"[{item.id}] {item.path}, lines {item.start}-{item.end} "
                      f"({item.kind}: {item.label})\n{body}")
    return "<evidence>\n" + "\n\n".join(blocks) + "\n</evidence>"


def stem(word: str) -> str:
    """A prefix for full-text search, so "trained", "training" and "trains" all find "train",
    and "dependency" finds "dependencies". Searches match by prefix, so over-trimming is safe."""
    lowered = word.lower()
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= 4:
            lowered = lowered[: -len(suffix)]
            break
    if lowered[-1] in "ey" and len(lowered) >= 5:
        lowered = lowered[:-1]
    return lowered


def keywords(question: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z_][\w]*", question)
    out: list[str] = []
    for word in words:
        if len(word) < 3 or word.lower() in STOPWORDS:
            continue
        # Identifiers (snake_case, CamelCase) are kept as written; plain words are stemmed.
        term = word if ("_" in word or word[1:] != word[1:].lower()) else stem(word)
        if term.lower() not in {t.lower() for t in out}:
            out.append(term)
    return out[:limit]


@dataclass
class _Candidate:
    path: str
    start: int
    end: int
    score: float
    label: str


def _symbols_in(snapshot_id: str, path: str) -> list[tuple[int, int, str]]:
    with get_connection() as connection:
        return [(r["start_line"], r["end_line"], r["qualified_name"]) for r in connection.execute(
            "SELECT start_line, end_line, qualified_name FROM symbols WHERE snapshot_id = ? AND "
            "relative_path = ? AND kind != 'module' ORDER BY start_line", (snapshot_id, path))]


def _window(symbols: list[tuple[int, int, str]], line: int) -> tuple[int, int, str]:
    """The smallest function or class around a line, if it is short enough; else a few lines
    either side."""
    around = [s for s in symbols if s[0] <= line <= s[1] and s[1] - s[0] < MAX_ITEM_LINES]
    if around:
        start, end, name = min(around, key=lambda s: s[1] - s[0])
        return start, end, name
    return max(1, line - CONTEXT_LINES), line + CONTEXT_LINES, ""


def _rarity(connection, snapshot_id: str, term: str) -> float:
    """1.0 for a term few files contain, down to 0.25 for one nearly every file contains
    (like the project's own name)."""
    count = connection.execute(
        "SELECT count(*) FROM files_fts WHERE files_fts MATCH ? AND snapshot_id = ?",
        (f'content : "{term}"*', snapshot_id)).fetchone()[0]
    total = connection.execute("SELECT count(*) FROM files_fts WHERE snapshot_id = ?",
                               (snapshot_id,)).fetchone()[0] or 1
    share = count / total
    return 1.0 if share <= 0.1 else 0.6 if share <= 0.3 else 0.25


def _text_hits(files: SnapshotFiles, terms: list[str], weights: dict[str, float],
               rarity: dict[str, float]) -> list[_Candidate]:
    found: list[_Candidate] = []
    with get_connection() as connection:
        for term in terms:
            # Search file contents only: a folder name in every path would match everything.
            # A wide net, re-weighted below: plain text ranking favours long docs pages.
            rows = connection.execute(
                "SELECT relative_path FROM files_fts WHERE files_fts MATCH ? AND snapshot_id = ? "
                "ORDER BY rank LIMIT 30", (f'content : "{term}"*', files.snapshot_id)).fetchall()
            for row in rows:
                path = row["relative_path"]
                lines = files.lines(path)
                if not lines:
                    continue
                symbols = _symbols_in(files.snapshot_id, path)
                needle = term.lower()
                hits = [n for n, text in enumerate(lines, 1) if needle in text.lower()][:3]
                for line in hits:
                    start, end, name = _window(symbols, line)
                    weight = weights.get(path, 0.6) * rarity[term]
                    if needle in path.lower() and path in weights:
                        weight *= 1.5  # code named after the term (dependencies/utils.py)
                    found.append(_Candidate(path, start, end, weight,
                                            f"{name} mentions {term}" if name else
                                            f"mentions {term}"))
    return found


def _path_hits(files: SnapshotFiles, terms: list[str],
               weights: dict[str, float]) -> list[_Candidate]:
    """Production code whose path names the term (fastapi/dependencies/utils.py for
    "dependency"): specific even when the word itself is everywhere."""
    found: list[_Candidate] = []
    for term in terms:
        needle = term.lower()
        named = sorted(p for p, w in weights.items() if w >= 1.0 and needle in p.lower())[:4]
        for path in named:
            lines = files.lines(path)
            if not lines:
                continue
            symbols = _symbols_in(files.snapshot_id, path)
            hits = [n for n, text in enumerate(lines, 1) if needle in text.lower()][:2] or [1]
            for line in hits:
                start, end, name = _window(symbols, line)
                found.append(_Candidate(path, start, end, 1.5, name or f"in {path}"))
    return found


def _symbol_hits(snapshot_id: str, terms: list[str], weights: dict[str, float],
                 rarity: dict[str, float]) -> list[_Candidate]:
    found: list[_Candidate] = []
    for term in terms:
        for row in search_symbols(snapshot_id, term, limit=10):
            name = row["qualified_name"]
            if "<module>" in name or row["end_line"] - row["start_line"] >= 400:
                continue  # a whole file; the text hits point inside it better
            in_name = term.lower() in name.lower()
            weight = (3.0 if in_name else 1.0) * weights.get(row["path"], 0.6) * rarity[term]
            found.append(_Candidate(row["path"], row["start_line"], row["end_line"], weight,
                                    name))
    return found


def _merge(candidates: list[_Candidate]) -> list[_Candidate]:
    """Add up the score of candidates covering the same lines; keep the best label."""
    merged: list[_Candidate] = []
    for cand in sorted(candidates, key=lambda c: (c.path, c.start, -c.end)):
        for kept in merged:
            if kept.path == cand.path and cand.start <= kept.end and cand.end >= kept.start:
                kept.score += cand.score
                kept.start, kept.end = min(kept.start, cand.start), max(kept.end, cand.end)
                if len(cand.label) < len(kept.label):
                    kept.label = cand.label
                break
        else:
            merged.append(_Candidate(cand.path, cand.start, cand.end, cand.score, cand.label))
    return merged


def build_evidence(snapshot_id: str, question: str, report: dict, files: SnapshotFiles,
                   limit: int | None = None) -> list[EvidenceItem]:
    limit = limit or max_items()
    terms = keywords(question)
    if not terms:
        return []
    # Production code counts most; examples and docs code, tests, and other text (READMEs,
    # config) count less.
    supporting = {p for a in report["architecture"]["areas"] if a.get("supporting")
                  for p in a["files"]}
    weights = {f["path"]: 0.5 if f["is_test"] else 0.6 if f["path"] in supporting else 1.0
               for f in report["files"]}
    with get_connection() as connection:
        rarity = {t: _rarity(connection, snapshot_id, t) for t in terms}
    ranked = sorted(_merge(_symbol_hits(snapshot_id, terms, weights, rarity) +
                           _text_hits(files, terms, weights, rarity) +
                           _path_hits(files, terms, weights)),
                    key=lambda c: (-c.score, c.path, c.start))

    reserve = min(MAX_GRAPH_ITEMS + MAX_ISSUE_ITEMS, max(limit - 4, 0))
    items: list[EvidenceItem] = []
    per_file: dict[str, int] = {}
    for cand in ranked:
        if len(items) >= limit - reserve:
            break
        if per_file.get(cand.path, 0) >= 3 or is_secret_file(cand.path):
            continue  # spread the evidence over several files; never send a secrets file
        item = make_item(files, f"E{len(items) + 1}", cand.path, cand.start, cand.end, "code",
                         cand.label)
        if item:
            items.append(item)
            per_file[cand.path] = per_file.get(cand.path, 0) + 1

    chosen = list(dict.fromkeys(i.path for i in items))[:3]
    for edge in _neighbour_edges(report, chosen)[:MAX_GRAPH_ITEMS]:
        if len(items) >= limit:
            break
        if is_secret_file(edge["source"]):
            continue
        line = min(edge["lines"])
        item = make_item(files, f"E{len(items) + 1}", edge["source"], line, max(edge["lines"]),
                         "import", f"{edge['source']} imports {edge['target']}")
        if item:
            items.append(item)

    for insight in _related_issues(report, chosen)[:MAX_ISSUE_ITEMS]:
        if len(items) >= limit:
            break
        evidence = insight["evidence"][0]
        if is_secret_file(evidence["path"]):
            continue
        item = make_item(files, f"E{len(items) + 1}", evidence["path"], evidence["start"],
                         evidence["end"], "issue", f"Issue: {insight['title']}")
        if item:
            items.append(item)
    return items


def _neighbour_edges(report: dict, paths: list[str]) -> list[dict]:
    """Imports that connect the chosen files to files outside the evidence, most-used first."""
    chosen = set(paths)
    edges = [e for e in report["graph"]["edges"]
             if not e.get("type_only") and e["lines"] and e["kind"] != "tests"
             and ((e["target"] in chosen) != (e["source"] in chosen))]
    fan_in = {f["path"]: f["fan_in"] for f in report["files"]}
    return sorted(edges, key=lambda e: (-fan_in.get(e["target"], 0), e["source"]))


def _related_issues(report: dict, paths: list[str]) -> list[dict]:
    chosen = set(paths)
    return [i for i in report["insights"]
            if i.get("evidence") and i["evidence"][0]["path"] in chosen]
