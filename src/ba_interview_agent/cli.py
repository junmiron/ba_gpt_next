"""Command line entry-point for the Business Analyst interview agent."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from typing import Optional

from .config import AppSettings, InterviewScope
from .devui import run_devui
from .interview_agent import run_interview
from .test_agent import run_test_agent_cli
from .transcripts_cli import run_transcripts_cli
from .workflow_visualization import run_workflow_visualization_cli


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ba-interview-agent",
        description=(
            "Interview stakeholders and draft functional specifications"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=[scope.value for scope in InterviewScope],
        help="Interview scope focus (project, process, change_request)",
    )
    parser.add_argument(
        "--devui",
        action="store_true",
        help="Launch the Microsoft Agent Framework DevUI instead of the CLI.",
    )
    parser.add_argument(
        "--devui-host",
        default="127.0.0.1",
        help="Host interface for the DevUI server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--devui-port",
        type=int,
        default=8080,
        help="TCP port for the DevUI server (default: 8080)",
    )
    parser.add_argument(
        "--devui-no-auto-open",
        action="store_true",
        help="Do not automatically launch a browser window for the DevUI.",
    )
    parser.add_argument(
        "--devui-tracing",
        action="store_true",
        help="Enable OpenTelemetry tracing when running the DevUI server.",
    )
    parser.add_argument(
        "--subject-max-questions",
        type=int,
        help=(
            "Maximum number of questions per subject before automatically "
            "moving on. Overrides MAF_SUBJECT_MAX_QUESTIONS."
        ),
    )
    return parser.parse_args(argv)


def run_cli(argv: Optional[list[str]] = None) -> None:
    """Entry-point invoked from ``python -m ba_interview_agent``."""

    arg_list = list(argv) if argv is not None else sys.argv[1:]
    if arg_list:
        command = arg_list[0]
        if command == "transcripts":
            settings = AppSettings.load()
            run_transcripts_cli(settings, arg_list[1:])
            return
        if command in {"simulate", "test"}:
            settings = AppSettings.load()
            run_test_agent_cli(settings, arg_list[1:])
            return
        if command in {"workflow-viz", "workflowviz", "workflow_viz"}:
            settings = AppSettings.load()
            run_workflow_visualization_cli(settings, arg_list[1:])
            return

    args = _parse_args(arg_list)
    settings = AppSettings.load()
    if args.subject_max_questions is not None:
        subject_cap = args.subject_max_questions
        if subject_cap < 1:
            raise SystemExit("--subject-max-questions must be >= 1")
        settings = replace(settings, subject_max_questions=subject_cap)
    if args.devui:
        scopes = None
        if args.scope:
            scopes = [
                InterviewScope.from_string(
                    args.scope,
                    default=settings.default_scope,
                )
            ]
        run_devui(
            settings=settings,
            host=args.devui_host,
            port=args.devui_port,
            auto_open=not args.devui_no_auto_open,
            scopes=scopes,
            tracing_enabled=args.devui_tracing,
        )
        return

    scope = InterviewScope.from_string(
        args.scope,
        default=settings.default_scope,
    )
    asyncio.run(run_interview(settings=settings, scope=scope))


if __name__ == "__main__":  # pragma: no cover - manual execution hook
    run_cli()
