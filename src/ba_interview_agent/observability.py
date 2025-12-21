"""Tracing helpers for the Business Analyst agent runtime."""

from __future__ import annotations

import logging
import os
from typing import Optional

from agent_framework.observability import setup_observability


_initialized = False


def _should_capture_sensitive_data() -> bool:
    raw = os.getenv("MAF_TRACING_CAPTURE_SENSITIVE", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def initialize_tracing(*, endpoint: Optional[str] = None, enable_sensitive_data: Optional[bool] = None) -> bool:
    """Configure OpenTelemetry tracing for the agent framework runtime."""

    global _initialized
    if _initialized:
        return False

    otlp_endpoint = endpoint or os.getenv("MAF_OTLP_ENDPOINT", "http://localhost:4317").strip()
    if not otlp_endpoint:
        logging.info("Tracing skipped because no OTLP endpoint is configured.")
        return False

    try:
        setup_observability(
            otlp_endpoint=otlp_endpoint,
            enable_sensitive_data=enable_sensitive_data
            if enable_sensitive_data is not None
            else _should_capture_sensitive_data(),
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logging.warning("Tracing initialization failed: %s", exc)
        return False

    _initialized = True
    logging.info("Tracing initialized with OTLP endpoint %s", otlp_endpoint)
    return True
