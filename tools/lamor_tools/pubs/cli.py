"""`lamor-pubs`: CRORIS publications pipeline for the LAMOR website.

    lamor-pubs sync [--full] [--limit N] [--dry-run] [-v]   download raw records into the SQLite DB
    lamor-pubs export [-v]                                  write src/data/publications.json + report.md
    lamor-pubs pdfmap [--refresh] [-v]                      attach old-site PDFs via overrides/extras
"""

from __future__ import annotations

import argparse
import logging
import sys

from lamor_tools.common import setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lamor-pubs", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="download publications from the CRORIS API into the database")
    p_sync.add_argument("--full", action="store_true", help="refetch every publication and event")
    p_sync.add_argument("--limit", type=int, default=None, metavar="N", help="fetch at most N details (testing)")
    p_sync.add_argument("--dry-run", action="store_true", help="work on an in-memory copy; write nothing")
    p_sync.add_argument("-v", "--verbose", action="store_true")

    p_export = sub.add_parser("export", help="write src/data/publications.json and the report (offline)")
    p_export.add_argument("-v", "--verbose", action="store_true")

    p_pdf = sub.add_parser("pdfmap", help="map PDFs from the old website's publication lists to CRORIS records")
    p_pdf.add_argument("--refresh", action="store_true", help="re-download the old pages instead of using the cache")
    p_pdf.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.command == "sync":
        from lamor_tools.pubs.sync import run_sync

        stats = run_sync(full=args.full, limit=args.limit, dry_run=args.dry_run)
        return 1 if stats.errors and stats.errors * 10 > max(stats.fetched, 10) else 0
    if args.command == "export":
        from lamor_tools.pubs.export import run_export

        run_export()
        return 0
    if args.command == "pdfmap":
        from lamor_tools.pubs.pdfmap import run_pdfmap

        run_pdfmap(refresh=args.refresh)
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
