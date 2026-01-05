"""FastAPI entrypoint that exposes the interview agent via AG-UI."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import json
import re
from contextlib import suppress
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, Iterable, Mapping, Optional, Sequence, List, Literal
from uuid import uuid4
from weakref import WeakKeyDictionary

from pydantic import BaseModel


class TestAgentRequest(BaseModel):
    seed: Optional[int] = None
    persona: Optional[Dict[str, Any]] = None
    language: Optional[str] = None


class SpecPreviewRequest(BaseModel):
    thread_id: str
    refresh: bool = False


class SpecDiagramAsset(BaseModel):
    path: str
    svg: Optional[str] = None


class SpecPreviewResponse(BaseModel):
    markdown: str
    markdown_path: Optional[str]
    pdf_path: Optional[str]
    diagrams: List[SpecDiagramAsset]


class SessionSummary(BaseModel):
    id: str
    scope: InterviewScope
    created_at: datetime
    turn_count: int
    spec_available: bool
    pdf_available: bool
    feedback_count: int


class TranscriptMessage(BaseModel):
    role: Literal["assistant", "user", "system"]
    content: str


class SpecFeedbackEntry(BaseModel):
    feedback_id: str
    session_id: str
    message: str
    created_at: datetime


class SpecFeedbackRequest(BaseModel):
    message: str


class SessionDetailResponse(BaseModel):
    id: str
    scope: InterviewScope
    created_at: datetime
    spec: SpecPreviewResponse
    transcript: List[TranscriptMessage]
    feedback: List[SpecFeedbackEntry]


def _coerce_string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [segment.strip() for segment in value.split("\n") if segment.strip()]
    return []


def _normalize_persona(persona: object) -> Dict[str, object]:
    if is_dataclass(persona):
        raw = asdict(persona)
    elif isinstance(persona, Mapping):
        raw = dict(persona)
    else:
        raw = {
            "project_name": getattr(persona, "project_name", ""),
            "company": getattr(persona, "company", ""),
            "stakeholder_role": getattr(persona, "stakeholder_role", ""),
            "context": getattr(persona, "context", ""),
            "goals": getattr(persona, "goals", []),
            "risks": getattr(persona, "risks", []),
            "preferences": getattr(persona, "preferences", []),
            "tone": getattr(persona, "tone", ""),
        }

    return {
        "project_name": str(raw.get("project_name", "")).strip(),
        "company": str(raw.get("company", "")).strip(),
        "stakeholder_role": str(raw.get("stakeholder_role", "")).strip(),
        "context": str(raw.get("context", "")).strip(),
        "goals": _coerce_string_list(raw.get("goals")),
        "risks": _coerce_string_list(raw.get("risks")),
        "preferences": _coerce_string_list(raw.get("preferences")),
        "tone": str(raw.get("tone", "")).strip(),
    }

from agent_framework import (
    AgentRunResponse,
    AgentRunResponseUpdate,
    AgentThread,
    ChatMessage as FrameworkChatMessage,
    Role,
    TextContent,
)
from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from starlette.responses import StreamingResponse
from fastapi.responses import FileResponse

from .config import AppSettings, InterviewScope
from .observability import initialize_tracing
from .prompts import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, resolve_language_code
from .transcript_archive import TranscriptArchive
from .interview_agent import BusinessAnalystInterviewAgent
from .sessions import BusinessAnalystSession
from .transcript_archive import TranscriptRecord
from .test_agent import SimulatedStakeholderResponder, simulate_interview

MessageInput = (
    str
    | FrameworkChatMessage
    | Sequence[str | FrameworkChatMessage]
    | None
)


class BusinessAnalystAGUIAgent:
    """Agent Framework adapter that streams interview interactions to AG-UI."""

    def __init__(self, settings: AppSettings, scope: InterviewScope) -> None:
        self._settings = settings
        self._scope = scope
        self._id = f"ba-interview-{scope.value}"
        scope_label = scope.value.replace("_", " ").title()
        self._name = f"Business Analyst ({scope_label})"
        scope_line = scope_label.lower()
        self._description = (
            "Guided discovery interview that drafts a functional "
            f"specification for the {scope_line} scope."
        )
        self._sessions: WeakKeyDictionary[AgentThread, BusinessAnalystSession] = (
            WeakKeyDictionary()
        )
        self._thread_keys: WeakKeyDictionary[AgentThread, list[str]] = (
            WeakKeyDictionary()
        )
        self._thread_languages: WeakKeyDictionary[AgentThread, str] = (
            WeakKeyDictionary()
        )
        self._language_lookup: dict[str, str] = {}
        self._session_lookup: dict[str, BusinessAnalystSession] = {}
        self._retained_session_keys: list[str] = []
        self._retained_session_limit = 8
        self._thread_record_index_path = (
            self._settings.output_dir / "thread_record_index.json"
        )
        self._thread_record_index = self._load_thread_record_index()
        # Minimal surfaces expected by AgentFrameworkAgent orchestrators
        self.chat_options = SimpleNamespace(tools=None, response_format=None)
        self.chat_client = SimpleNamespace(
            function_invocation_configuration=None
        )

    @property
    def id(self) -> str:
        return self._id

    def _load_thread_record_index(self) -> dict[str, str]:
        if not self._thread_record_index_path.exists():
            return {}
        try:
            raw = self._thread_record_index_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            logging.warning(
                "Unable to load thread record index from %s",
                self._thread_record_index_path,
            )
            return {}
        if not isinstance(data, Mapping):
            return {}
        result: dict[str, str] = {}
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, str):
                result[key] = value
        return result

    def _persist_thread_record_index(self) -> None:
        try:
            payload = json.dumps(
                dict(sorted(self._thread_record_index.items())),
                ensure_ascii=False,
                indent=2,
            )
            self._thread_record_index_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            self._thread_record_index_path.write_text(
                payload,
                encoding="utf-8",
            )
        except OSError:
            logging.exception(
                "Failed to persist thread record index to %s",
                self._thread_record_index_path,
            )

    def get_record_id_for_thread(self, thread_identifier: str) -> Optional[str]:
        return self._thread_record_index.get(thread_identifier)

    def remember_session_record(self, session: BusinessAnalystSession) -> None:
        self._remember_record_mapping(session)

    def remember_thread_record(self, thread_identifier: str, record_id: str) -> None:
        if not thread_identifier.startswith("thread_"):
            return
        if not record_id:
            return
        if self._thread_record_index.get(thread_identifier) == record_id:
            return
        self._thread_record_index[thread_identifier] = record_id
        logging.info(
            "Persisting thread record mapping for thread=%s record=%s",
            thread_identifier,
            record_id,
        )
        self._persist_thread_record_index()

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    def get_new_thread(self, **_: object) -> AgentThread:
        return AgentThread()

    async def run(
        self,
        messages: MessageInput = None,
        *,
        thread: AgentThread | None = None,
        state: Mapping[str, object] | None = None,
        **_: object,
    ) -> AgentRunResponse:
        updates: list[AgentRunResponseUpdate] = []
        async for update in self.run_stream(messages, thread=thread, state=state):
            updates.append(update)
        if updates:
            return AgentRunResponse.from_agent_run_response_updates(updates)
        return AgentRunResponse()

    async def run_stream(
        self,
        messages: MessageInput = None,
        *,
        thread: AgentThread | None = None,
        state: Mapping[str, object] | None = None,
        **_: object,
    ) -> AsyncIterator[AgentRunResponseUpdate]:
        thread = thread or self.get_new_thread()
        session_state: Optional[BusinessAnalystSession] = self._sessions.get(thread)
        user_text = self._extract_user_text(messages)

        if state is None and thread is not None:
            metadata = getattr(thread, "metadata", None)
            if isinstance(metadata, Mapping):
                metadata_state = metadata.get("current_state")
                if isinstance(metadata_state, Mapping):
                    state = metadata_state
                elif isinstance(metadata_state, str):
                    try:
                        loaded_state = json.loads(metadata_state)
                    except json.JSONDecodeError:
                        loaded_state = None
                    if isinstance(loaded_state, dict):
                        state = loaded_state

        state_language = None
        if state is not None:
            logging.info(
                "AGUI run_stream received state keys=%s for scope=%s",
                list(state.keys()),
                self._scope.value,
            )
            state_language = self._normalize_language_value(state.get("language"))
            logging.info(
                "AGUI run_stream normalized state language=%s",
                state_language,
            )

        preferred_language = state_language
        if preferred_language is None:
            preferred_language = self._thread_languages.get(thread)
        if preferred_language is None:
            preferred_language = self._lookup_language_for_thread(thread)
        if preferred_language is None:
            preferred_language = DEFAULT_LANGUAGE

        self._remember_language(thread, preferred_language)
        logging.info(
            "AGUI run_stream resolved language=%s for scope=%s thread=%s",
            preferred_language,
            self._scope.value,
            getattr(thread, "id", None) or id(thread),
        )

        if session_state is None:
            session_state = BusinessAnalystSession.create(
                settings=self._settings,
                scope=self._scope,
                language=preferred_language,
            )
            if session_state.agent.language != preferred_language:
                session_state.set_language(preferred_language)
            logging.debug(
                "Created new session with agent language=%s",
                session_state.agent.language,
            )
            kickoff = await session_state.kickoff()
            await self._append_assistant_message(thread, kickoff)
            yield self._as_update(kickoff)
            self._sessions[thread] = session_state
            self._register_session(thread, session_state)
            if not user_text:
                return
        else:
            self._register_session(thread, session_state)
            if session_state.agent.language != preferred_language:
                session_state.set_language(preferred_language)
                logging.debug(
                    "Updated existing session language to %s",
                    preferred_language,
                )

        if not user_text:
            return

        await self._append_user_message(thread, user_text)
        assert session_state is not None  # for type checkers
        responses = await session_state.handle_user_message(user_text)
        for update in responses:
            await self._append_assistant_message(thread, update)
            yield self._as_update(update)

        self._remember_record_mapping(session_state)

    async def _append_user_message(
        self,
        thread: AgentThread,
        text: str,
    ) -> None:
        if not text:
            return
        await thread.on_new_messages(
            FrameworkChatMessage(role=Role.USER, text=text)
        )

    async def _append_assistant_message(
        self,
        thread: AgentThread,
        text: str,
    ) -> None:
        if not text:
            return
        await thread.on_new_messages(
            FrameworkChatMessage(role=Role.ASSISTANT, text=text)
        )

    @staticmethod
    def _as_update(text: str) -> AgentRunResponseUpdate:
        return AgentRunResponseUpdate(contents=[TextContent(text=text)])

    def _extract_user_text(
        self,
        messages: MessageInput,
    ) -> str:
        if messages is None:
            return ""
        if isinstance(messages, str):
            return messages.strip()
        if isinstance(messages, FrameworkChatMessage):
            if messages.role == Role.USER:
                if messages.text:
                    return messages.text.strip()
                return self._coalesce_contents(messages.contents)
            return ""
        if isinstance(messages, Mapping):
            return self._extract_user_text_from_mapping(messages)
        if isinstance(messages, (list, tuple)):
            for item in reversed(messages):
                text = self._extract_user_text(item)
                if text:
                    return text
        return ""

    @staticmethod
    def _coalesce_contents(contents: Iterable[object]) -> str:
        fragments: list[str] = []
        for content in contents:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                fragments.append(text)
        return " ".join(fragment.strip() for fragment in fragments if fragment)

    @staticmethod
    def _extract_user_text_from_mapping(message: Mapping[str, object]) -> str:
        role = str(message.get("role", "")).strip().lower()
        if role and role != Role.USER.value:
            return ""

        content = message.get("content")
        if content is None and "contents" in message:
            content = message.get("contents")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, Mapping):
            text = content.get("text")
            if isinstance(text, str):
                return text.strip()

        if isinstance(content, Iterable) and not isinstance(content, (str, bytes)):
            fragments: list[str] = []
            for item in content:
                if isinstance(item, str):
                    fragments.append(item)
                elif isinstance(item, Mapping):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        fragments.append(text)
            joined = " ".join(fragment.strip() for fragment in fragments if fragment)
            if joined:
                return joined

        if content is not None:
            return str(content).strip()

        text_value = message.get("text")
        if isinstance(text_value, str):
            return text_value.strip()

        return ""

    @staticmethod
    def _normalize_language_value(value: object) -> str | None:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                return None
            normalized = normalized.replace("_", "-")
            normalized = normalized.split("-")[0]
            if normalized in SUPPORTED_LANGUAGES:
                return normalized
        return None

    def _remember_language(self, thread: AgentThread, language: str) -> None:
        normalized = language if language in SUPPORTED_LANGUAGES else resolve_language_code(language)
        self._thread_languages[thread] = normalized
        keys = self._compute_thread_keys(thread)
        if not keys:
            return
        for key in keys:
            self._language_lookup[key] = normalized

    def _lookup_language_for_thread(self, thread: AgentThread) -> str | None:
        keys = self._thread_keys.get(thread)
        if not keys:
            keys = self._compute_thread_keys(thread)
        for key in keys:
            stored = self._language_lookup.get(key)
            if stored:
                return stored
        return None

    def _compute_thread_keys(self, thread: AgentThread) -> list[str]:
        keys: list[str] = []

        def _append(value: object) -> None:
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    keys.append(normalized)

        for attr in (
            "id",
            "thread_id",
            "threadId",
            "identifier",
            "service_thread_id",
            "conversation_id",
        ):
            _append(getattr(thread, attr, None))

        metadata = getattr(thread, "metadata", None)
        if isinstance(metadata, Mapping):
            for meta_key in (
                "ag_ui_thread_id",
                "thread_id",
                "threadId",
                "identifier",
                "service_thread_id",
                "conversation_id",
            ):
                _append(metadata.get(meta_key))

        keys.append(str(id(thread)))
        normalized: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if key not in seen:
                seen.add(key)
                normalized.append(key)
        return normalized

    def _remember_record_mapping(
        self,
        session: BusinessAnalystSession,
    ) -> None:
        record_id = session.archived_record_id
        if not record_id:
            return
        relevant_keys: list[str] = []
        for key, candidate in self._session_lookup.items():
            if candidate is session and key.startswith("thread_"):
                relevant_keys.append(key)
        if not relevant_keys:
            return
        for key in relevant_keys:
            self.remember_thread_record(key, record_id)

    def _register_session(
        self,
        thread: AgentThread,
        session: BusinessAnalystSession,
    ) -> None:
        keys = self._compute_thread_keys(thread)
        if not keys:
            return
        self._thread_keys[thread] = keys
        language = self._thread_languages.get(thread)
        if language:
            for key in keys:
                self._language_lookup[key] = language
        for key in keys:
            self._session_lookup[key] = session
            if key in self._retained_session_keys:
                self._retained_session_keys.remove(key)
        self._remember_record_mapping(session)
        logging.debug(
            "Registered session for scope=%s thread_keys=%s",
            self._scope.value,
            keys,
        )

    def _unregister_session(
        self,
        thread: AgentThread,
        *,
        keep_lookup: bool = False,
    ) -> None:
        keys = self._thread_keys.pop(thread, [])
        if keep_lookup:
            retained_session: Optional[BusinessAnalystSession] = None
            for key in keys:
                if key not in self._retained_session_keys:
                    self._retained_session_keys.append(key)
                if retained_session is None:
                    retained_session = self._session_lookup.get(key)
            if retained_session is not None:
                self._remember_record_mapping(retained_session)
            self._trim_retained_sessions()
            return

        for key in keys:
            existing = self._session_lookup.get(key)
            if existing is not None and existing in self._sessions.values():
                continue
            self._session_lookup.pop(key, None)
            self._language_lookup.pop(key, None)
            try:
                self._retained_session_keys.remove(key)
            except ValueError:
                pass

    def get_session_by_id(
        self,
        thread_identifier: str,
    ) -> Optional[BusinessAnalystSession]:
        session = self._session_lookup.get(thread_identifier)
        if session is None:
            logging.debug(
                "Lookup miss for thread=%s scope=%s known=%s",
                thread_identifier,
                self._scope.value,
                list(self._session_lookup.keys()),
            )
        return session

    def _trim_retained_sessions(self) -> None:
        while len(self._retained_session_keys) > self._retained_session_limit:
            oldest = self._retained_session_keys.pop(0)
            session = self._session_lookup.get(oldest)
            if session is not None:
                self._remember_record_mapping(session)
            if session is not None and session in self._sessions.values():
                self._retained_session_keys.append(oldest)
                continue
            self._session_lookup.pop(oldest, None)
            self._language_lookup.pop(oldest, None)


def _build_response_from_simulation(persona: object, result: Dict[str, Any]) -> Dict[str, object]:
    transcript_raw = result.get("transcript", [])
    transcript: list[Dict[str, str]] = []
    for turn in transcript_raw:
        question = ""
        answer = ""
        if isinstance(turn, Mapping):
            question = str(turn.get("question", "")).strip()
            answer = str(turn.get("answer", "")).strip()
        elif isinstance(turn, (list, tuple)) and len(turn) >= 2:
            question = str(turn[0]).strip()
            answer = str(turn[1]).strip()
        if question or answer:
            transcript.append({"question": question, "answer": answer})

    review_warnings_raw = result.get("review_warnings", [])
    warnings: list[str] = []
    if isinstance(review_warnings_raw, Iterable) and not isinstance(review_warnings_raw, (str, bytes)):
        for note in review_warnings_raw:
            text = str(note).strip()
            if text:
                warnings.append(text)

    record_id = result.get("record_id")
    spec_path = result.get("spec_path")
    pdf_path = result.get("pdf_path")

    return {
        "persona": _normalize_persona(persona),
        "transcript": transcript,
        "closing_feedback": str(result.get("closing_feedback", "")).strip(),
        "review_warnings": warnings,
        "record_id": record_id if record_id is None else str(record_id),
        "spec_path": str(spec_path) if spec_path is not None else None,
        "pdf_path": str(pdf_path) if pdf_path is not None else None,
        "language": result.get("language"),
    }


def create_app(
    settings: AppSettings,
    *,
    scopes: Sequence[InterviewScope] | None = None,
    allow_origins: Sequence[str] | None = None,
) -> FastAPI:
    """Create a FastAPI app exposing each configured scope as an AG-UI endpoint."""

    app = FastAPI(title="Business Analyst Interview Agent")

    origins = list(allow_origins) if allow_origins else ["*"]
    allow_credentials = origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    target_scopes = list(scopes) if scopes else list(InterviewScope)
    output_root = settings.output_dir.resolve()
    archive = TranscriptArchive(settings)
    feedback_log = output_root / "spec_feedback.jsonl"
    feedback_log.parent.mkdir(parents=True, exist_ok=True)
    agent_registry: Dict[str, BusinessAnalystAGUIAgent] = {}
    for scope in target_scopes:
        agent = BusinessAnalystAGUIAgent(settings=settings, scope=scope)
        agent_registry[scope.value] = agent
        add_agent_framework_fastapi_endpoint(
            app=app,
            agent=agent,
            path=f"/{scope.value}",
        )

    test_agent_mode = os.getenv("MAF_TEST_AGENT_MODE", "live").strip().lower()
    test_agent_profile = os.getenv("MAF_TEST_AGENT_PROFILE", "quick").strip().lower()

    timeout_env = os.getenv("MAF_TEST_AGENT_TIMEOUT", "").strip()
    if timeout_env:
        try:
            parsed_timeout = int(timeout_env)
        except ValueError:
            parsed_timeout = -1
        if parsed_timeout <= 0:
            logging.warning(
                "Invalid MAF_TEST_AGENT_TIMEOUT value '%s'. Falling back to defaults.",
                timeout_env,
            )
            timeout_env = ""
        else:
            test_agent_timeout = parsed_timeout
    if not timeout_env:
        test_agent_timeout = 360 if test_agent_profile == "full" else 120

    def _resolve_scope(scope_name: str) -> InterviewScope:
        try:
            return InterviewScope.from_string(scope_name, default=None)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _get_agent(scope: InterviewScope) -> BusinessAnalystAGUIAgent:
        agent = agent_registry.get(scope.value)
        if agent is None:
            raise HTTPException(status_code=500, detail="Agent not registered for this scope.")
        return agent

    def _runtime_settings_for_request() -> AppSettings:
        if test_agent_profile == "full":
            return settings
        return replace(
            settings,
            subject_max_questions=max(1, min(settings.subject_max_questions, 2)),
            review_max_passes=max(1, min(settings.review_max_passes, 1)),
        )

    def _ensure_test_agent_enabled() -> None:
        if test_agent_mode in {"disabled", "off"}:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Test agent simulation is disabled. Set MAF_TEST_AGENT_MODE"
                    " to 'live' to enable it."
                ),
            )

    def _parse_scope_param(value: Optional[str]) -> Optional[InterviewScope]:
        if value is None:
            return None
        try:
            return InterviewScope.from_string(value, default=None)
        except ValueError as exc:  # pragma: no cover - validation guard
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _relative_to_output(path: Optional[Path]) -> Optional[str]:
        if path is None:
            return None
        try:
            return str(path.relative_to(output_root))
        except ValueError:
            return str(path)

    def _parse_feedback_timestamp(raw_value: object) -> datetime:
        if not isinstance(raw_value, str):
            return datetime.now(timezone.utc)
        cleaned = raw_value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _load_feedback_entries(session_id: Optional[str] = None) -> List[SpecFeedbackEntry]:
        if not feedback_log.exists():
            return []
        entries: List[SpecFeedbackEntry] = []
        with feedback_log.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                text = raw_line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                payload_session = str(payload.get("session_id", ""))
                if session_id and payload_session != session_id:
                    continue
                message = str(payload.get("message", "")).strip()
                if not message:
                    continue
                created_at = _parse_feedback_timestamp(payload.get("created_at"))
                feedback_id = str(payload.get("feedback_id") or uuid4().hex)
                entry = SpecFeedbackEntry(
                    feedback_id=feedback_id,
                    session_id=payload_session,
                    message=message,
                    created_at=created_at,
                )
                entries.append(entry)
        entries.sort(key=lambda item: item.created_at)
        return entries

    def _append_feedback_entry(session_id: str, message: str) -> SpecFeedbackEntry:
        feedback_id = uuid4().hex
        timestamp = datetime.now(timezone.utc)
        record = {
            "feedback_id": feedback_id,
            "session_id": session_id,
            "message": message,
            "created_at": timestamp.isoformat(),
        }
        with feedback_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return SpecFeedbackEntry(
            feedback_id=feedback_id,
            session_id=session_id,
            message=message,
            created_at=timestamp,
        )

    def _load_spec_from_record(
        record: TranscriptRecord,
    ) -> tuple[str, SimpleNamespace]:
        markdown_path = record.spec_path if record.spec_path and record.spec_path.exists() else None
        spec_text = record.spec_text or ""
        if not spec_text and markdown_path is not None:
            try:
                spec_text = markdown_path.read_text(encoding="utf-8")
            except OSError:
                spec_text = ""
        pdf_path = None
        if markdown_path is not None:
            candidate_pdf = markdown_path.with_suffix(".pdf")
            if candidate_pdf.exists():
                pdf_path = candidate_pdf
        return spec_text, SimpleNamespace(
            markdown_path=markdown_path,
            pdf_path=pdf_path,
        )

    def _find_record_by_spec_path(
        path: Path,
        scope_hint: Optional[InterviewScope],
    ) -> Optional[TranscriptRecord]:
        if not path.exists():
            return None

        resolved = path.resolve()

        def _match(records: Iterable[TranscriptRecord]) -> Optional[TranscriptRecord]:
            for entry in records:
                if entry.spec_path and entry.spec_path.exists():
                    try:
                        if entry.spec_path.resolve() == resolved:
                            return entry
                    except OSError:
                        continue
            return None

        candidates = archive.list(limit=200, scope=scope_hint)
        match = _match(candidates)
        if match is not None:
            return match
        archive.refresh()
        candidates = archive.list(limit=200, scope=scope_hint)
        return _match(candidates)

    async def _regenerate_spec_from_feedback(
        record_id: str,
        record: TranscriptRecord,
        feedback_message: str,
    ) -> None:
        sanitized = feedback_message.strip()
        if not sanitized:
            return
        agent = BusinessAnalystInterviewAgent(settings=settings, scope=record.scope)
        agent.load_transcript_history(
            turns=record.turns,
            initial_prompt=record.initial_prompt,
        )
        agent.record_feedback_annotation(sanitized)
        agent.add_manual_correction(sanitized)
        updated_spec = await agent.summarize()
        artifacts = agent.export_spec(updated_spec)
        archive.append_spec_update(
            record_id,
            scope=record.scope,
            spec_text=updated_spec,
            spec_path=artifacts.markdown_path,
        )

    async def _regenerate_live_session_spec(
        session_state: BusinessAnalystSession,
        feedback_message: str,
    ) -> Optional[str]:
        sanitized = feedback_message.strip()
        if not sanitized:
            return session_state.archived_record_id
        session_state.agent.record_feedback_annotation(sanitized)
        session_state.agent.add_manual_correction(sanitized)
        updated_spec = await session_state.agent.summarize()
        artifacts = session_state.agent.export_spec(updated_spec)
        session_state.pending_spec_text = updated_spec
        session_state.last_spec_text = updated_spec
        session_state.last_spec_markdown_path = artifacts.markdown_path
        session_state.last_spec_pdf_path = artifacts.pdf_path
        record_id = session_state.agent.persist_transcript(
            spec_text=updated_spec,
            spec_path=artifacts.markdown_path,
        )
        if record_id:
            session_state.archived_record_id = record_id
            archive.refresh()
            owning_agent = agent_registry.get(session_state.agent.scope.value)
            if owning_agent is not None:
                owning_agent.remember_session_record(session_state)
        return session_state.archived_record_id

    image_pattern = re.compile(r"!\[[^\]]*\]\((?P<path>[^)]+)\)")

    def _collect_svg_assets(
        markdown_text: str,
        markdown_path: Optional[Path],
    ) -> List[SpecDiagramAsset]:
        assets: List[SpecDiagramAsset] = []
        if not markdown_text:
            return assets
        base_dir = markdown_path.parent if markdown_path else output_root
        seen: set[str] = set()
        for match in image_pattern.finditer(markdown_text):
            raw_path = match.group("path").strip()
            if not raw_path:
                continue
            normalized = raw_path.replace("\\", "/")
            if normalized in seen:
                continue
            seen.add(normalized)
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = (base_dir / raw_path).resolve()
            else:
                candidate = candidate.resolve()
            try:
                candidate.relative_to(output_root)
            except ValueError:
                continue
            if candidate.suffix.lower() != ".svg":
                continue
            if not candidate.exists():
                continue
            try:
                svg_text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            assets.append(SpecDiagramAsset(path=normalized, svg=svg_text))
        return assets

    def _load_latest_spec_from_disk(
        scope: Optional[InterviewScope] = None,
    ) -> Optional[tuple[str, SimpleNamespace, InterviewScope]]:
        patterns: list[tuple[InterviewScope, str]]
        if scope is not None:
            patterns = [(scope, f"functional_spec_{scope.value}_*.md")]
        else:
            patterns = [
                (candidate_scope, f"functional_spec_{candidate_scope.value}_*.md")
                for candidate_scope in InterviewScope
            ]

        entries: list[tuple[float, Path, InterviewScope]] = []
        for pattern_scope, pattern in patterns:
            for candidate in output_root.glob(pattern):
                try:
                    mtime = candidate.stat().st_mtime
                except OSError:
                    continue
                entries.append((mtime, candidate, pattern_scope))
        entries.sort(key=lambda item: item[0], reverse=True)
        for _, candidate, resolved_scope in entries:
            try:
                spec_text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            pdf_candidate = candidate.with_suffix(".pdf")
            pdf_path = pdf_candidate if pdf_candidate.exists() else None
            artifacts = SimpleNamespace(
                markdown_path=candidate,
                pdf_path=pdf_path,
            )
            return spec_text, artifacts, resolved_scope
        return None

    @app.post("/test-agent/{scope_name}")
    async def run_test_agent(scope_name: str, payload: TestAgentRequest) -> Dict[str, object]:
        scope = _resolve_scope(scope_name)
        _ensure_test_agent_enabled()
        runtime_settings = _runtime_settings_for_request()

        logging.info(
            "Running test agent simulation (profile=%s, timeout=%ss, scope=%s)",
            test_agent_profile or "quick",
            test_agent_timeout,
            scope.value,
        )

        language_code = resolve_language_code(payload.language or DEFAULT_LANGUAGE)

        try:
            responder = await asyncio.wait_for(
                SimulatedStakeholderResponder.create(
                    settings=runtime_settings,
                    scope=scope,
                    seed=payload.seed,
                    persona_override=payload.persona,
                    language=language_code,
                ),
                timeout=test_agent_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail=(
                    "Timed out while preparing the simulated stakeholder."
                    f" Increase MAF_TEST_AGENT_TIMEOUT (current: {test_agent_timeout}s)."
                ),
            ) from exc
        except Exception as exc:
            logging.exception(
                "Failed to initialize test agent responder for scope %s",
                scope.value,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Unable to initialize test agent responder: {exc}",
            ) from exc

        try:
            result = await asyncio.wait_for(
                simulate_interview(
                    settings=runtime_settings,
                    scope=scope,
                    responder=responder,
                    language=language_code,
                    verbose=False,
                ),
                timeout=test_agent_timeout,
            )
        except asyncio.TimeoutError as exc:
            logging.warning(
                "Test agent simulation timed out for scope %s after %ss",
                scope.value,
                test_agent_timeout,
            )
            raise HTTPException(
                status_code=504,
                detail=(
                    "Test agent simulation timed out. Consider increasing "
                    "MAF_TEST_AGENT_TIMEOUT or reducing interview depth."
                ),
            ) from exc
        except Exception as exc:
            logging.exception(
                "Test agent simulation failed for scope %s",
                scope.value,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Test agent simulation failed: {exc}",
            ) from exc

        persona_payload = result.get("persona", responder.persona)
        return _build_response_from_simulation(persona_payload, result)

    @app.post("/test-agent/{scope_name}/stream")
    async def stream_test_agent(scope_name: str, payload: TestAgentRequest) -> StreamingResponse:
        scope = _resolve_scope(scope_name)
        _ensure_test_agent_enabled()
        runtime_settings = _runtime_settings_for_request()

        status_prefix = test_agent_profile or "quick"
        logging.info(
            "Streaming test agent simulation (profile=%s, timeout=%ss, scope=%s)",
            status_prefix,
            test_agent_timeout,
            scope.value,
        )

        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        done_token = object()

        language_code = resolve_language_code(payload.language or DEFAULT_LANGUAGE)
        status_translations = {
            "Confirming AS-IS understanding with the stakeholder...": "Confirmando la comprension AS-IS con la parte interesada...",
            "Reviewing the target TO-BE vision with the stakeholder...": "Revisando la vision TO-BE objetivo con la parte interesada...",
            "Generating functional specification draft...": "Generando el borrador de la especificacion funcional...",
            "Reviewer requested additional details...": "El revisor solicito detalles adicionales...",
            "Additional details captured. Regenerating the specification...": "Se capturaron detalles adicionales. Regenerando la especificacion...",
            "Awaiting stakeholder closing feedback...": "Esperando la retroalimentacion final de la parte interesada...",
            "Stakeholder approved summary. Refreshing specification...": "La parte interesada aprobo el resumen. Actualizando la especificacion...",
            "Simulation complete.": "Simulacion completada.",
        }

        async def enqueue(event: Dict[str, Any]) -> None:
            await queue.put(event)

        async def observer(kind: str, data: Dict[str, object]) -> None:
            event: Dict[str, Any] = {"type": kind}

            if kind == "message":
                event["role"] = str(data.get("role", "assistant"))
                event["content"] = str(data.get("content", ""))
            elif kind == "persona":
                persona_raw = data.get("persona")
                event["persona"] = _normalize_persona(persona_raw)
            elif kind in {"spec_draft", "spec_final", "review_feedback", "status"}:
                event["content"] = str(data.get("content", ""))
            elif kind in {"review_warning", "review_note"}:
                note = data.get("note") or data.get("content")
                event["note"] = str(note or "")
            elif kind == "artifact":
                event["kind"] = str(data.get("kind", ""))
                path = data.get("path")
                if path is not None:
                    event["path"] = str(path)
                record_id = data.get("record_id")
                if record_id is not None:
                    event["recordId"] = str(record_id)
            else:
                for key, value in data.items():
                    if key in {"role", "content", "persona", "note", "kind", "path", "record_id"}:
                        continue
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        event[key] = value
                    else:
                        event[key] = str(value)

            if kind == "message" and not event.get("content"):
                return
            if kind == "status" and language_code == "es":
                translated = status_translations.get(event.get("content", ""))
                if translated:
                    event["content"] = translated
            await enqueue(event)

        async def producer() -> None:
            try:
                await enqueue(
                    {
                        "type": "status",
                        "content": (
                            "Preparando la persona simulada del stakeholder..."
                            if language_code == "es"
                            else "Preparing simulated stakeholder persona..."
                        ),
                    }
                )
                responder = await asyncio.wait_for(
                    SimulatedStakeholderResponder.create(
                        settings=runtime_settings,
                        scope=scope,
                        seed=payload.seed,
                        persona_override=payload.persona,
                        language=language_code,
                    ),
                    timeout=test_agent_timeout,
                )
                result = await asyncio.wait_for(
                    simulate_interview(
                        settings=runtime_settings,
                        scope=scope,
                        responder=responder,
                        language=language_code,
                        verbose=False,
                        observer=observer,
                    ),
                    timeout=test_agent_timeout,
                )
                persona_payload = result.get("persona", responder.persona)
                await enqueue(
                    {
                        "type": "complete",
                        "result": _build_response_from_simulation(persona_payload, result),
                    }
                )
            except asyncio.TimeoutError:
                message = (
                    "Test agent simulation timed out. Consider increasing "
                    "MAF_TEST_AGENT_TIMEOUT or reducing interview depth."
                )
                logging.warning(
                    "Test agent simulation timed out for scope %s after %ss",
                    scope.value,
                    test_agent_timeout,
                )
                await enqueue({"type": "error", "message": message})
            except Exception as exc:
                logging.exception("Test agent simulation failed for scope %s", scope.value)
                await enqueue(
                    {
                        "type": "error",
                        "message": f"Test agent simulation failed: {exc}",
                    }
                )
            finally:
                await queue.put({"type": done_token})

        simulation_task = asyncio.create_task(producer())

        async def event_stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    event = await queue.get()
                    if event.get("type") is done_token:
                        break
                    payload = json.dumps(event, ensure_ascii=False)
                    yield f"data: {payload}\n\n".encode("utf-8")
            except asyncio.CancelledError:
                simulation_task.cancel()
                raise
            finally:
                simulation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await simulation_task

        headers = {"Cache-Control": "no-cache"}
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

    @app.get("/sessions", response_model=List[SessionSummary])
    async def list_sessions(scope: Optional[str] = None, limit: int = 10) -> List[SessionSummary]:
        resolved_scope = _parse_scope_param(scope)
        bounded_limit = max(1, min(limit, 50))
        records = archive.list(limit=bounded_limit, scope=resolved_scope)
        feedback_index: Dict[str, int] = {}
        for entry in _load_feedback_entries(None):
            feedback_index[entry.session_id] = feedback_index.get(entry.session_id, 0) + 1
        summaries: List[SessionSummary] = []
        for record in records:
            markdown_path = record.spec_path if record.spec_path and record.spec_path.exists() else None
            pdf_path = None
            if markdown_path is not None:
                candidate_pdf = markdown_path.with_suffix(".pdf")
                if candidate_pdf.exists():
                    pdf_path = candidate_pdf
            has_spec = bool(record.spec_text) or markdown_path is not None
            has_pdf = pdf_path is not None
            summaries.append(
                SessionSummary(
                    id=record.id,
                    scope=record.scope,
                    created_at=record.created_at,
                    turn_count=record.turn_count,
                    spec_available=has_spec,
                    pdf_available=has_pdf,
                    feedback_count=feedback_index.get(record.id, 0),
                )
            )
        return summaries

    @app.get("/sessions/{session_id}", response_model=SessionDetailResponse)
    async def get_session_detail(session_id: str) -> SessionDetailResponse:
        record = archive.get(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Session not found.")

        markdown_text, artifacts = _load_spec_from_record(record)
        diagrams = _collect_svg_assets(markdown_text, artifacts.markdown_path)

        spec_payload = SpecPreviewResponse(
            markdown=markdown_text,
            markdown_path=_relative_to_output(artifacts.markdown_path),
            pdf_path=_relative_to_output(artifacts.pdf_path),
            diagrams=diagrams,
        )

        transcript_messages: List[TranscriptMessage] = []
        if record.initial_prompt:
            transcript_messages.append(
                TranscriptMessage(role="user", content=record.initial_prompt)
            )
        for question, answer in record.turns:
            question_text = question.strip()
            if question_text:
                transcript_messages.append(
                    TranscriptMessage(role="assistant", content=question_text)
                )
            answer_text = answer.strip()
            if answer_text:
                transcript_messages.append(
                    TranscriptMessage(role="user", content=answer_text)
                )

        feedback_entries = _load_feedback_entries(record.id)

        return SessionDetailResponse(
            id=record.id,
            scope=record.scope,
            created_at=record.created_at,
            spec=spec_payload,
            transcript=transcript_messages,
            feedback=feedback_entries,
        )

    @app.post("/sessions/{session_id}/feedback", response_model=SpecFeedbackEntry, status_code=201)
    async def submit_spec_feedback(session_id: str, payload: SpecFeedbackRequest) -> SpecFeedbackEntry:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Feedback message cannot be empty.")
        record = archive.get(session_id)
        target_session_id = session_id
        session_state: Optional[BusinessAnalystSession] = None

        if record is None:
            for agent in agent_registry.values():
                candidate = agent.get_session_by_id(session_id)
                if candidate is None:
                    continue
                session_state = candidate
                archived_identifier = getattr(candidate, "archived_record_id", None)
                if archived_identifier:
                    target_session_id = archived_identifier
                    record = archive.get(archived_identifier)
                    if record is None:
                        archive.refresh()
                        record = archive.get(archived_identifier)
                break

        if record is None and session_state is not None:
            new_record_id = await _regenerate_live_session_spec(session_state, message)
            if new_record_id:
                target_session_id = new_record_id
                record = archive.get(new_record_id)
                if record is None:
                    archive.refresh()
                    record = archive.get(new_record_id)

        if record is None and session_state is None:
            for agent in agent_registry.values():
                mapped_record_id = agent.get_record_id_for_thread(target_session_id)
                if not mapped_record_id:
                    continue
                candidate = archive.get(mapped_record_id)
                if candidate is None:
                    archive.refresh()
                    candidate = archive.get(mapped_record_id)
                if candidate is None:
                    continue
                target_session_id = mapped_record_id
                record = candidate
                break

        if record is None:
            archive.refresh()
            record = archive.get(target_session_id)

        if record is None and session_state is None:
            raise HTTPException(status_code=404, detail="Session not found.")

        if record is not None:
            try:
                await _regenerate_spec_from_feedback(target_session_id, record, message)
            except Exception as exc:  # pragma: no cover - defensive path
                logging.exception(
                    "Failed to apply feedback for session %s", target_session_id
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Unable to update specification with feedback: {exc}",
                ) from exc
        else:
            logging.info(
                "Feedback applied to live session %s without archived record;"
                " retained in-memory spec update only.",
                target_session_id,
            )

        entry = _append_feedback_entry(target_session_id, message)
        return entry

    @app.post("/spec/{scope_name}", response_model=SpecPreviewResponse)
    async def generate_spec_preview(scope_name: str, payload: SpecPreviewRequest) -> SpecPreviewResponse:
        scope = _resolve_scope(scope_name)
        agent = _get_agent(scope)
        thread_id = payload.thread_id.strip()
        if not thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required.")

        session = agent.get_session_by_id(thread_id)
        record: Optional[TranscriptRecord] = None
        spec_text = ""
        artifacts = SimpleNamespace(markdown_path=None, pdf_path=None)
        resolved_scope = scope
        if session is None:
            mapped_record_id = agent.get_record_id_for_thread(thread_id)
            if mapped_record_id:
                record = archive.get(mapped_record_id)
                if record is None:
                    archive.refresh()
                    record = archive.get(mapped_record_id)
                if record is not None:
                    spec_text, artifacts = _load_spec_from_record(record)
                    resolved_scope = record.scope
                    if resolved_scope != scope:
                        logging.info(
                            "Serving specification from scope=%s for request scope=%s",
                            resolved_scope.value,
                            scope.value,
                        )
                    agent.remember_thread_record(thread_id, record.id)

        if session is None and record is None:
            logging.info(
                "Spec preview using disk fallback (scope=%s thread=%s)",
                scope.value,
                thread_id,
            )
            fallback = _load_latest_spec_from_disk(scope) or _load_latest_spec_from_disk()
            if fallback is None:
                logging.debug(
                    "No disk artifacts available for scope=%s; known threads=%s",
                    scope.value,
                    list(agent._session_lookup.keys()),
                )
                raise HTTPException(
                    status_code=404,
                    detail="Active session not found for the requested thread.",
                )
            spec_text, artifacts, resolved_scope = fallback
            if resolved_scope != scope:
                logging.info(
                    "Serving specification from scope=%s for request scope=%s",
                    resolved_scope.value,
                    scope.value,
                )
            if artifacts.markdown_path is not None:
                matched_record = _find_record_by_spec_path(
                    artifacts.markdown_path,
                    resolved_scope,
                )
                if matched_record is not None:
                    agent.remember_thread_record(thread_id, matched_record.id)
                    record = matched_record
        elif session is not None:
            try:
                spec_text, artifacts = await session.generate_spec_preview(
                    force_refresh=payload.refresh,
                )
            except Exception as exc:  # pragma: no cover - defensive guard
                logging.exception("Failed to generate specification preview")
                raise HTTPException(
                    status_code=500,
                    detail=f"Unable to generate specification preview: {exc}",
                ) from exc

        diagrams = _collect_svg_assets(spec_text, artifacts.markdown_path)

        return SpecPreviewResponse(
            markdown=spec_text,
            markdown_path=str(artifacts.markdown_path) if artifacts.markdown_path else None,
            pdf_path=str(artifacts.pdf_path) if artifacts.pdf_path else None,
            diagrams=diagrams,
        )

    @app.get("/spec/{scope_name}/pdf")
    async def download_spec_pdf(scope_name: str, thread_id: str) -> FileResponse:
        scope = _resolve_scope(scope_name)
        agent = _get_agent(scope)
        normalized_thread = thread_id.strip()
        if not normalized_thread:
            raise HTTPException(status_code=400, detail="thread_id query parameter is required.")

        session = agent.get_session_by_id(normalized_thread)
        record: Optional[TranscriptRecord] = None
        resolved_scope = scope
        pdf_path: Optional[Path] = None
        if session is None:
            mapped_record_id = agent.get_record_id_for_thread(normalized_thread)
            if mapped_record_id:
                record = archive.get(mapped_record_id)
                if record is None:
                    archive.refresh()
                    record = archive.get(mapped_record_id)
                if record is not None:
                    _, artifacts = _load_spec_from_record(record)
                    if artifacts.pdf_path is not None and artifacts.pdf_path.exists():
                        pdf_path = artifacts.pdf_path
                        resolved_scope = record.scope
                        if resolved_scope != scope:
                            logging.info(
                                "Serving PDF from scope=%s for request scope=%s",
                                resolved_scope.value,
                                scope.value,
                            )
                        agent.remember_thread_record(normalized_thread, record.id)

        if session is None:
            if pdf_path is None:
                logging.info(
                    "Spec PDF using disk fallback (scope=%s thread=%s)",
                    scope.value,
                    normalized_thread,
                )
                fallback = _load_latest_spec_from_disk(scope) or _load_latest_spec_from_disk()
                if fallback is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Active session not found for the requested thread.",
                    )
                _, artifacts, resolved_scope = fallback
                if artifacts.pdf_path is None or not artifacts.pdf_path.exists():
                    raise HTTPException(
                        status_code=404,
                        detail="Specification PDF is not available for this session.",
                    )
                pdf_path = artifacts.pdf_path
                if resolved_scope != scope:
                    logging.info(
                        "Serving PDF from scope=%s for request scope=%s",
                        resolved_scope.value,
                        scope.value,
                    )
                if artifacts.markdown_path is not None:
                    matched_record = _find_record_by_spec_path(
                        artifacts.markdown_path,
                        resolved_scope,
                    )
                    if matched_record is not None:
                        agent.remember_thread_record(
                            normalized_thread,
                            matched_record.id,
                        )
        else:
            pdf_path = session.last_spec_pdf_path
            if pdf_path is None or not pdf_path.exists():
                _, artifacts = await session.generate_spec_preview(force_refresh=True)
                pdf_path = artifacts.pdf_path

            if pdf_path is None or not pdf_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail="Specification PDF is not available for this session.",
                )

        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:  # pragma: no cover - simple health probe
        return {"status": "ok"}

    return app


def run_agui_server(
    settings: AppSettings,
    *,
    host: str = "127.0.0.1",
    port: int = 8081,
    scopes: Sequence[InterviewScope] | None = None,
    allow_origins: Sequence[str] | None = None,
    reload: bool = False,
    log_level: str = "info",
    enable_tracing: bool = False,
    otlp_endpoint: str | None = None,
    capture_sensitive: bool | None = None,
) -> None:
    """Start the AG-UI FastAPI server."""

    if enable_tracing:
        initialize_tracing(endpoint=otlp_endpoint, enable_sensitive_data=capture_sensitive)

    app = create_app(
        settings=settings,
        scopes=scopes,
        allow_origins=allow_origins,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ba_interview_agent.agui",
        description=(
            "Launch the Business Analyst agent as an AG-UI compatible FastAPI service."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the AG-UI server (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Port for the AG-UI server (default: 8081).",
    )
    parser.add_argument(
        "--scope",
        choices=[scope.value for scope in InterviewScope],
        action="append",
        help="Interview scope(s) to register. May be passed multiple times.",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        dest="allow_origin",
        help=(
            "Optional CORS origin(s) to allow. Defaults to '*' if not provided."
        ),
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Run the server in auto-reload development mode.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        help="Logging level for uvicorn (default: info).",
    )
    parser.add_argument(
        "--tracing",
        action="store_true",
        help="Enable OpenTelemetry tracing for the AG-UI server.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    try:
        settings = AppSettings.load()
    except RuntimeError as exc:
        logging.error("Failed to load AppSettings: %s", exc)
        raise SystemExit(1) from exc

    selected_scopes = None
    if args.scope:

        selected_scopes = [InterviewScope(scope) for scope in args.scope]

    tracing_env = os.getenv("MAF_TRACING_ENABLED", "").strip().lower()
    tracing_flag = bool(args.tracing)
    if not tracing_flag and tracing_env:
        tracing_flag = tracing_env in {"1", "true", "yes", "on"}

    capture_env = os.getenv("MAF_TRACING_CAPTURE_SENSITIVE", "").strip().lower()
    capture_sensitive: bool | None = None
    if capture_env:
        capture_sensitive = capture_env in {"1", "true", "yes", "on"}

    run_agui_server(
        settings=settings,
        host=args.host,
        port=args.port,
        scopes=selected_scopes,
        allow_origins=args.allow_origin,
        reload=args.reload,
        log_level=args.log_level,
        enable_tracing=tracing_flag,
        otlp_endpoint=os.getenv("MAF_OTLP_ENDPOINT"),
        capture_sensitive=capture_sensitive,
    )


if __name__ == "__main__":
    main()
