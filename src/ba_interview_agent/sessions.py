"""Shared session orchestration for Business Analyst interviews."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .config import AppSettings, InterviewScope
from .interview_agent import (
    BusinessAnalystInterviewAgent,
    CLOSING_PROMPT,
    NEGATIVE_FEEDBACK_RESPONSES,
    TERMINATION_TOKENS,
    SpecificationArtifacts,
)


@dataclass(slots=True)
class BusinessAnalystSession:
    """Encapsulates the state machine for a single interview run."""

    agent: BusinessAnalystInterviewAgent
    completed: bool = False
    awaiting_closing_feedback: bool = False
    pending_spec_text: Optional[str] = None
    final_message: Optional[str] = None
    last_spec_text: Optional[str] = None
    last_spec_markdown_path: Optional[Path] = None
    last_spec_pdf_path: Optional[Path] = None

    @classmethod
    def create(
        cls, settings: AppSettings, scope: InterviewScope
    ) -> "BusinessAnalystSession":
        return cls(
            agent=BusinessAnalystInterviewAgent(settings=settings, scope=scope)
        )

    async def kickoff(self) -> str:
        """Start the interview and return the initial question."""

        question = await self.agent.kickoff()
        self.agent.record_question(question)
        return question

    async def handle_user_message(self, user_text: str) -> List[str]:
        """Process a user response and return assistant utterances."""

        updates: List[str] = []
        normalized = user_text.strip()
        if not normalized:
            return updates

        if self.awaiting_closing_feedback:
            updates.extend(await self._handle_closing_feedback(normalized))
            return updates

        if normalized.lower() in TERMINATION_TOKENS:
            updates.append(await self._finalize_session())
            return updates

        follow_up = await self.agent.next_question(normalized)
        if follow_up is None:
            updates.append(await self._finalize_session())
            return updates

        self.agent.record_question(follow_up)
        updates.append(follow_up)
        return updates

    async def generate_spec_preview(
        self,
        *,
        force_refresh: bool = False,
    ) -> tuple[str, SpecificationArtifacts]:
        """Produce (or reuse) the latest specification artifacts."""

        reuse_available = (
            not force_refresh
            and self.last_spec_text is not None
            and self.last_spec_markdown_path is not None
            and self.last_spec_markdown_path.exists()
        )
        if reuse_available:
            artifacts = SpecificationArtifacts(
                markdown_path=self.last_spec_markdown_path,
                pdf_path=self.last_spec_pdf_path,
            )
            return self.last_spec_text, artifacts

        spec_text = await self.agent.summarize()
        artifacts = self.agent.export_spec(spec_text)

        self.pending_spec_text = spec_text
        self.last_spec_text = spec_text
        self.last_spec_markdown_path = artifacts.markdown_path
        self.last_spec_pdf_path = artifacts.pdf_path

        return spec_text, artifacts

    async def _finalize_session(self) -> str:
        if self.completed and self.final_message:
            return self.final_message
        if self.awaiting_closing_feedback and self.final_message:
            return self.final_message

        spec_text = await self.agent.summarize()
        self.pending_spec_text = spec_text
        message = f"{spec_text}\n\n{CLOSING_PROMPT}"
        self.awaiting_closing_feedback = True
        self.final_message = message
        return message

    async def _handle_closing_feedback(self, user_text: str) -> List[str]:
        updates: List[str] = []
        self.agent.record_question(CLOSING_PROMPT, answer=user_text)

        wants_update = user_text.lower() not in NEGATIVE_FEEDBACK_RESPONSES
        if wants_update:
            acknowledgement = (
                "BA Agent: Thanks! I'll incorporate that feedback into the specification."
            )
            updates.append(acknowledgement)
            updated_spec = await self.agent.summarize()
            spec_message = (
                "Updated functional specification draft:\n\n"
                f"{updated_spec}"
            )
            updates.append(spec_message)
            final_spec = updated_spec
        else:
            acknowledgement = (
                "BA Agent: Understood. We'll keep the specification as-is."
            )
            updates.append(acknowledgement)
            final_spec = self.pending_spec_text
            if final_spec is None:
                final_spec = await self.agent.summarize()

        self.pending_spec_text = final_spec

        artifacts = self.agent.export_spec(final_spec)
        output_path = artifacts.markdown_path
        record_id = self.agent.persist_transcript(
            spec_text=final_spec,
            spec_path=output_path,
        )
        self.last_spec_text = final_spec
        self.last_spec_markdown_path = artifacts.markdown_path
        self.last_spec_pdf_path = artifacts.pdf_path
        closing_lines = [
            "Interview complete. Functional specification saved to:",
            f" - {output_path}",
        ]
        if artifacts.pdf_path is not None:
            closing_lines.append(f" - {artifacts.pdf_path}")
        if record_id:
            closing_lines.append(f"Transcript id: {record_id}")
        closing_message = "\n".join(closing_lines)
        updates.append(closing_message)
        self.completed = True
        self.awaiting_closing_feedback = False
        self.final_message = closing_message
        return updates
