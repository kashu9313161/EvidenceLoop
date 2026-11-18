"""EvidenceLoop command line.

    python -m cli.main serve                      # open the web app on http://127.0.0.1:8000
    python -m cli.main analyze https://github.com/owner/repo   (or a local .zip path)
    python -m cli.main report  "<snapshot_id>"    # headline, structure and issues as text
    python -m cli.main list                       # every analysed repository
"""

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from analysis.pipeline import AnalysisError
from analysis.understanding.report import ReportError, load_report, run_full_analysis
from ingestion.service import IngestionError, ingest_github_repository, ingest_zip_file
from storage.database import get_coverage, get_snapshot, list_snapshots


def _print_report(report: dict) -> None:
    summary = report["summary"]
    print(summary["headline"])
    print()
    print(" ".join(summary["facts"]))
    print()
    for area in report["architecture"]["areas"]:
        print(f"  {area['name']}: {area['description']}")
    print()
    for issue in report["insights"]:
        where = issue["evidence"][0] if issue["evidence"] else None
        location = f"  {where['path']}:{where['start']}" if where else ""
        print(f"[{issue['severity']:<6}] {issue['title']}{location}")


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def port_in_use(host: str, port: int) -> bool:
    bind_host = "0.0.0.0" if host in {"", "0.0.0.0"} else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((bind_host, port))
        except OSError:
            return True
    return False


def _serve(host: str, port: int) -> None:
    import uvicorn

    if port_in_use(host, port):
        raise SystemExit(f"Port {port} is already in use. Is EvidenceLoop already running? "
                         f"Open http://127.0.0.1:{port}, or start on another port with "
                         f"--port {port + 1}.")
    if host not in LOOPBACK:
        # The server must answer to the name people will use to reach it.
        names = {host} if host not in {"", "0.0.0.0", "::"} else {socket.gethostname()}
        os.environ["EVIDENCELOOP_ALLOWED_HOSTS"] = ",".join(sorted(names))
        print("Warning: EvidenceLoop has no login. Anyone who can reach this address can "
              "read every analysed repository.", file=sys.stderr)
    shown = "127.0.0.1" if host in LOOPBACK else host
    print(f"EvidenceLoop is running on http://{shown}:{port}")
    uvicorn.run("api.main:app", host=host, port=port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="evidenceloop",
                                     description="Understand a codebase before you change it.")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run the web app")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--host", default="127.0.0.1")

    analyze = commands.add_parser("analyze", help="Ingest and analyse a repository")
    analyze.add_argument("source", help="GitHub URL, owner/repo, a local .zip, or a snapshot id")
    analyze.add_argument("--json", action="store_true", help="Print the full report as JSON")

    report = commands.add_parser("report", help="Print the analysis of a snapshot")
    report.add_argument("snapshot_id")
    report.add_argument("--json", action="store_true")

    commands.add_parser("list", help="List analysed repositories")

    coverage = commands.add_parser("coverage", help="Per-file analysis status")
    coverage.add_argument("snapshot_id")

    args = parser.parse_args(argv)

    try:
        if args.command == "serve":
            _serve(args.host, args.port)

        elif args.command == "analyze":
            source = args.source
            if get_snapshot(source) is not None:
                snapshot_id = source
            elif source.endswith(".zip"):
                snapshot_id = ingest_zip_file(Path(source))["snapshot_id"]
            else:
                url = source if source.startswith("http") else f"https://github.com/{source}"
                snapshot_id = ingest_github_repository(url)["snapshot_id"]

            def progress(stage: str, done: int, total: int, message: str) -> None:
                if total and stage in {"parse", "measure"}:
                    print(f"\r{stage}: {done}/{total}", end="", file=sys.stderr)

            result = run_full_analysis(snapshot_id, progress)
            print(file=sys.stderr)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Snapshot: {snapshot_id}\n")
                _print_report(result)

        elif args.command == "report":
            found = load_report(args.snapshot_id) or run_full_analysis(args.snapshot_id)
            print(json.dumps(found, indent=2) if args.json else "", end="")
            if not args.json:
                _print_report(found)

        elif args.command == "list":
            for row in list_snapshots():
                found = load_report(row["snapshot_id"])
                headline = found["summary"]["headline"] if found else "(not analysed yet)"
                print(f"{row['snapshot_id']}\n    {headline}")

        elif args.command == "coverage":
            rows = get_coverage(args.snapshot_id)
            for row in rows:
                print(f"{row['status']:10} {row['relative_path']} {row['skip_reason'] or ''}")
            print(f"\nTotal files: {len(rows)}")

    except IngestionError as exc:
        parser.exit(1, f"Could not ingest: {exc}\n")
    except (AnalysisError, ReportError) as exc:
        parser.exit(1, f"Could not analyse: {exc}\n")


if __name__ == "__main__":
    main()
