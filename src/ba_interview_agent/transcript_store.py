"""Persistence utilities for interview transcripts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

import redis
from redis import Redis
from redis.exceptions import RedisError

from .config import InterviewScope

try:  # pragma: no cover - optional dependency path
    from redis.commands.json.path import Path as RedisJsonPath
except ImportError:  # pragma: no cover - fallback when RedisJSON missing
    RedisJsonPath = None

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .interview_agent import InterviewTranscript

logger = logging.getLogger(__name__)


class TranscriptRepository:
    """Persists transcripts to JSONL and mirrors them into Redis."""

    def __init__(self, archive_path: Path, redis_url: Optional[str]) -> None:
        self._archive_path = archive_path
        self._archive_path.parent.mkdir(parents=True, exist_ok=True)
        self._redis_url = redis_url
        self._redis: Optional[Redis] = None

    def _get_redis(self) -> Optional[Redis]:
        if not self._redis_url:
            return None
        if self._redis is None:
            try:
                self._redis = redis.from_url(  # type: ignore[call-overload]
                    self._redis_url,
                    decode_responses=True,
                )
            except RedisError as exc:  # pragma: no cover - network guarded
                logger.warning("Redis connection failed: %s", exc)
                self._redis = None
        return self._redis

    def save_transcript(
        self,
        *,
        transcript: "InterviewTranscript",
        scope: InterviewScope,
        spec_text: str,
        spec_path: Optional[Path] = None,
    ) -> Optional[str]:
        """Persist an interview transcript for archival and retrieval."""
        created_at = datetime.now(timezone.utc)
        session_suffix = uuid4().hex[:6]
        record_id = "sess-{}-{}".format(
            created_at.strftime("%Y%m%d%H%M%S"),
            session_suffix,
        )
        payload = transcript.to_dict()
        record: Dict[str, Any] = {
            "id": record_id,
            "scope": scope.value,
            "created_at": created_at.isoformat(),
            "created_at_ts": created_at.timestamp(),
            "turn_count": len(transcript.turns),
            "spec_path": str(spec_path) if spec_path else None,
            "spec_text": spec_text,
            "transcript": payload,
        }

        json_lines = self._format_jsonl(
            record_id,
            transcript,
            spec_text,
            spec_path,
        )
        with self._archive_path.open("a", encoding="utf-8") as handle:
            for entry in json_lines:
                handle.write(entry + "\n")

        client = self._get_redis()
        if client:
            key = f"transcript:{record_id}"
            record_blob = json.dumps(record, ensure_ascii=False)
            try:
                if RedisJsonPath and hasattr(client, "json"):
                    client.json().set(
                        key,
                        RedisJsonPath.root_path(),
                        record,
                    )
                else:
                    client.set(key, record_blob)
                client.zadd(
                    "transcripts:index",
                    {record_id: record["created_at_ts"]},
                )
                client.hset(  # type: ignore[call-overload]
                    "transcripts:scope",
                    record_id,
                    scope.value,
                )
            except RedisError as exc:  # pragma: no cover - best effort
                logger.warning("Redis persistence failed for %s: %s", key, exc)

        return record_id

    def update_spec_summary(
        self,
        record_id: str,
        *,
        scope: InterviewScope,
        spec_text: str,
        spec_path: Optional[Path] = None,
    ) -> None:
        """Append a refreshed specification summary for an existing session."""

        def _timestamp(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        summary_timestamp = datetime.now(timezone.utc)
        meta: Dict[str, Dict[str, Any]] = {
            "_meta": {
                "session_id": record_id,
                "ts": _timestamp(summary_timestamp),
                "n_records": 1,
                "summary": True,
                "scope": scope.value,
                "spec_path": str(spec_path) if spec_path else None,
            }
        }
        summary_entry: Dict[str, Any] = {
            "speaker": "interviewer",
            "message": spec_text,
            "subject": "Functional Specification",
            "q_index": 0,
            "timestamp": _timestamp(summary_timestamp),
        }

        with self._archive_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
            handle.write(json.dumps(summary_entry, ensure_ascii=False) + "\n")

        client = self._get_redis()
        if not client:
            return

        key = f"transcript:{record_id}"
        record_blob: Dict[str, Any]
        try:
            if RedisJsonPath and hasattr(client, "json"):
                record_blob = client.json().get(key) or {}
                if not isinstance(record_blob, dict):
                    record_blob = {}
                record_blob["spec_text"] = spec_text
                record_blob["spec_path"] = str(spec_path) if spec_path else None
                client.json().set(key, RedisJsonPath.root_path(), record_blob)
            else:
                raw_value = client.get(key)
                if raw_value:
                    if isinstance(raw_value, bytes):
                        decoded = raw_value.decode("utf-8")
                    else:
                        decoded = str(raw_value)
                    try:
                        record_blob = json.loads(decoded)
                        if not isinstance(record_blob, dict):
                            record_blob = {}
                    except json.JSONDecodeError:
                        record_blob = {}
                else:
                    record_blob = {}
                record_blob["spec_text"] = spec_text
                record_blob["spec_path"] = str(spec_path) if spec_path else None
                client.set(key, json.dumps(record_blob, ensure_ascii=False))
        except RedisError as exc:  # pragma: no cover - best effort path
            logger.warning("Redis persistence failed for %s: %s", key, exc)

    @property
    def archive_path(self) -> Path:
        """Return the filesystem path for the JSONL archive."""

        return self._archive_path

    def get_redis_client(self) -> Optional[Redis]:
        """Expose the cached Redis client, if configured."""

        return self._get_redis()

    def _format_jsonl(
        self,
        record_id: str,
        transcript: "InterviewTranscript",
        spec_text: str,
        spec_path: Optional[Path],
    ) -> List[str]:
        def _timestamp(dt: datetime) -> str:
            return (
                dt.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )

        lines: List[str] = []
        for index, turn in enumerate(transcript.turns, start=1):
            now = datetime.now(timezone.utc)
            meta: Dict[str, Dict[str, Any]] = {
                "_meta": {
                    "session_id": record_id,
                    "ts": _timestamp(now),
                    "n_records": 2,
                    "turn": index,
                    "scope": transcript.scope.value,
                }
            }
            lines.append(json.dumps(meta, ensure_ascii=False))

            question: Dict[str, Any] = {
                "speaker": "interviewer",
                "message": turn.question,
                "subject": "",
                "q_index": index,
                "timestamp": _timestamp(now),
            }
            lines.append(json.dumps(question, ensure_ascii=False))

            answer: Dict[str, Any] = {
                "speaker": "user",
                "message": turn.answer or "",
                "subject": "",
                "q_index": index,
                "timestamp": _timestamp(datetime.now(timezone.utc)),
            }
            lines.append(json.dumps(answer, ensure_ascii=False))

        summary_timestamp = datetime.now(timezone.utc)
        summary_meta: Dict[str, Dict[str, Any]] = {
            "_meta": {
                "session_id": record_id,
                "ts": _timestamp(summary_timestamp),
                "n_records": 1,
                "summary": True,
                "scope": transcript.scope.value,
                "spec_path": str(spec_path) if spec_path else None,
            }
        }
        lines.append(json.dumps(summary_meta, ensure_ascii=False))

        summary_entry: Dict[str, Any] = {
            "speaker": "interviewer",
            "message": spec_text,
            "subject": "Functional Specification",
            "q_index": len(transcript.turns) + 1,
            "timestamp": _timestamp(summary_timestamp),
        }
        lines.append(json.dumps(summary_entry, ensure_ascii=False))

        return lines
