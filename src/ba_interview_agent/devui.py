"""DevUI integration for the Business Analyst interview agent."""

from __future__ import annotations

import argparse
import logging

from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Optional, Sequence
from weakref import WeakKeyDictionary

from agent_framework import (
    AgentRunResponse,
    AgentRunResponseUpdate,
    AgentThread,
    ChatMessage as FrameworkChatMessage,
    Role,
    TextContent,
)
import agent_framework.devui as maf_devui

from .config import AppSettings, InterviewScope
from .interview_agent import (
    BusinessAnalystInterviewAgent,
    CLOSING_PROMPT,
    NEGATIVE_FEEDBACK_RESPONSES,
    TERMINATION_TOKENS,
)
from .workflow_visualization import build_interview_workflow

MessageInput = (
    str
    | FrameworkChatMessage
    | Sequence[str | FrameworkChatMessage]
    | None
)


@dataclass(slots=True)
class _SessionState:
    """Tracks per-thread interview state for the DevUI agent."""

    agent: BusinessAnalystInterviewAgent
    completed: bool = False
    awaiting_closing_feedback: bool = False
    pending_spec_text: Optional[str] = None
    final_message: Optional[str] = None


class BusinessAnalystDevUIAgent:
    """Adapter that exposes the interview workflow as a DevUI entity."""

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
        self._sessions: WeakKeyDictionary[AgentThread, _SessionState] = (
            WeakKeyDictionary()
        )

    # Properties required by AgentProtocol
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
        session_state: Optional[_SessionState] = self._sessions.get(thread)
        user_text = self._extract_user_text(messages)

        if session_state is None:
            session_state = self._start_session()
            kickoff = await session_state.agent.kickoff()
            session_state.agent.record_question(kickoff)
            await self._append_assistant_message(thread, kickoff)
            yield self._as_update(kickoff)
            self._sessions[thread] = session_state
            if not user_text:
                return

        if not user_text:
            return

        await self._append_user_message(thread, user_text)
        assert session_state is not None  # for type checkers
        if session_state.awaiting_closing_feedback:
            closing_updates = await self._handle_closing_feedback(
                thread,
                session_state,
                user_text,
            )
            for update in closing_updates:
                yield self._as_update(update)
            return
        if user_text.strip().lower() in TERMINATION_TOKENS:
            final_message = await self._finalize_session(thread, session_state)
            yield self._as_update(final_message)
            return

        follow_up = await session_state.agent.next_question(user_text)
        if follow_up is None:
            final_message = await self._finalize_session(thread, session_state)
            yield self._as_update(final_message)
            return

        session_state.agent.record_question(follow_up)
        await self._append_assistant_message(thread, follow_up)
        yield self._as_update(follow_up)

    def _start_session(self) -> _SessionState:
        agent = BusinessAnalystInterviewAgent(
            settings=self._settings,
            scope=self._scope,
        )
        return _SessionState(agent=agent)

    async def _finalize_session(
        self,
        thread: AgentThread,
        session: _SessionState,
    ) -> str:
        if session.completed and session.final_message:
            return session.final_message
        if session.awaiting_closing_feedback and session.final_message:
            return session.final_message

        spec_text = await session.agent.summarize()
        session.pending_spec_text = spec_text
        message = f"{spec_text}\n\n{CLOSING_PROMPT}"
        await self._append_assistant_message(thread, message)
        session.awaiting_closing_feedback = True
        session.final_message = message
        return message

    async def _handle_closing_feedback(
        self,
        thread: AgentThread,
        session: _SessionState,
        user_text: str,
    ) -> list[str]:
        updates: list[str] = []
        session.agent.record_question(
            CLOSING_PROMPT,
            answer=user_text,
        )
        normalized = user_text.strip().lower()
        wants_update = normalized not in NEGATIVE_FEEDBACK_RESPONSES
        if wants_update:
            acknowledgement = (
                "BA Agent: Thanks! I'll incorporate that feedback into the "
                "specification."
            )
            await self._append_assistant_message(thread, acknowledgement)
            updates.append(acknowledgement)
            updated_spec = await session.agent.summarize()
            session.pending_spec_text = updated_spec
            spec_message = (
                "Updated functional specification draft:\n\n"
                f"{updated_spec}"
            )
            await self._append_assistant_message(thread, spec_message)
            updates.append(spec_message)
            final_spec = updated_spec
        else:
            acknowledgement = (
                "BA Agent: Understood. We'll keep the specification as-is."
            )
            await self._append_assistant_message(thread, acknowledgement)
            updates.append(acknowledgement)
            final_spec = session.pending_spec_text
            if final_spec is None:
                final_spec = await session.agent.summarize()

        session.pending_spec_text = final_spec
        artifacts = session.agent.export_spec(final_spec)
        output_path = artifacts.markdown_path
        record_id = session.agent.persist_transcript(
            spec_text=final_spec,
            spec_path=output_path,
        )
        closing_lines = [
            "Interview complete. Functional specification saved to:",
            f" - {output_path}",
        ]
        if artifacts.pdf_path is not None:
            closing_lines.append(f" - {artifacts.pdf_path}")
        if record_id:
            closing_lines.append(f"Transcript id: {record_id}")
        closing_message = "\n".join(closing_lines)
        await self._append_assistant_message(thread, closing_message)
        updates.append(closing_message)
        session.completed = True
        session.awaiting_closing_feedback = False
        session.final_message = closing_message
        self._sessions.pop(thread, None)
        return updates

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


def run_devui(
    settings: AppSettings,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    auto_open: bool = True,
    scopes: Sequence[InterviewScope] | None = None,
    tracing_enabled: bool = False,
) -> None:
    """Launch the DevUI server with interview agent entities."""

    target_scopes = list(scopes) if scopes else list(InterviewScope)
    entities = [
        BusinessAnalystDevUIAgent(settings=settings, scope=scope)
        for scope in target_scopes
    ]
    workflow_entity = build_interview_workflow()
    entities.append(workflow_entity)
    maf_devui.serve(
        entities=entities,
        host=host,
        port=port,
        auto_open=auto_open,
        tracing_enabled=tracing_enabled,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ba_interview_agent.devui",
        description=(
            "Launch the Business Analyst agent in the Agent Framework DevUI."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the DevUI server (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for the DevUI server (default: 8080).",
    )
    parser.add_argument(
        "--scope",
        choices=[scope.value for scope in InterviewScope],
        action="append",
        help="Interview scope(s) to register. May be passed multiple times.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the DevUI in a browser window.",
    )
    parser.add_argument(
        "--tracing",
        action="store_true",
        help="Enable OpenTelemetry tracing for the DevUI server.",
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
    run_devui(
        settings=settings,
        host=args.host,
        port=args.port,
        auto_open=not args.no_browser,
        scopes=selected_scopes,
        tracing_enabled=args.tracing,
    )


if __name__ == "__main__":
    main()
