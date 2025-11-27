"""Business Analyst Interview Agent package."""

from __future__ import annotations

from typing import Optional

__all__ = ["run_cli"]


def run_cli(argv: Optional[list[str]] = None) -> None:
    """Proxy to :mod:`ba_interview_agent.cli.run_cli` for convenience."""

    from .cli import run_cli as _run_cli_impl

    _run_cli_impl(argv)
