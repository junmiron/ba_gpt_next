"""Utilities for reading and analyzing stored interview transcripts."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, cast

from redis import Redis
from redis.exceptions import RedisError

from .config import AppSettings, InterviewScope
from .transcript_store import TranscriptRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TranscriptRecord:
    """In-memory representation of a transcript session."""

    id: str
    scope: InterviewScope
    created_at: datetime
    turns: List[Tuple[str, str]]
    spec_text: Optional[str]
    spec_path: Optional[Path]
    initial_prompt: Optional[str]

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def searchable_blob(self) -> str:
        """Return lowercase text used for keyword search."""

        chunks: List[str] = []
        if self.initial_prompt:
            chunks.append(self.initial_prompt)
        for question, answer in self.turns:
            if question:
                chunks.append(question)
            if answer:
                chunks.append(answer)
        if self.spec_text:
            chunks.append(self.spec_text)
        return "\n".join(chunks).lower()

    def snippet(self, needle: str) -> str:
        """Produce a short snippet highlighting the first match."""

        haystack = self.searchable_blob()
        index = haystack.find(needle.lower())
        if index == -1:
            return ""
        window = 80
        start = max(index - window, 0)
        end = index + len(needle) + window
        raw = haystack[start:end]
        return raw.replace("\n", " ")


@dataclass(slots=True)
class TranscriptReport:
    """Aggregated statistics for a collection of transcripts."""

    total_transcripts: int
    total_turns: int
    average_turns: float
    first_timestamp: Optional[datetime]
    latest_timestamp: Optional[datetime]
    counts_by_scope: Dict[InterviewScope, int]


class TranscriptArchive:
    """Abstraction over JSONL and Redis transcript storage."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._repo = TranscriptRepository(
            archive_path=settings.transcript_log,
            redis_url=settings.redis_url,
        )
        self._json_cache: Optional[Dict[str, TranscriptRecord]] = None
        self._json_cache_mtime: Optional[float] = None

    def list(
        self,
        *,
        limit: int = 10,
        scope: Optional[InterviewScope] = None,
    ) -> List[TranscriptRecord]:
        records = list(self._iter_records(scope_filter=scope))
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records[:limit]

    def get(self, record_id: str) -> Optional[TranscriptRecord]:
        client = self._repo.get_redis_client()
        if client:
            record = self._fetch_from_redis(client, record_id)
            if record:
                return record
        cache = self._load_json_cache()
        record = cache.get(record_id)
        if record is not None:
            return record
        self.refresh()
        cache = self._load_json_cache()
        return cache.get(record_id)

    def search(
        self,
        *,
        query: str,
        limit: int = 10,
        scope: Optional[InterviewScope] = None,
    ) -> List[TranscriptRecord]:
        needle = query.lower().strip()
        if not needle:
            return []
        matches: List[TranscriptRecord] = []
        for record in self._iter_records(scope_filter=scope):
            if needle in record.searchable_blob():
                matches.append(record)
            if len(matches) >= limit:
                break
        matches.sort(key=lambda record: record.created_at, reverse=True)
        return matches

    def report(
        self,
        *,
        scope: Optional[InterviewScope] = None,
    ) -> TranscriptReport:
        records = list(self._iter_records(scope_filter=scope))
        if not records:
            return TranscriptReport(
                total_transcripts=0,
                total_turns=0,
                average_turns=0.0,
                first_timestamp=None,
                latest_timestamp=None,
                counts_by_scope={},
            )
        total_turns = sum(record.turn_count for record in records)
        average = total_turns / len(records)
        ordered = sorted(records, key=lambda record: record.created_at)
        counts = Counter(record.scope for record in records)
        return TranscriptReport(
            total_transcripts=len(records),
            total_turns=total_turns,
            average_turns=average,
            first_timestamp=ordered[0].created_at,
            latest_timestamp=ordered[-1].created_at,
            counts_by_scope=dict(counts),
        )

    def append_spec_update(
        self,
        record_id: str,
        *,
        scope: InterviewScope,
        spec_text: str,
        spec_path: Optional[Path] = None,
    ) -> None:
        """Persist a refreshed specification snapshot for an existing session."""

        self._repo.update_spec_summary(
            record_id,
            scope=scope,
            spec_text=spec_text,
            spec_path=spec_path,
        )
        self.refresh()

    def _iter_records(
        self,
        *,
        scope_filter: Optional[InterviewScope],
    ) -> Iterator[TranscriptRecord]:
        client = self._repo.get_redis_client()
        if client:
            yield from self._iter_redis_records(client, scope_filter)
            return
        cache = self._load_json_cache()
        for record in cache.values():
            if scope_filter and record.scope != scope_filter:
                continue
            yield record

    def _iter_redis_records(
        self,
        client: Redis,
        scope_filter: Optional[InterviewScope],
    ) -> Iterator[TranscriptRecord]:
        try:
            ids: List[str] = client.zrevrange(  # type: ignore[call-arg]
                "transcripts:index",
                0,
                -1,
            )
        except RedisError as exc:  # pragma: no cover - redis failure path
            logger.warning("Failed to list Redis transcripts: %s", exc)
            return
        for record_id in ids:
            record = self._fetch_from_redis(client, record_id)
            if not record:
                continue
            if scope_filter and record.scope != scope_filter:
                continue
            yield record

    def _fetch_from_redis(
        self,
        client: Redis,
        record_id: str,
    ) -> Optional[TranscriptRecord]:
        key = f"transcript:{record_id}"
        payload: Optional[Dict[str, object]] = None
        try:
            if hasattr(client, "json"):
                payload = client.json().get(key)  # type: ignore[attr-defined]
            else:
                raw_value = client.get(key)
                if raw_value:
                    if isinstance(raw_value, bytes):
                        decoded = raw_value.decode("utf-8")
                    else:
                        decoded = str(raw_value)
                    payload = json.loads(decoded)
        except (RedisError, json.JSONDecodeError) as exc:  # pragma: no cover
            logger.warning("Failed to retrieve %s from Redis: %s", key, exc)
            return None
        if not payload:
            return None
        return self._record_from_payload(payload)

    def refresh(self) -> None:
        """Reset cached transcript metadata forcing disk reload on next access."""

        self._json_cache = None
        self._json_cache_mtime = None

    def _load_json_cache(self) -> Dict[str, TranscriptRecord]:
        path = self._repo.archive_path
        current_mtime: Optional[float] = None
        if path.exists():
            try:
                current_mtime = path.stat().st_mtime
            except OSError:
                current_mtime = None
        if (
            self._json_cache is not None
            and self._json_cache_mtime is not None
            and current_mtime is not None
            and self._json_cache_mtime >= current_mtime
        ):
            return self._json_cache
        sessions: Dict[str, TranscriptRecord] = {}
        path = self._repo.archive_path
        if not path.exists():
            self._json_cache = {}
            self._json_cache_mtime = None
            return self._json_cache
        with path.open("r", encoding="utf-8") as handle:
            sessions = self._parse_jsonl(handle)
        self._json_cache = sessions
        self._json_cache_mtime = current_mtime
        return sessions

    def _parse_jsonl(
        self,
        handle: Iterable[str],
    ) -> Dict[str, TranscriptRecord]:
        sessions: Dict[str, "_SessionBuilder"] = {}
        current_session: Optional[str] = None
        pending_summary = False
        pending_spec_path: Optional[str] = None
        pending_question: Optional[str] = None
        for raw_line in handle:
            text = raw_line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:  # pragma: no cover - invalid row
                logger.debug("Skipping malformed transcript row: %s", text)
                continue
            if not isinstance(parsed, dict):
                continue
            data = cast(Dict[str, Any], parsed)
            if "id" in data and "transcript" in data:
                record = self._record_from_payload(
                    cast(Dict[str, object], data)
                )
                sessions[record.id] = _SessionBuilder.from_record(record)
                current_session = None
                pending_summary = False
                pending_question = None
                continue
            meta_obj = data.get("_meta")
            if isinstance(meta_obj, dict):
                meta = cast(Dict[str, Any], meta_obj)
                session_reference = meta.get("session_id")
                if not isinstance(session_reference, str):
                    continue
                current_session = session_reference
                builder = sessions.setdefault(
                    current_session,
                    _SessionBuilder(session_id=current_session),
                )
                scope_override = meta.get("scope")
                if isinstance(scope_override, str):
                    builder.scope = scope_override
                timestamp = meta.get("ts")
                if isinstance(timestamp, str):
                    builder.touch_timestamp(timestamp)
                pending_summary = bool(meta.get("summary"))
                spec_override = meta.get("spec_path")
                if isinstance(spec_override, str):
                    pending_spec_path = spec_override
                else:
                    pending_spec_path = None
                pending_question = None
                continue
            if not current_session:
                continue
            builder = sessions[current_session]
            if pending_summary:
                message_value = data.get("message")
                if message_value is not None:
                    builder.spec_text = str(message_value)
                else:
                    builder.spec_text = ""
                builder.spec_path = pending_spec_path
                pending_summary = False
                continue
            speaker_value = data.get("speaker")
            if speaker_value == "interviewer":
                prompt_value = data.get("message")
                pending_question = (
                    str(prompt_value)
                    if prompt_value is not None
                    else ""
                )
            elif speaker_value == "user":
                answer_value = data.get("message")
                builder.add_turn(
                    pending_question or "",
                    str(answer_value) if answer_value is not None else "",
                )
                pending_question = None
        return {key: builder.build() for key, builder in sessions.items()}

    def _record_from_payload(
        self,
        payload: Dict[str, object],
    ) -> TranscriptRecord:
        record_id = str(payload.get("id"))
        scope_value = str(payload.get("scope"))
        scope = InterviewScope.from_string(
            scope_value,
            default=InterviewScope.PROJECT,
        )
        created_at = _parse_timestamp(str(payload.get("created_at")))
        spec_path_value = payload.get("spec_path")
        spec_path = (
            Path(spec_path_value)
            if isinstance(spec_path_value, str)
            else None
        )
        spec_text = payload.get("spec_text")
        transcript_data = payload.get("transcript", {})
        transcript_dict: Dict[str, Any] = {}
        turns_payload: List[Dict[str, Any]] = []
        if isinstance(transcript_data, dict):
            transcript_dict = cast(Dict[str, Any], transcript_data)
            turns_raw = transcript_dict.get("turns")
            if isinstance(turns_raw, list):
                for raw_item in cast(List[Any], turns_raw):
                    if isinstance(raw_item, dict):
                        turns_payload.append(
                            cast(Dict[str, Any], raw_item)
                        )
        turns: List[Tuple[str, str]] = []
        for item in turns_payload:
            question = str(item.get("question", ""))
            answer = str(item.get("answer", ""))
            turns.append((question, answer))
        initial_prompt = None
        if transcript_dict:
            prompt_value = transcript_dict.get("initial_user_prompt")
            if isinstance(prompt_value, str):
                initial_prompt = prompt_value
        return TranscriptRecord(
            id=record_id,
            scope=scope,
            created_at=created_at,
            turns=turns,
            spec_text=str(spec_text) if spec_text is not None else None,
            spec_path=spec_path,
            initial_prompt=initial_prompt,
        )


