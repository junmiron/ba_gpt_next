"""Configuration helpers for the Business Analyst Interview Agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from pathlib import Path
from typing import Optional


class InterviewScope(str, Enum):
    """Available interview scopes."""

    PROJECT = "project"
    PROCESS = "process"
    CHANGE_REQUEST = "change_request"

    @classmethod
    def from_string(
        cls,
        scope: str | None,
        default: Optional["InterviewScope"] = None,
    ) -> "InterviewScope":
        """Normalize arbitrary user input into a valid scope."""
        if not scope:
            if default is None:
                raise ValueError("Interview scope is required.")
            return default
        normalized = scope.strip().lower().replace(" ", "_")
        for candidate in cls:
            if candidate.value == normalized:
                return candidate
        if default is not None:
            return default
        raise ValueError(f"Unsupported interview scope: {scope}")


@dataclass(slots=True)
class ModelSettings:
    """Holds model-related configuration for the runtime."""

    provider: str
    model: str
    endpoint: Optional[str]
    api_key: str
    api_version: Optional[str]


@dataclass(slots=True)
class AppSettings:
    """Top-level application settings loaded from environment variables."""

    model: ModelSettings
    default_scope: InterviewScope
    output_dir: Path
    transcript_log: Path
    redis_url: Optional[str]
    subject_max_questions: int
    review_max_passes: int

    @classmethod
    def load(cls) -> "AppSettings":
        """Load settings from the environment or .env file."""
        _ensure_dotenv()
        provider = os.getenv("MAF_MODEL_PROVIDER", "azure-openai")
        model = os.getenv("MAF_MODEL")
        if not model:
            raise RuntimeError("MAF_MODEL environment variable is required.")
        endpoint = os.getenv("MAF_MODEL_ENDPOINT")
        api_key = os.getenv("MAF_MODEL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MAF_MODEL_API_KEY environment variable is required."
            )
        api_version = os.getenv("MAF_MODEL_API_VERSION")
        default_scope = InterviewScope.from_string(
            os.getenv("MAF_DEFAULT_SCOPE"),
            default=InterviewScope.PROJECT,
        )
        output_dir = Path(os.getenv("MAF_OUTPUT_DIR", "outputs"))
        output_dir.mkdir(parents=True, exist_ok=True)
        transcript_log = Path(
            os.getenv(
                "MAF_TRANSCRIPT_JSONL",
                os.getenv(
                    "MAF_TRANSCRIPT_DB",
                    str(output_dir / "transcripts.jsonl"),
                ),
            )
        )
        transcript_log.parent.mkdir(parents=True, exist_ok=True)
        redis_url = os.getenv("MAF_REDIS_URL", "redis://localhost:6379/0")
        if redis_url and not redis_url.strip():
            redis_url = None
        max_questions_raw = os.getenv("MAF_SUBJECT_MAX_QUESTIONS", "3")
        try:
            subject_max_questions = int(max_questions_raw)
        except ValueError as exc:
            raise RuntimeError(
                "MAF_SUBJECT_MAX_QUESTIONS must be an integer"
            ) from exc
        if subject_max_questions < 1:
            raise RuntimeError(
                "MAF_SUBJECT_MAX_QUESTIONS must be at least 1"
            )
        review_passes_raw = os.getenv("MAF_REVIEW_MAX_PASSES", "3")
        try:
            review_max_passes = int(review_passes_raw)
        except ValueError as exc:
            raise RuntimeError(
                "MAF_REVIEW_MAX_PASSES must be an integer"
            ) from exc
        if review_max_passes < 1:
            raise RuntimeError("MAF_REVIEW_MAX_PASSES must be at least 1")
        return cls(
            model=ModelSettings(
                provider=provider,
                model=model,
                endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
            ),
            default_scope=default_scope,
            output_dir=output_dir,
            transcript_log=transcript_log,
            redis_url=redis_url,
            subject_max_questions=subject_max_questions,
            review_max_passes=review_max_passes,
        )


def _ensure_dotenv() -> None:
    """Load dotenv variables and provide a helpful error if missing."""

    try:
        dotenv_module = import_module("dotenv")
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "python-dotenv is required. Install with `pip install "
            "python-dotenv`."
        ) from exc

    load_dotenv = getattr(dotenv_module, "load_dotenv")
    load_dotenv()
