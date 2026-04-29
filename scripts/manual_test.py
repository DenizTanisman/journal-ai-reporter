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
from src.modules.parser.schemas import ParsedCollection  # noqa: E402
from src.modules.parser.service import ParserService  # noqa: E402
from src.modules.reporter.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from src.modules.reporter.service import ReporterService  # noqa: E402
from src.modules.reporter.tag_handlers import prepare as prepare_handler  # noqa: E402


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


def _add_reporter_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("reporter", help="Run the Reporter module")
    p.add_argument("--tag", required=True, help="/detail | /todo | /concern | /success | /date{dd.mm.yyyy}")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", type=str, help="Path to a Parser JSON file")
    g.add_argument("--last-days", type=int, help="Run full pipeline (Converter + Parser)")
    p.add_argument("--dry-run", action="store_true", help="Print prompt without calling Gemini")
    p.add_argument("--out", type=str, help="Write JSON output to this file")


def _add_pipeline_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("pipeline", help="Converter + Parser + Reporter end-to-end")
    p.add_argument("--tag", required=True)
    p.add_argument("--last-days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out", type=str)


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


async def _load_parsed_for_reporter(args: argparse.Namespace) -> ParsedCollection:
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            return ParsedCollection.model_validate_json(fh.read())
    raw = await ConverterService().fetch_last_days(days=args.last_days)
    return ParserService().parse(raw)


async def _run_reporter(args: argparse.Namespace) -> int:
    parsed = await _load_parsed_for_reporter(args)
    return await _emit_report(parsed, tag=args.tag, dry_run=args.dry_run, out=args.out)


async def _run_pipeline(args: argparse.Namespace) -> int:
    raw = await ConverterService().fetch_last_days(days=args.last_days)
    parsed = ParserService().parse(raw)
    return await _emit_report(parsed, tag=args.tag, dry_run=args.dry_run, out=args.out)


async def _emit_report(
    parsed: ParsedCollection,
    *,
    tag: str,
    dry_run: bool,
    out: str | None,
) -> int:
    if dry_run:
        handler = prepare_handler(parsed, tag)
        user_prompt = build_user_prompt(handler.template_key, handler.payload_json)
        print("=== SYSTEM ===")
        print(SYSTEM_PROMPT)
        print("=== USER ===")
        print(user_prompt)
        print("=== STATS ===")
        print(f"tag={tag} entry_count={handler.entry_count} range={handler.date_range}")
        return 0

    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_api_key == "your_gemini_key_here":
        print(
            "warning: GEMINI_API_KEY not set — switching to --dry-run automatically",
            file=sys.stderr,
        )
        return await _emit_report(parsed, tag=tag, dry_run=True, out=out)

    report = await ReporterService().generate(parsed, tag)
    payload = {"json": report.model_dump(mode="json"), "markdown": report.raw_markdown}
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"Wrote report to {out}")
    else:
        print(rendered)
    return 0


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Manual pipeline runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_converter_subparser(subparsers)
    _add_parser_subparser(subparsers)
    _add_reporter_subparser(subparsers)
    _add_pipeline_subparser(subparsers)
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
        if args.command == "reporter":
            return asyncio.run(_run_reporter(args))
        if args.command == "pipeline":
            return asyncio.run(_run_pipeline(args))
    except JournalReporterError as e:
        print(f"error[{e.code}]: {e.message}", file=sys.stderr)
        if e.detail:
            print(f"  detail: {e.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
