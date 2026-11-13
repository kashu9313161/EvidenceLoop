
import argparse
import json
from pathlib import Path

from ingestion.service import (
    IngestionError,
    ingest_github_repository,
    ingest_zip_file,
)
from storage.database import get_coverage, get_snapshot


def main():
    parser = argparse.ArgumentParser(
        prog="evidenceloop",
        description="EvidenceLoop repository ingestion CLI",
    )

    commands = parser.add_subparsers(dest="command", required=True)

    ingest_parser = commands.add_parser("ingest")
    ingest_parser.add_argument("source", help="GitHub URL or local ZIP path")

    coverage_parser = commands.add_parser("coverage")
    coverage_parser.add_argument("snapshot_id")

    show_parser = commands.add_parser("snapshot")
    show_parser.add_argument("snapshot_id")

    args = parser.parse_args()

    try:
        if args.command == "ingest":
            if args.source.startswith("https://"):
                result = ingest_github_repository(args.source)
            else:
                result = ingest_zip_file(Path(args.source))

            print(json.dumps(result, indent=2))

        elif args.command == "coverage":
            rows = get_coverage(args.snapshot_id)

            for row in rows:
                print(
                    f"{row['status']:10} "
                    f"{row['relative_path']} "
                    f"{row['skip_reason'] or ''}"
                )

            print(f"\nTotal files: {len(rows)}")

        elif args.command == "snapshot":
            snapshot = get_snapshot(args.snapshot_id)

            if snapshot is None:
                print("Snapshot not found.")
            else:
                print(json.dumps(dict(snapshot), indent=2))

    except IngestionError as exc:
        parser.exit(1, f"Ingestion error: {exc}\n")


if __name__ == "__main__":
    main()
