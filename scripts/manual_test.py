"""Manual pipeline runner for local development.

Examples
--------
    python scripts/manual_test.py converter --last-30-days
    python scripts/manual_test.py converter --start 2026-04-01 --end 2026-04-30
    python scripts/manual_test.py converter --fetch-all

Future subcommands (parser, reporter, pipeline) plug in as later phases land.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

# Allow `python scripts/manual_test.py ...` without setting PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings  # noqa: E402
from src.exceptions import JournalReporterError  # noqa: E402
from src.logger import setup_logging  # noqa: E402
from src.modules.converter.schemas import RawEntryCollection  # noqa: E402
from src.modules.converter.service import ConverterService  # noqa: E402
from src.modules.parser.service import ParserService  # noqa: E402


def _add_converter_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("converter", help="Run the Converter module")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--last-30-days", action="store_true", help="Fetch the last 30 days")
    g.add_argument("--last-days", type=int, help="Fetch the last N days")
    g.add_argument("--fetch-all", action="store_true", help="Fetch every entry")
    p.add_argument("--start", type=date.fromisoformat, help="ISO date YYYY-MM-DD")
    p.add_argument("--end", type=date.fromisoformat, help="ISO date YYYY-MM-DD")
    p.add_argument("--out", type=str, help="Write JSON output to this file")


def _add_parser_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("parser", help="Run the Parser module on a JSON input")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", type=str, help="Path to a Converter JSON file")
    g.add_argument("--last-days", type=int, help="Run Converter first, then Parser")
    p.add_argument("--out", type=str, help="Write JSON output to this file")


async def _run_converter(args: argparse.Namespace) -> int:
    service = ConverterService()
    if args.fetch_all:
        result = await service.fetch_all()
    elif args.last_days is not None:
        result = await service.fetch_last_days(days=args.last_days)
    elif args.last_30_days or (args.start is None and args.end is None):
        result = await service.fetch_last_days(days=30)
    elif args.start and args.end:
        result = await service.fetch(args.start, args.end)
    else:
        print("error: provide --start AND --end, or --last-30-days, or --fetch-all", file=sys.stderr)
        return 2

    output = result.model_dump(mode="json")
    rendered = json.dumps(output, indent=2, ensure_ascii=False)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"Wrote {result.count} entries to {args.out}")
    else:
        print(rendered)
    return 0


async def _run_parser(args: argparse.Namespace) -> int:
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            raw = RawEntryCollection.model_validate_json(fh.read())
    else:
        raw = await ConverterService().fetch_last_days(days=args.last_days)

    parsed = ParserService().parse(raw)
    output = parsed.model_dump(mode="json")
    rendered = json.dumps(output, indent=2, ensure_ascii=False)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"Wrote parsed output for {parsed.metadata.entry_count} entries to {args.out}")
    else:
        print(rendered)
    return 0


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Manual pipeline runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_converter_subparser(subparsers)
    _add_parser_subparser(subparsers)
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env == "production":
        print("refusing to run manual_test.py against production config", file=sys.stderr)
        return 2

    try:
        if args.command == "converter":
            return asyncio.run(_run_converter(args))
        if args.command == "parser":
            return asyncio.run(_run_parser(args))
    except JournalReporterError as e:
        print(f"error[{e.code}]: {e.message}", file=sys.stderr)
        if e.detail:
            print(f"  detail: {e.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
