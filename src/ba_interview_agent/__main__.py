"""Module executed when running ``python -m ba_interview_agent``."""

from __future__ import annotations

from .cli import run_cli


def main() -> None:
    """Invoke the CLI entry point."""

    run_cli()


if __name__ == "__main__":  # pragma: no cover - runtime hook
    main()
