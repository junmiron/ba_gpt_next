"""Command-line utilities for transcript search and reporting."""

from __future__ import annotations

import argparse
from typing import Callable, List, Optional

from .config import AppSettings, InterviewScope
from .transcript_archive import TranscriptArchive, TranscriptReport

CommandHandler = Callable[[TranscriptArchive, argparse.Namespace], None]


def run_transcripts_cli(
    settings: AppSettings,
    argv: Optional[List[str]] = None,
) -> None:
    """Entry point for transcript-related CLI commands."""

    archive = TranscriptArchive(settings)
    parser = argparse.ArgumentParser(
        prog="ba-interview-agent transcripts",
        description="Search and summarize archived interview transcripts.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    list_parser = subparsers.add_parser(
        "list",
        help="Show recent transcript sessions",
    )
    list_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Maximum number of sessions to display (default: 10)",
    )
    list_parser.add_argument(
        "--scope",
        choices=[scope.value for scope in InterviewScope],
        help="Filter sessions by interview scope",
    )
    list_parser.set_defaults(func=_handle_list)

    show_parser = subparsers.add_parser(
        "show",
        help="Display the full conversation for a session",
    )
    show_parser.add_argument("id", help="Transcript session identifier")
    show_parser.set_defaults(func=_handle_show)

    search_parser = subparsers.add_parser(
        "search",
        help="Find transcripts containing a keyword",
    )
    search_parser.add_argument("query", help="Keyword or phrase to search for")
    search_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=5,
        help="Maximum number of matches to return (default: 5)",
    )
    search_parser.add_argument(
        "--scope",
        choices=[scope.value for scope in InterviewScope],
        help="Filter sessions by interview scope",
    )
    search_parser.set_defaults(func=_handle_search)

    report_parser = subparsers.add_parser(
        "report",
        help="Show aggregate statistics for transcripts",
    )
    report_parser.add_argument(
        "--scope",
        choices=[scope.value for scope in InterviewScope],
        help="Limit the report to a single interview scope",
    )
    report_parser.set_defaults(func=_handle_report)

    args = parser.parse_args(argv)
    handler: CommandHandler = args.func
    handler(archive, args)


def _handle_list(archive: TranscriptArchive, args: argparse.Namespace) -> None:
    scope = _resolve_scope(args.scope)
    records = archive.list(limit=args.limit, scope=scope)
    if not records:
        print("No transcripts found.")
        return
    print(f"Showing {len(records)} transcripts:")
    for record in records:
        print(
            f" - {record.id} | {record.scope.value} | "
            f"{record.created_at.isoformat()} | {record.turn_count} turns"
        )


def _handle_show(archive: TranscriptArchive, args: argparse.Namespace) -> None:
    record = archive.get(args.id)
    if not record:
        print(f"Transcript '{args.id}' not found.")
        return
    print(f"Transcript ID: {record.id}")
    print(f"Scope: {record.scope.value}")
    print(f"Started: {record.created_at.isoformat()}")
    print(f"Turns: {record.turn_count}")
    if record.spec_path:
        print(f"Spec path: {record.spec_path}")
    if record.spec_text:
        print("\nFunctional Specification:\n")
        print(record.spec_text)
    for idx, (question, answer) in enumerate(record.turns, start=1):
        print("\n" + "-" * 40)
        print(f"Turn {idx} question:\n{question}")
        print("\nAnswer:\n" + answer)


def _handle_search(
    archive: TranscriptArchive,
    args: argparse.Namespace,
) -> None:
    scope = _resolve_scope(args.scope)
    matches = archive.search(query=args.query, limit=args.limit, scope=scope)
    if not matches:
        print("No transcripts matched that query.")
        return
    print(f"Found {len(matches)} transcript(s):")
    for record in matches:
        snippet = record.snippet(args.query).strip()
        snippet_display = snippet or "(no snippet available)"
        print(
            f" - {record.id} | {record.scope.value} | "
            f"{record.created_at.isoformat()}\n   {snippet_display}"
        )


def _handle_report(
    archive: TranscriptArchive,
    args: argparse.Namespace,
) -> None:
    scope = _resolve_scope(args.scope)
    report = archive.report(scope=scope)
    if report.total_transcripts == 0:
        print("No transcript data available.")
        return
    _print_report(report)


def _resolve_scope(value: Optional[str]) -> Optional[InterviewScope]:
    if not value:
        return None
    return InterviewScope.from_string(value, default=None)


def _print_report(report: TranscriptReport) -> None:
    print("Transcript Summary Report")
    print("=" * 26)
    print(f"Total transcripts: {report.total_transcripts}")
    print(f"Total turns: {report.total_turns}")
    print(f"Average turns per session: {report.average_turns:.1f}")
    if report.first_timestamp and report.latest_timestamp:
        print(
            "Date range: "
            f"{report.first_timestamp.isoformat()} -> "
            f"{report.latest_timestamp.isoformat()}"
        )
    if report.counts_by_scope:
        print("By scope:")
        for scope, count in sorted(
            report.counts_by_scope.items(),
            key=lambda item: item[0].value,
        ):
            print(f" - {scope.value}: {count}")