def _empty_turn_pairs() -> List[Tuple[str, str]]:
    return []


@dataclass(slots=True)
class _SessionBuilder:
    """Helper for assembling legacy JSONL sessions."""

    session_id: str
    scope: Optional[str] = None
    created_at: Optional[datetime] = None
    latest_timestamp: Optional[datetime] = None
    turns: List[Tuple[str, str]] = field(default_factory=_empty_turn_pairs)
    spec_text: Optional[str] = None
    spec_path: Optional[str] = None
    initial_prompt: Optional[str] = None

    @classmethod
    def from_record(cls, record: TranscriptRecord) -> "_SessionBuilder":
        builder = cls(session_id=record.id)
        builder.scope = record.scope.value
        builder.created_at = record.created_at
        builder.latest_timestamp = record.created_at
        builder.turns = list(record.turns)
        builder.spec_text = record.spec_text
        builder.spec_path = str(record.spec_path) if record.spec_path else None
        builder.initial_prompt = record.initial_prompt
        return builder

    def touch_timestamp(self, value: str) -> None:
        timestamp = _parse_timestamp(value)
        if not self.created_at or timestamp < self.created_at:
            self.created_at = timestamp
        if not self.latest_timestamp or timestamp > self.latest_timestamp:
            self.latest_timestamp = timestamp

    def add_turn(self, question: str, answer: str) -> None:
        self.turns.append((question, answer))

    def build(self) -> TranscriptRecord:
        created_at = self.created_at or datetime.fromtimestamp(0)
        scope = InterviewScope.from_string(
            self.scope,
            default=InterviewScope.PROJECT,
        )
        spec_path = Path(self.spec_path) if self.spec_path else None
        return TranscriptRecord(
            id=self.session_id,
            scope=scope,
            created_at=created_at,
            turns=list(self.turns),
            spec_text=self.spec_text,
            spec_path=spec_path,
            initial_prompt=self.initial_prompt,
        )


def _parse_timestamp(value: str) -> datetime:
    cleaned = value
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        logger.debug("Falling back to unix epoch parsing for %s", value)
        return datetime.fromtimestamp(0)
