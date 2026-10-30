"""`lamor-migrate` - migrate the old LAMOR website (lamor.fer.hr/lamor) into src/content.

    lamor-migrate fetch      download + cache old-site pages and news items (1 req/s)
    lamor-migrate news       parse cached news -> src/content/news, assets, tools/reports/news.md
    lamor-migrate pages      parse cached pages -> projects, equipment, partners, people,
                             theses, pages; assets; tools/reports/pages.md
    lamor-migrate pdfs       download publication/thesis PDFs linked from the old site
    lamor-migrate verify     check that every image/pdf referenced in src/content exists
    lamor-migrate all        fetch + news + pages + pdfs + verify
"""

from __future__ import annotations

import argparse
import sys

from ..common import setup_logging


def cmd_fetch(args: argparse.Namespace) -> int:
    from .fetch import fetch_all

    fetch_all(refresh=args.refresh)
    return 0


def cmd_news(args: argparse.Namespace) -> int:
    from .news import migrate_news

    migrate_news(offline=args.offline)
    return 0


def cmd_pages(args: argparse.Namespace) -> int:
    from .pages import migrate_pages

    migrate_pages(offline=args.offline)
    return 0


def cmd_pdfs(args: argparse.Namespace) -> int:
    from .pdfs import download_pdfs

    download_pdfs(dry_run=args.dry_run, verify_size=args.verify_size)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import verify

    return verify()


def cmd_all(args: argparse.Namespace) -> int:
    args.refresh = getattr(args, "refresh", False)
    args.offline = False
    args.dry_run = False
    args.verify_size = False
    for step in (cmd_fetch, cmd_news, cmd_pages, cmd_pdfs):
        step(args)
    return cmd_verify(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lamor-migrate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download and cache old-site pages and news items")
    p.add_argument("--refresh", action="store_true", help="re-download pages already in the cache")
    p.set_defaults(func=cmd_fetch)

    for name, func, help_ in (
        ("news", cmd_news, "convert cached news items to Markdown"),
        ("pages", cmd_pages, "convert cached static pages to Markdown"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--offline", action="store_true", help="do not download any assets or verify URLs")
        p.set_defaults(func=func)

    p = sub.add_parser("pdfs", help="download PDFs linked from the publication and thesis pages")
    p.add_argument("--dry-run", action="store_true", help="only list what would be downloaded")
    p.add_argument("--verify-size", action="store_true", help="HEAD each existing file and re-download on size mismatch")
    p.set_defaults(func=cmd_pdfs)

    p = sub.add_parser("verify", help="check referenced images/PDFs exist under public/")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("all", help="fetch, news, pages, pdfs, verify")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_all)

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
