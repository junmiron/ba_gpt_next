"""FastAPI entrypoint that exposes the interview agent via AG-UI."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import json
from contextlib import suppress
from dataclasses import asdict, is_dataclass, replace
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, Iterable, Mapping, Optional, Sequence
from weakref import WeakKeyDictionary

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
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from .config import AppSettings, InterviewScope
from .sessions import BusinessAnalystSession
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
        # Minimal surfaces expected by AgentFrameworkAgent orchestrators
        self.chat_options = SimpleNamespace(tools=None, response_format=None)
        self.chat_client = SimpleNamespace(
            function_invocation_configuration=None
        )

    @property
    def id(self) -> str:
        return self._id

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
        **_: object,
    ) -> AgentRunResponse:
        updates: list[AgentRunResponseUpdate] = []
        async for update in self.run_stream(messages, thread=thread):
            updates.append(update)
        if updates:
            return AgentRunResponse.from_agent_run_response_updates(updates)
        return AgentRunResponse()

    async def run_stream(
        self,
        messages: MessageInput = None,
        *,
        thread: AgentThread | None = None,
        **_: object,
    ) -> AsyncIterator[AgentRunResponseUpdate]:
        thread = thread or self.get_new_thread()
        session_state: Optional[BusinessAnalystSession] = self._sessions.get(thread)
        user_text = self._extract_user_text(messages)

        if session_state is None:
            session_state = BusinessAnalystSession.create(
                settings=self._settings,
                scope=self._scope,
            )
            kickoff = await session_state.kickoff()
            await self._append_assistant_message(thread, kickoff)
            yield self._as_update(kickoff)
            self._sessions[thread] = session_state
            if not user_text:
                return

        if not user_text:
            return

        await self._append_user_message(thread, user_text)
        assert session_state is not None  # for type checkers
        responses = await session_state.handle_user_message(user_text)
        for update in responses:
            await self._append_assistant_message(thread, update)
            yield self._as_update(update)

        if session_state.completed:
            self._sessions.pop(thread, None)

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


class TestAgentRequest(BaseModel):
    seed: Optional[int] = None
    persona: Optional[Dict[str, Any]] = None


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
    for scope in target_scopes:
        agent = BusinessAnalystAGUIAgent(settings=settings, scope=scope)
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

        try:
            responder = await asyncio.wait_for(
                SimulatedStakeholderResponder.create(
                    settings=runtime_settings,
                    scope=scope,
                    seed=payload.seed,
                    persona_override=payload.persona,
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
            await enqueue(event)

        async def producer() -> None:
            try:
                await enqueue(
                    {
                        "type": "status",
                        "content": "Preparing simulated stakeholder persona...",
                    }
                )
                responder = await asyncio.wait_for(
                    SimulatedStakeholderResponder.create(
                        settings=runtime_settings,
                        scope=scope,
                        seed=payload.seed,
                        persona_override=payload.persona,
                    ),
                    timeout=test_agent_timeout,
                )
                result = await asyncio.wait_for(
                    simulate_interview(
                        settings=runtime_settings,
                        scope=scope,
                        responder=responder,
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
) -> None:
    """Start the AG-UI FastAPI server."""

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

    run_agui_server(
        settings=settings,
        host=args.host,
        port=args.port,
        scopes=selected_scopes,
        allow_origins=args.allow_origin,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
