"""Interview orchestration logic for the Business Analyst agent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    cast,
)

from .as_is_agent import AsIsDerivationAgent, AsIsProcess, ConsoleAsIsReviewer
from .config import AppSettings, InterviewScope
from .diagram_agent import DiagramExportError, ProcessDiagramAgent
from .maf_client import ChatMessage, MAFChatClient
from .pdf_exporter import PDFExportError, SpecificationPDFExporter
from .prompts import GUIDANCE, PROMPT_LIBRARY, ScopePromptPack
from .spec_review_agent import (
    FollowUpQuestion,
    FunctionalSpecificationReviewAgent,
    SpecificationReview,
)
from .to_be_agent import (
    ConsoleToBeReviewer,
    ToBeDerivationAgent,
    ToBeProcess,
)
from .transcript_store import TranscriptRepository

TERMINATION_TOKENS = {"done", "no further questions", "[end]"}

CLOSING_PROMPT = (
    "Before we wrap up, is there anything you'd like to add or "
    "change in the specification?"
)

AS_IS_REVIEW_PROMPT = (
    "Do these bullet points capture the current AS-IS state accurately? "
    "Please add, edit, or approve them so we document today's reality "
    "correctly."
)

TO_BE_REVIEW_PROMPT = (
    "Do these bullet points reflect the desired TO-BE experience? Please "
    "add, edit, or approve them so we capture the future-state vision "
    "correctly."
)

NEGATIVE_FEEDBACK_RESPONSES = {
    "",
    "no",
    "nope",
    "nah",
    "nothing",
    "none",
    "no thanks",
    "no thank you",
    "no changes",
    "no change",
    "no additions",
    "nothing else",
    "all good",
    "we are good",
    "we're good",
    "we good",
    "looks good",
    "looks great",
    "looks fine",
    "good to go",
    "that is all",
    "that's all",
    "approve",
    "approved",
}.union({token.lower() for token in TERMINATION_TOKENS})

logger = logging.getLogger(__name__)

SUMMARY_MAX_ATTEMPTS = 3

ProcessSignature = Tuple[str, Tuple[str, ...], Tuple[str, ...]]
SummarySignature = Tuple[int, Tuple[str, ...], Tuple[ProcessSignature, ...]]
ProcessModel = AsIsProcess | ToBeProcess
ProcessT = TypeVar("ProcessT", AsIsProcess, ToBeProcess)


@dataclass(frozen=True, slots=True)
class InterviewSubject:
    """Represents a required interview subject."""

    name: str
    focus: str


SUBJECT_PLAN: List[InterviewSubject] = [
    InterviewSubject(
        name="Product Overview",
        focus=(
            "Clarify the product vision, target users, primary goals, "
            "timeline expectations, and differentiators."
        ),
    ),
    InterviewSubject(
        name="KPI & Success Metrics",
        focus=(
            "Identify measurable outcomes, target KPIs, success criteria, "
            "and how progress will be tracked."
        ),
    ),
    InterviewSubject(
        name="AS IS",
        focus=(
            "Document the current state, existing processes, manual "
            "workarounds, and pain points."
        ),
    ),
    InterviewSubject(
        name="Scope In and Out",
        focus=(
            "Capture what capabilities are required, what is explicitly "
            "excluded, and priority boundaries."
        ),
    ),
    InterviewSubject(
        name="Non-Functional Requirements",
        focus=(
            "Gather expectations for performance, security, compliance, "
            "availability, scalability, and reliability."
        ),
    ),
    InterviewSubject(
        name="User Roles & Personas",
        focus=(
            "Understand the personas, their responsibilities, goals, and "
            "access needs."
        ),
    ),
    InterviewSubject(
        name="Integrations & External Systems",
        focus=(
            "Determine required integrations, data exchanges, and system "
            "dependencies."
        ),
    ),
    InterviewSubject(
        name="Constraints & Assumptions",
        focus=(
            "Surface budget, timeline, regulatory, technical, and business "
            "constraints along with key assumptions."
        ),
    ),
    InterviewSubject(
        name="Dependencies & Risks",
        focus=(
            "Identify upstream or downstream dependencies, risks, and "
            "mitigation considerations."
        ),
    ),
]


@dataclass(slots=True)
class QuestionDecision:
    """Represents the model's decision about the next subject question."""

    question: str
    subject_complete: bool
    notes: Optional[str] = None


@dataclass(slots=True)
class InterviewTurn:
    """Container for a single question/answer pair."""

    question: str
    answer: str
    subject: str = ""


def _empty_turns() -> List["InterviewTurn"]:
    return []


@dataclass(slots=True)
class InterviewTranscript:
    """Represents the evolving interview conversation."""

    scope: InterviewScope
    turns: List["InterviewTurn"] = field(default_factory=_empty_turns)
    initial_user_prompt: str | None = None
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def as_messages(self) -> List[ChatMessage]:
        """Flatten the transcript into chat messages."""
        messages: List[ChatMessage] = []
        if self.initial_user_prompt:
            messages.append(
                ChatMessage(role="user", content=self.initial_user_prompt)
            )
        for turn in self.turns:
            question_content = turn.question
            if turn.subject:
                question_content = (
                    f"[Subject: {turn.subject}] {question_content}"
                )
            messages.append(
                ChatMessage(role="assistant", content=question_content)
            )
            messages.append(ChatMessage(role="user", content=turn.answer))
        return messages

    def append(self, question: str, answer: str, *, subject: str = "") -> None:
        self.turns.append(
            InterviewTurn(question=question, answer=answer, subject=subject)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope.value,
            "started_at": self.started_at.isoformat(),
            "initial_user_prompt": self.initial_user_prompt,
            "turns": [
                {
                    "question": turn.question,
                    "answer": turn.answer,
                    "subject": turn.subject,
                }
                for turn in self.turns
            ],
        }


@dataclass(slots=True)
class SpecificationArtifacts:
    """Collection of specification export artifacts."""

    markdown_path: Path
    pdf_path: Path | None = None


class BusinessAnalystInterviewAgent:
    """Coordinates the interview flow and delegates LLM reasoning to MAF."""

    def __init__(self, settings: AppSettings, scope: InterviewScope) -> None:
        self._settings = settings
        self._scope = scope
        self._prompt_pack: ScopePromptPack = PROMPT_LIBRARY[scope]
        self._chat_client = MAFChatClient(settings.model)
        self._transcript = InterviewTranscript(scope=scope)
        self._system_guidance = "\n\n".join(
            [GUIDANCE.system, GUIDANCE.interviewer]
        )
        self._transcript_repo: TranscriptRepository = TranscriptRepository(
            archive_path=settings.transcript_log,
            redis_url=settings.redis_url,
        )
        self._subject_max_questions = settings.subject_max_questions
        self._subject_question_counts: List[int] = [0] * len(SUBJECT_PLAN)
        self._subjects_completed: List[bool] = [False] * len(SUBJECT_PLAN)
        self._current_subject_idx = 0
        self._pending_subject_index: Optional[int] = None
        self._active_subject_index: Optional[int] = None
        self._review_agent = FunctionalSpecificationReviewAgent(
            settings=settings,
            scope=scope,
            subjects=[subject.name for subject in SUBJECT_PLAN],
        )
        self._as_is_agent = AsIsDerivationAgent(
            settings=settings,
            scope=scope,
        )
        self._as_is_reviewer = ConsoleAsIsReviewer()
        self._to_be_agent = ToBeDerivationAgent(
            settings=settings,
            scope=scope,
        )
        self._to_be_reviewer = ConsoleToBeReviewer()
        try:
            self._diagram_agent: ProcessDiagramAgent | None = (
                ProcessDiagramAgent(
                    output_dir=settings.output_dir / "diagrams",
                )
            )
        except DiagramExportError:
            logger.warning(
                "Graphviz support unavailable; diagram rendering disabled."
            )
            self._diagram_agent = None
        self._summarization_corrections: List[str] = []
        self._approved_as_is_items: List[str] | None = None
        self._approved_as_is_processes: List[AsIsProcess] | None = None
        self._as_is_confirmed_turns = 0
        self._as_is_signature: SummarySignature | None = None
        self._approved_future_state_items: List[str] | None = None
        self._approved_future_state_processes: List[ToBeProcess] | None = None
        self._future_state_confirmed_turns = 0
        self._future_state_signature: SummarySignature | None = None
        self._latest_summary_data: Dict[str, Any] | None = None
        self._latest_spec_markdown: str | None = None
        self._pdf_exporter = SpecificationPDFExporter(
            asset_root=settings.output_dir
        )

    async def kickoff(self) -> str:
        """Generate the first interview question."""
        self._transcript.initial_user_prompt = self._prompt_pack.kickoff
        question = await self._generate_next_question()
        if question is None:
            raise RuntimeError("Unable to generate kickoff question.")
        return question

    async def next_question(self, latest_answer: str) -> str | None:
        """Ask the LLM for the next probing question."""
        if not self._transcript.turns:
            raise RuntimeError(
                "Call kickoff() before requesting follow-up questions."
            )
        self._transcript.turns[-1].answer = latest_answer
        self._handle_post_answer()
        return await self._generate_next_question()

    async def summarize(self) -> str:
        """Produce a functional specification draft using the transcript."""
        guidance = ChatMessage(role="system", content=self._system_guidance)
        transcript_messages = self._transcript.as_messages()
        retry_note = ""
        raw_content = ""
        attempts = 0
        while attempts < SUMMARY_MAX_ATTEMPTS:
            summarization_prompt = self._build_summarization_prompt(
                retry_note=retry_note
            )
            payload: List[ChatMessage] = [guidance]
            payload.extend(transcript_messages)
            payload.append(
                ChatMessage(role="user", content=summarization_prompt)
            )
            response = await self._chat_client.complete(payload)
            raw_content = response.content.strip()
            summary_data = self._parse_structured_summary(raw_content)
            if summary_data is not None:
                self._latest_summary_data = summary_data
                spec_text = self._render_structured_summary(summary_data)
                self._latest_spec_markdown = spec_text
                return spec_text
            self._latest_summary_data = None
            retry_note = (
                "Your previous response was not valid JSON matching the "
                "required schema. Respond with JSON only—no markdown or "
                "surrounding commentary."
            )
            attempts += 1
        self._latest_summary_data = None
        self._latest_spec_markdown = raw_content
        return raw_content

    async def review_spec(self, spec_text: str) -> SpecificationReview:
        """Run the specification draft through the review agent."""

        return await self._review_agent.review(spec_text)

    def apply_review_feedback(self, review: SpecificationReview) -> None:
        """Capture reviewer guidance for future summarization passes."""

        if review.missing_subjects:
            subject_summary = ", ".join(review.missing_subjects)
            self._add_summarization_correction(
                "Ensure the specification explicitly addresses: "
                f"{subject_summary}."
            )
        if not review.table_valid:
            if review.table_feedback:
                instruction = review.table_feedback.strip()
            else:
                instruction = (
                    "Ensure the 'Functional Requirements' table follows "
                    "the format 'Spec ID | Specification Description | "
                    "Business Rules/Data Dependency' with sequential IDs "
                    "(FR-1, FR-2, ...)."
                )
            self._add_summarization_correction(instruction)

    def _build_summarization_prompt(self, *, retry_note: str = "") -> str:
        prompt = self._prompt_pack.summarization
        if self._summarization_corrections:
            correction_lines = "\n".join(
                f"- {item}" for item in self._summarization_corrections
            )
            prompt = (
                f"{prompt}\n\nAdditional guidance for this draft:\n"
                f"{correction_lines}"
            )
        if retry_note:
            prompt = f"{prompt}\n\n{retry_note}"
        return prompt

    def _parse_structured_summary(
        self,
        raw: str,
    ) -> Optional[Dict[str, Any]]:
        data = self._extract_json_object(raw)
        if data is None:
            return None
        return self._normalize_structured_summary(data)

    async def finalize_current_summary(self) -> str:
        if self._latest_summary_data is None:
            if self._latest_spec_markdown is None:
                raise RuntimeError("No summary available to finalize.")
            return self._latest_spec_markdown
        await self._apply_as_is_confirmation(self._latest_summary_data)
        await self._apply_future_state_confirmation(self._latest_summary_data)
        self._generate_process_diagrams(self._latest_summary_data)
        finalized = self._render_structured_summary(self._latest_summary_data)
        self._latest_spec_markdown = finalized
        return finalized

    @staticmethod
    def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
        text = raw.strip()
        if not text:
            return None
        candidate = text
        if not candidate.lstrip().startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            candidate = text[start:end + 1]
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return cast(Dict[str, Any], payload)

    @staticmethod
    def _serialize_processes(
        processes: Sequence[ProcessModel],
    ) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for process in processes:
            serialized.append(
                {
                    "name": process.name,
                    "happy_path": list(process.happy_path),
                    "unhappy_path": list(process.unhappy_path),
                }
            )
        return serialized

    @classmethod
    def _coerce_processes(
        cls,
        value: Any,
        *,
        constructor: Type[ProcessT],
        fallback_step: str | None = None,
    ) -> List[ProcessT]:
        processes: List[ProcessT] = []
        if not isinstance(value, list):
            return processes
        sanitizer = cls._sanitize_process_steps
        for entry in cast(List[Any], value):
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(Dict[str, Any], entry)
            name = str(entry_dict.get("name", "")).strip()
            if not name:
                continue
            happy_steps = sanitizer(entry_dict.get("happy_path"))
            unhappy_steps = sanitizer(entry_dict.get("unhappy_path"))
            if not happy_steps and not unhappy_steps:
                if fallback_step is None:
                    continue
                happy_steps = [fallback_step]
            processes.append(
                constructor(
                    name=name.replace("|", "/"),
                    happy_path=happy_steps,
                    unhappy_path=unhappy_steps,
                )
            )
        return processes

    @classmethod
    def _coerce_as_is_processes(cls, value: Any) -> List[AsIsProcess]:
        return cls._coerce_processes(
            value,
            constructor=AsIsProcess,
            fallback_step=None,
        )

    @classmethod
    def _coerce_future_processes(cls, value: Any) -> List[ToBeProcess]:
        return cls._coerce_processes(
            value,
            constructor=ToBeProcess,
            fallback_step="Future-state steps pending definition.",
        )

    @staticmethod
    def _sanitize_process_steps(raw_steps: Any) -> List[str]:
        steps: List[str] = []
        if isinstance(raw_steps, list):
            for entry in cast(List[Any], raw_steps):
                text = str(entry).strip()
                if text:
                    sanitized = text.replace("|", "/").replace("\n", " ")
                    steps.append(sanitized)
        elif isinstance(raw_steps, str):
            segments = [segment.strip() for segment in raw_steps.split(";")]
            for segment in segments:
                if segment:
                    steps.append(segment.replace("|", "/"))
        return steps

    @staticmethod
    def _process_signature(
        processes: Sequence[ProcessModel],
    ) -> Tuple[ProcessSignature, ...]:
        signature: List[ProcessSignature] = []
        for process in processes:
            signature.append(
                (
                    process.name.strip(),
                    tuple(step.strip() for step in process.happy_path),
                    tuple(step.strip() for step in process.unhappy_path),
                )
            )
        return tuple(signature)

    @staticmethod
    def _items_signature(items: Sequence[str]) -> Tuple[str, ...]:
        return tuple(item.strip() for item in items)

    @classmethod
    def _compose_process_summary_signature(
        cls,
        *,
        turn_count: int,
        items: Sequence[str],
        processes: Sequence[ProcessModel],
    ) -> SummarySignature:
        return (
            turn_count,
            cls._items_signature(items),
            cls._process_signature(processes),
        )

    @classmethod
    def _items_equivalent(
        cls,
        first: Sequence[str],
        second: Sequence[str],
    ) -> bool:
        return cls._items_signature(first) == cls._items_signature(second)

    @classmethod
    def _processes_equivalent(
        cls,
        first: Sequence[ProcessModel],
        second: Sequence[ProcessModel],
    ) -> bool:
        return cls._process_signature(first) == cls._process_signature(second)

    async def _apply_as_is_confirmation(
        self,
        summary_data: Dict[str, Any],
    ) -> None:
        fallback_items_raw = cast(
            List[str],
            summary_data.get("current_state", []),
        )
        fallback_items = list(fallback_items_raw)
        fallback_processes = self._coerce_as_is_processes(
            summary_data.get("current_processes")
        )
        approved_items = self._approved_as_is_items
        approved_processes = self._approved_as_is_processes
        if (
            approved_items is not None
            and approved_processes is not None
            and len(self._transcript.turns) == self._as_is_confirmed_turns
        ):
            summary_data["current_state"] = list(approved_items)
            summary_data["current_processes"] = (
                self._serialize_processes(approved_processes)
            )
            return
        try:
            conversation_excerpt = self._conversation_excerpt()
            derived_draft = await self._as_is_agent.derive(
                summary_data=summary_data,
                conversation_excerpt=conversation_excerpt,
                fallback_items=fallback_items,
                fallback_processes=fallback_processes,
            )
            derived_items = list(derived_draft.items)
            derived_processes = [
                AsIsProcess(
                    name=process.name,
                    happy_path=list(process.happy_path),
                    unhappy_path=list(process.unhappy_path),
                )
                for process in derived_draft.processes
            ]
            current_signature = self._compose_process_summary_signature(
                turn_count=len(self._transcript.turns),
                items=derived_items,
                processes=derived_processes,
            )
            if (
                approved_items is not None
                and approved_processes is not None
                and self._as_is_signature == current_signature
            ):
                summary_data["current_state"] = list(approved_items)
                summary_data["current_processes"] = (
                    self._serialize_processes(approved_processes)
                )
                return
            if (
                approved_items is not None
                and approved_processes is not None
                and self._items_equivalent(approved_items, derived_items)
                and self._processes_equivalent(
                    approved_processes, derived_processes
                )
            ):
                summary_data["current_state"] = list(approved_items)
                summary_data["current_processes"] = (
                    self._serialize_processes(approved_processes)
                )
                self._as_is_signature = current_signature
                self._as_is_confirmed_turns = len(self._transcript.turns)
                return
            preview_data = dict(summary_data)
            preview_data["current_state"] = list(derived_items)
            preview_data["current_processes"] = (
                self._serialize_processes(derived_processes)
            )
            spec_preview = self._render_structured_summary(preview_data)
            review = await self._as_is_reviewer.confirm_as_is(
                proposed_items=derived_items,
                proposed_processes=derived_processes,
                spec_text=spec_preview,
                question=AS_IS_REVIEW_PROMPT,
            )
        except Exception:  # noqa: BLE001 # pylint: disable=broad-except
            logger.exception(
                "Failed to derive stakeholder-approved AS-IS summary; "
                "using fallback details.",
            )
            summary_data["current_state"] = fallback_items
            summary_data["current_processes"] = (
                self._serialize_processes(fallback_processes)
            )
            return
        approved_items = list(review.items)
        approved_processes = [
            AsIsProcess(
                name=process.name,
                happy_path=list(process.happy_path),
                unhappy_path=list(process.unhappy_path),
            )
            for process in review.processes
        ]
        summary_data["current_state"] = list(approved_items)
        summary_data["current_processes"] = self._serialize_processes(
            approved_processes
        )
        self._approved_as_is_items = list(approved_items)
        self._approved_as_is_processes = approved_processes
        self._as_is_confirmed_turns = len(self._transcript.turns)
        self._as_is_signature = self._compose_process_summary_signature(
            turn_count=len(self._transcript.turns),
            items=approved_items,
            processes=approved_processes,
        )
        if review.stakeholder_comment:
            logger.info(
                "Stakeholder AS-IS confirmation: %s",
                review.stakeholder_comment,
            )

    async def _apply_future_state_confirmation(
        self,
        summary_data: Dict[str, Any],
    ) -> None:
        fallback_items_raw = cast(
            List[str],
            summary_data.get("future_state", []),
        )
        fallback_items = list(fallback_items_raw)
        fallback_processes = self._coerce_future_processes(
            summary_data.get("future_processes")
        )
        approved_items = self._approved_future_state_items
        approved_processes = self._approved_future_state_processes
        if (
            approved_items is not None
            and approved_processes is not None
            and len(self._transcript.turns)
            == self._future_state_confirmed_turns
        ):
            summary_data["future_state"] = list(approved_items)
            summary_data["future_processes"] = self._serialize_processes(
                approved_processes
            )
            return
        try:
            conversation_excerpt = self._conversation_excerpt()
            derived_draft = await self._to_be_agent.derive(
                summary_data=summary_data,
                conversation_excerpt=conversation_excerpt,
                fallback_items=fallback_items,
                fallback_processes=fallback_processes,
            )
            derived_items = list(derived_draft.items)
            derived_processes = [
                ToBeProcess(
                    name=process.name,
                    happy_path=list(process.happy_path),
                    unhappy_path=list(process.unhappy_path),
                )
                for process in derived_draft.processes
            ]
            current_signature = self._compose_process_summary_signature(
                turn_count=len(self._transcript.turns),
                items=derived_items,
                processes=derived_processes,
            )
            if (
                approved_items is not None
                and approved_processes is not None
                and self._future_state_signature == current_signature
            ):
                summary_data["future_state"] = list(approved_items)
                summary_data["future_processes"] = self._serialize_processes(
                    approved_processes
                )
                return
            if (
                approved_items is not None
                and approved_processes is not None
                and self._items_equivalent(approved_items, derived_items)
                and self._processes_equivalent(
                    approved_processes, derived_processes
                )
            ):
                summary_data["future_state"] = list(approved_items)
                summary_data["future_processes"] = self._serialize_processes(
                    approved_processes
                )
                self._future_state_signature = current_signature
                self._future_state_confirmed_turns = len(
                    self._transcript.turns
                )
                return
            preview_data = dict(summary_data)
            preview_data["future_state"] = list(derived_items)
            preview_data["future_processes"] = self._serialize_processes(
                derived_processes
            )
            spec_preview = self._render_structured_summary(preview_data)
            review = await self._to_be_reviewer.confirm_to_be(
                proposed_items=derived_items,
                proposed_processes=derived_processes,
                spec_text=spec_preview,
                question=TO_BE_REVIEW_PROMPT,
            )
        except Exception:  # noqa: BLE001 # pylint: disable=broad-except
            logger.exception(
                "Failed to derive stakeholder-approved TO-BE summary; "
                "using fallback details.",
            )
            summary_data["future_state"] = fallback_items
            summary_data["future_processes"] = self._serialize_processes(
                fallback_processes
            )
            return
        approved_items = [str(item).strip() for item in review.items if item]
        approved_processes = [
            ToBeProcess(
                name=process.name,
                happy_path=list(process.happy_path),
                unhappy_path=list(process.unhappy_path),
            )
            for process in review.processes
        ]
        summary_data["future_state"] = list(approved_items)
        summary_data["future_processes"] = self._serialize_processes(
            approved_processes
        )
        self._approved_future_state_items = list(approved_items)
        self._approved_future_state_processes = approved_processes
        self._future_state_confirmed_turns = len(self._transcript.turns)
        self._future_state_signature = self._compose_process_summary_signature(
            turn_count=len(self._transcript.turns),
            items=approved_items,
            processes=approved_processes,
        )
        if review.stakeholder_comment:
            logger.info(
                "Stakeholder TO-BE confirmation: %s",
                review.stakeholder_comment,
            )

    def _conversation_excerpt(
        self,
        *,
        max_turns: int = 6,
        max_chars: int = 1500,
    ) -> str:
        """Collect a concise excerpt of recent Q&A for AS-IS derivation."""

        if not self._transcript.turns:
            return ""
        selected_turns = self._transcript.turns[-max_turns:]
        lines: List[str] = []
        for turn in selected_turns:
            question = turn.question.strip()
            answer = turn.answer.strip()
            if question:
                lines.append(f"Q: {question}")
            if answer:
                lines.append(f"A: {answer}")
        excerpt = "\n".join(lines).strip()
        if len(excerpt) <= max_chars:
            return excerpt
        truncated = excerpt[: max_chars - 5].rstrip()
        return f"{truncated}\n[...]"

    @staticmethod
    def _to_clean_string(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return str(value).strip()

    @classmethod
    def _sanitize_string_list(
        cls,
        value: Any,
        *,
        default: List[str],
    ) -> List[str]:
        items: List[str] = []
        if isinstance(value, list):
            for element in cast(List[Any], value):
                text = cls._to_clean_string(element)
                if text:
                    items.append(text)
        elif isinstance(value, str):
            text = value.strip()
            if text:
                items.append(text)
        if not items:
            items = default
        return items

    def _normalize_structured_summary(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        title = (
            self._to_clean_string(payload.get("title"))
            or "Untitled Initiative"
        )
        project_overview = (
            self._to_clean_string(payload.get("project_overview"))
            or "Pending clarification."
        )
        project_objective = self._to_clean_string(
            payload.get("project_objective")
        ) or "Pending clarification."
        scope_raw = payload.get("scope")
        scope_data: Dict[str, Any]
        if isinstance(scope_raw, dict):
            scope_data = cast(Dict[str, Any], scope_raw)
        else:
            scope_data = {}
        scope_overview = self._to_clean_string(scope_data.get("overview"))
        scope_in_scope = self._to_clean_string(scope_data.get("in_scope"))
        scope_out_scope = self._to_clean_string(scope_data.get("out_of_scope"))
        if not scope_in_scope:
            scope_in_scope = "Pending clarification."
        if not scope_out_scope:
            scope_out_scope = "Pending clarification."
        current_state = self._sanitize_string_list(
            payload.get("current_state"),
            default=["Pending clarification."],
        )
        processes_raw = payload.get("current_processes")
        current_processes: List[Dict[str, Any]] = []
        if isinstance(processes_raw, list):
            for entry in cast(List[Any], processes_raw):
                if not isinstance(entry, dict):
                    continue
                entry_dict = cast(Dict[str, Any], entry)
                name = self._to_clean_string(entry_dict.get("name"))
                happy_steps = self._sanitize_process_steps(
                    entry_dict.get("happy_path")
                )
                unhappy_steps = self._sanitize_process_steps(
                    entry_dict.get("unhappy_path")
                )
                if not name and not happy_steps and not unhappy_steps:
                    continue
                if not name:
                    name = "Process detail pending clarification."
                current_processes.append(
                    {
                        "name": name,
                        "happy_path": happy_steps,
                        "unhappy_path": unhappy_steps,
                    }
                )
        future_state = self._sanitize_string_list(
            payload.get("future_state"),
            default=["Pending clarification."],
        )
        future_processes_raw = payload.get("future_processes")
        future_processes: List[Dict[str, Any]] = []
        if isinstance(future_processes_raw, list):
            for entry in cast(List[Any], future_processes_raw):
                if not isinstance(entry, dict):
                    continue
                entry_dict = cast(Dict[str, Any], entry)
                name = self._to_clean_string(entry_dict.get("name"))
                happy_steps = self._sanitize_process_steps(
                    entry_dict.get("happy_path")
                )
                unhappy_steps = self._sanitize_process_steps(
                    entry_dict.get("unhappy_path")
                )
                if not name and not happy_steps and not unhappy_steps:
                    continue
                if not name:
                    name = "Future process detail pending clarification."
                future_processes.append(
                    {
                        "name": name,
                        "happy_path": happy_steps,
                        "unhappy_path": unhappy_steps,
                    }
                )
        personas_raw = payload.get("personas")
        personas: List[Dict[str, str]] = []
        if isinstance(personas_raw, list):
            for entry in cast(List[Any], personas_raw):
                if not isinstance(entry, dict):
                    continue
                entry_dict = cast(Dict[str, Any], entry)
                name = (
                    self._to_clean_string(entry_dict.get("name"))
                    or "Stakeholder"
                )
                description = (
                    self._to_clean_string(entry_dict.get("description"))
                    or "Pending clarification."
                )
                personas.append({"name": name, "description": description})
        if not personas:
            personas = [
                {
                    "name": "Stakeholder",
                    "description": "Pending clarification.",
                }
            ]
        functional_overview = (
            self._to_clean_string(payload.get("functional_overview"))
            or "Pending clarification."
        )
        non_functional_requirements = self._sanitize_string_list(
            payload.get("non_functional_requirements"),
            default=["Pending clarification."],
        )
        assumptions = self._sanitize_string_list(
            payload.get("assumptions"),
            default=["Pending clarification."],
        )
        risks = self._sanitize_string_list(
            payload.get("risks"),
            default=["Pending clarification."],
        )
        open_issues = self._sanitize_string_list(
            payload.get("open_issues"),
            default=["Pending clarification."],
        )
        reqs_raw = payload.get("functional_requirements")
        requirements: List[Dict[str, str]] = []
        if isinstance(reqs_raw, list):
            for entry in cast(List[Any], reqs_raw):
                if isinstance(entry, dict):
                    entry_dict = cast(Dict[str, Any], entry)
                    description = self._to_clean_string(
                        entry_dict.get("description")
                    )
                    business_rules = self._to_clean_string(
                        entry_dict.get("business_rules")
                    )
                else:
                    description = self._to_clean_string(entry)
                    business_rules = ""
                if not description:
                    continue
                if not business_rules:
                    business_rules = (
                        "Define validation steps and data dependencies."
                    )
                requirements.append(
                    {
                        "description": description,
                        "business_rules": business_rules,
                    }
                )
        if not requirements:
            requirements = [
                {
                    "description": (
                        "Functional requirement pending clarification."
                    ),
                    "business_rules": (
                        "Detail validation expectations and data dependencies "
                        "once confirmed."
                    ),
                }
            ]
        return {
            "title": title,
            "project_overview": project_overview,
            "project_objective": project_objective,
            "scope_overview": scope_overview,
            "scope_in_scope": scope_in_scope,
            "scope_out_of_scope": scope_out_scope,
            "current_state": current_state,
            "current_processes": current_processes,
            "future_state": future_state,
            "future_processes": future_processes,
            "personas": personas,
            "functional_overview": functional_overview,
            "non_functional_requirements": non_functional_requirements,
            "assumptions": assumptions,
            "risks": risks,
            "open_issues": open_issues,
            "functional_requirements": requirements,
        }

    @staticmethod
    def _clean_table_cell(value: str) -> str:
        sanitized = value.replace("\r\n", "\n").replace("\r", "\n")
        sanitized = sanitized.replace("|", "\\|")
        sanitized = sanitized.replace("\n", " <br> ")
        return sanitized.strip()

    @staticmethod
    def _coerce_diagram_paths(value: Any) -> List[str]:
        if isinstance(value, str):
            path = value.strip()
            return [path] if path else []
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            paths: List[str] = []
            for entry in value:
                if isinstance(entry, str):
                    candidate = entry.strip()
                    if candidate:
                        paths.append(candidate)
            return paths
        return []

    def _generate_process_diagrams(self, summary_data: Dict[str, Any]) -> None:
        if self._diagram_agent is None:
            summary_data.pop("current_process_diagram", None)
            summary_data.pop("future_process_diagram", None)
            logger.info(
                "Skipping diagram generation: ProcessDiagramAgent unavailable"
            )
            return
        current_models = self._coerce_as_is_processes(
            summary_data.get("current_processes")
        )
        future_models = self._coerce_future_processes(
            summary_data.get("future_processes")
        )
        self._update_diagram_entry(
            summary_data=summary_data,
            storage_key="current_process_diagram",
            processes=current_models,
            diagram_type="as_is",
            diagram_title="AS-IS Process Flows",
        )
        self._update_diagram_entry(
            summary_data=summary_data,
            storage_key="future_process_diagram",
            processes=future_models,
            diagram_type="to_be",
            diagram_title="Future (TO-BE) Process Flows",
        )

    def _update_diagram_entry(
        self,
        *,
        summary_data: Dict[str, Any],
        storage_key: str,
        processes: Sequence[ProcessModel],
        diagram_type: str,
        diagram_title: str,
    ) -> None:
        if not processes:
            summary_data.pop(storage_key, None)
            logger.info(
                "No processes provided for %s diagram; skipping.",
                diagram_type,
            )
            return
        try:
            artifacts = self._diagram_agent.render_processes(
                processes=processes,
                group_prefix=diagram_type,
                context_label=diagram_title,
            )
        except DiagramExportError:
            logger.exception(
                "Unable to render %s process diagram.",
                diagram_type,
            )
            summary_data.pop(storage_key, None)
            return
        if not artifacts:
            summary_data.pop(storage_key, None)
            return
        summary_data[storage_key] = [
            artifact.relative_path for artifact in artifacts
        ]
        logger.debug(
            "Rendered %s process diagrams: %s",
            diagram_type,
            ", ".join(artifact.relative_path for artifact in artifacts),
        )

    def _render_structured_summary(self, data: Dict[str, Any]) -> str:
        title = data["title"]
        project_overview = data["project_overview"]
        objective = data["project_objective"]
        scope_overview = data.get("scope_overview", "")
        scope_in_scope = data["scope_in_scope"]
        scope_out_scope = data["scope_out_of_scope"]
        current_state = cast(List[str], data["current_state"])
        process_models = self._coerce_as_is_processes(
            data.get("current_processes")
        )
        future_state = cast(List[str], data["future_state"])
        future_process_models = self._coerce_future_processes(
            data.get("future_processes")
        )
        personas = cast(List[Dict[str, str]], data["personas"])
        functional_overview = data["functional_overview"]
        non_functional = cast(List[str], data["non_functional_requirements"])
        assumptions = cast(List[str], data["assumptions"])
        risks = cast(List[str], data["risks"])
        open_issues = cast(List[str], data["open_issues"])
        requirements = cast(
            List[Dict[str, str]],
            data["functional_requirements"],
        )

        if title.lower().startswith("functional specification"):
            header = f"## {title.strip()}"
        else:
            header = f"## Functional Specification: {title.strip()}"

        lines: List[str] = [header, ""]
        lines.append("**1. Project Overview & Objectives**")
        lines.append(project_overview)
        lines.append("")
        lines.append(f"*   **Project Objective:** {objective}")
        lines.append("")
        lines.append("**2. Scope Boundaries:**")
        if scope_overview:
            lines.append(scope_overview)
        lines.append("")
        lines.append(f"*   **In-Scope:** {scope_in_scope}")
        lines.append(f"*   **Out-of-Scope:** {scope_out_scope}")
        lines.append("")
        lines.append("**3. Current State (As-Is)**")
        lines.append("")
        for item in current_state:
            lines.append(f"*   {item}")
        if process_models:
            lines.append("")
            lines.append("**As-Is Process Flows**")
            lines.append("")
            for process in process_models:
                lines.append(f"*   **{process.name}:**")
                if process.happy_path:
                    lines.append("    * Happy path:")
                    for step_index, step in enumerate(
                        process.happy_path, start=1
                    ):
                        lines.append(
                            f"        * {step_index}. {step}"
                        )
                if process.unhappy_path:
                    lines.append(
                        "    * Unhappy path / exceptions:"
                    )
                    for step_index, step in enumerate(
                        process.unhappy_path, start=1
                    ):
                        lines.append(
                            f"        * {step_index}. {step}"
                        )
        diagram_paths = self._coerce_diagram_paths(
            data.get("current_process_diagram")
        )
        for diagram_path in diagram_paths:
            lines.append("")
            lines.append(f"![AS-IS Process Diagram]({diagram_path})")
        lines.append("")
        lines.append("**4. Future State (To-Be)**")
        lines.append("")
        for item in future_state:
            lines.append(f"*   {item}")
        if future_process_models:
            lines.append("")
            lines.append("**Future Process Flows**")
            lines.append("")
            for process in future_process_models:
                lines.append(f"*   **{process.name}:**")
                if process.happy_path:
                    lines.append("    * Happy path:")
                    for step_index, step in enumerate(
                        process.happy_path, start=1
                    ):
                        lines.append(
                            f"        * {step_index}. {step}"
                        )
                if process.unhappy_path:
                    lines.append(
                        "    * Unhappy path / exceptions:"
                    )
                    for step_index, step in enumerate(
                        process.unhappy_path, start=1
                    ):
                        lines.append(
                            f"        * {step_index}. {step}"
                        )
        future_diagrams = self._coerce_diagram_paths(
            data.get("future_process_diagram")
        )
        for diagram_path in future_diagrams:
            lines.append("")
            lines.append(f"![TO-BE Process Diagram]({diagram_path})")
        lines.append("")
        lines.append("**5. Stakeholders & Personas**")
        lines.append("")
        for persona in personas:
            lines.append(
                f"*   **{persona['name']}:** {persona['description']}"
            )
        lines.append("")
        lines.append("**6. Functional Requirements Overview**")
        lines.append(functional_overview)
        lines.append("")
        lines.append("**7. Non-Functional Requirements**")
        lines.append("")
        for item in non_functional:
            lines.append(f"*   {item}")
        lines.append("")
        lines.append("**8. Assumptions**")
        lines.append("")
        for assumption in assumptions:
            lines.append(f"*   {assumption}")
        lines.append("")
        lines.append("**9. Risks**")
        lines.append("")
        for risk in risks:
            lines.append(f"*   {risk}")
        lines.append("")
        lines.append("**10. Open Issues**")
        lines.append("")
        for issue in open_issues:
            lines.append(f"*   {issue}")
        lines.append("")
        lines.append("**11. Functional Requirements**")
        lines.append("")
        lines.append("### Functional Requirements")
        lines.append("")
        lines.append(
            "| Spec ID | Specification Description | "
            "Business Rules/Data Dependency |"
        )
        lines.append("|---|---|---|")
        for index, requirement in enumerate(requirements, start=1):
            desc = self._clean_table_cell(requirement["description"])
            rules = self._clean_table_cell(requirement["business_rules"])
            lines.append(f"| FR-{index} | {desc} | {rules} |")
        return "\n".join(lines)

    def clear_review_corrections(self) -> None:
        """Reset accumulated reviewer guidance."""

        self._summarization_corrections.clear()

    def record_manual_follow_up(
        self,
        question: str,
        answer: str,
        *,
        subject_name: str = "",
    ) -> None:
        """Append a manually asked follow-up question to the transcript."""

        normalized_subject = self._normalize_subject_name(subject_name)
        self._transcript.append(question, answer, subject=normalized_subject)

    def _add_summarization_correction(self, instruction: str) -> None:
        note = instruction.strip()
        if not note:
            return
        if note not in self._summarization_corrections:
            self._summarization_corrections.append(note)

    def _normalize_subject_name(self, subject_name: str) -> str:
        if not subject_name:
            return ""
        lookup = subject_name.strip().lower()
        for subject in SUBJECT_PLAN:
            if subject.name.lower() == lookup:
                return subject.name
        return subject_name

    def record_question(
        self,
        question: str,
        *,
        answer: Optional[str] = None,
    ) -> None:
        """Persist the generated question into the transcript."""
        subject_name = ""
        if self._pending_subject_index is not None:
            subject_name = SUBJECT_PLAN[self._pending_subject_index].name
        self._transcript.turns.append(
            InterviewTurn(
                question=question,
                answer="" if answer is None else answer,
                subject=subject_name,
            )
        )
        self._pending_subject_index = None

    async def _generate_next_question(self) -> Optional[str]:
        while True:
            if self._current_subject_idx >= len(SUBJECT_PLAN):
                return None
            if self._subjects_completed[self._current_subject_idx]:
                self._advance_subject()
                continue
            if (
                self._subject_question_counts[self._current_subject_idx]
                >= self._subject_max_questions
            ):
                self._mark_current_subject_complete()
                continue
            subject = SUBJECT_PLAN[self._current_subject_idx]
            initial = (
                self._subject_question_counts[self._current_subject_idx] == 0
            )
            decision = await self._request_question_decision(
                subject_index=self._current_subject_idx,
                subject=subject,
                initial=initial,
            )
            if decision.subject_complete and not decision.question.strip():
                self._mark_current_subject_complete()
                continue
            question_text = decision.question.strip()
            if not question_text:
                if decision.subject_complete:
                    self._mark_current_subject_complete()
                    continue
                logger.debug(
                    "Empty question response for subject '%s'; "
                    "marking complete.",
                    subject.name,
                )
                self._mark_current_subject_complete()
                continue
            self._subject_question_counts[self._current_subject_idx] += 1
            self._pending_subject_index = self._current_subject_idx
            self._active_subject_index = self._current_subject_idx
            return question_text

    async def _request_question_decision(
        self,
        *,
        subject_index: int,
        subject: InterviewSubject,
        initial: bool,
    ) -> QuestionDecision:
        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=self._system_guidance)
        ]
        messages.extend(self._transcript.as_messages())
        control_prompt = self._compose_instruction(
            subject_index=subject_index,
            subject=subject,
            initial=initial,
        )
        messages.append(ChatMessage(role="user", content=control_prompt))
        response = await self._chat_client.complete(messages)
        decision = self._parse_question_decision(response.content)
        if decision.notes:
            logger.debug(
                "Subject decision notes for '%s': %s",
                subject.name,
                decision.notes,
            )
        return decision

    def _compose_instruction(
        self,
        *,
        subject_index: int,
        subject: InterviewSubject,
        initial: bool,
    ) -> str:
        status_lines: List[str] = []
        for idx, plan_subject in enumerate(SUBJECT_PLAN):
            if self._subjects_completed[idx]:
                status = "complete"
            elif idx == subject_index:
                status = "current"
            else:
                status = "pending"
            status_lines.append(
                (
                    f"{idx + 1}. {plan_subject.name} ({status}) - "
                    f"{plan_subject.focus}"
                )
            )
        asked = self._subject_question_counts[subject_index]
        remaining = self._subject_max_questions - asked
        style_guidance = (
            (
                self._prompt_pack.kickoff
                if initial
                else self._prompt_pack.follow_up
            )
        )
        lines: List[str] = [style_guidance, "", "Subject plan:"]
        lines.extend(status_lines)
        lines.append("")
        lines.append(
            f"Current subject: {subject.name}. Focus: {subject.focus}"
        )
        lines.append(
            (
                "Questions asked for this subject: {asked}. "
                "Maximum allowed: {cap}."
            )
            .format(asked=asked, cap=self._subject_max_questions)
        )
        lines.append(
            "You may ask up to {remaining} more question(s) if they add value."
            .format(remaining=max(remaining, 0))
        )
        lines.append(
            "Decide whether another question is required. Ask only if it will "
            "reveal new or clarifying information."
        )
        lines.append(
            "Respond ONLY with valid JSON using double quotes and no extra "
            "commentary."
        )
        lines.append(
            '{"question": "your next question", "subject_complete": false, '
            '"notes": "optional short rationale"}'
        )
        lines.append(
            'If no further question is needed, set "question" to an empty '
            'string and "subject_complete" to true.'
        )
        lines.append(
            "If every subject is complete, also set subject_complete to true "
            "with an empty question."
        )
        lines.append(
            "Keep the question conversational, professional, and grounded in "
            "prior answers."
        )
        return "\n".join(lines)

    def _parse_question_decision(self, raw: str) -> QuestionDecision:
        text = raw.strip()
        if not text:
            return QuestionDecision(question="", subject_complete=True)
        json_candidate = text
        if not json_candidate.lstrip().startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_candidate = text[start:end + 1]
        try:
            data = json.loads(json_candidate)
        except json.JSONDecodeError:
            logger.debug(
                "Falling back to free-form question output: %s",
                text,
            )
            return QuestionDecision(question=text, subject_complete=False)
        question = str(data.get("question", "")).strip()
        subject_complete = bool(data.get("subject_complete", False))
        notes_obj = data.get("notes")
        notes = str(notes_obj) if notes_obj is not None else None
        return QuestionDecision(
            question=question,
            subject_complete=subject_complete,
            notes=notes,
        )

    def _handle_post_answer(self) -> None:
        if self._active_subject_index is None:
            return
        if (
            self._subject_question_counts[self._active_subject_index]
            >= self._subject_max_questions
        ):
            self._mark_subject_complete(self._active_subject_index)
        self._active_subject_index = None

    def _mark_current_subject_complete(self) -> None:
        self._mark_subject_complete(self._current_subject_idx)

    def _mark_subject_complete(self, index: int) -> None:
        if index < 0 or index >= len(SUBJECT_PLAN):
            return
        if not self._subjects_completed[index]:
            self._subjects_completed[index] = True
        if index == self._current_subject_idx:
            self._advance_subject()

    def _advance_subject(self) -> None:
        next_idx = self._current_subject_idx + 1
        while (
            next_idx < len(SUBJECT_PLAN)
            and self._subjects_completed[next_idx]
        ):
            next_idx += 1
        self._current_subject_idx = next_idx
        self._active_subject_index = None

    def export_spec(self, spec_text: str) -> SpecificationArtifacts:
        """Persist the functional specification (Markdown + PDF)."""

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"functional_spec_{self._scope.value}_{timestamp}.md"
        markdown_path = self._settings.output_dir / filename
        markdown_path.write_text(spec_text, encoding="utf-8")

        pdf_path: Path | None = None
        try:
            pdf_path = self._pdf_exporter.export(
                spec_text,
                markdown_path.with_suffix(".pdf"),
            )
        except PDFExportError:
            logger.exception("Unable to render specification PDF.")

        return SpecificationArtifacts(
            markdown_path=markdown_path,
            pdf_path=pdf_path,
        )

    def persist_transcript(
        self,
        *,
        spec_text: str,
        spec_path: Optional[Path] = None,
    ) -> Optional[str]:
        """Persist the current transcript using the configured repository."""

        return self._transcript_repo.save_transcript(
            transcript=self._transcript,
            scope=self._scope,
            spec_text=spec_text,
            spec_path=spec_path,
        )


async def run_interview(settings: AppSettings, scope: InterviewScope) -> str:
    """Conduct the full interview via the terminal and return the spec text."""

    agent = BusinessAnalystInterviewAgent(settings=settings, scope=scope)
    question = await agent.kickoff()
    agent.record_question(question)
    print()  # noqa: T201 - CLI UX newline
    print(f"BA Agent: {question}")  # noqa: T201 - CLI output
    while True:
        answer = input("You: ")  # noqa: PLW1514 - intentional CLI input
        if answer.strip().lower() in TERMINATION_TOKENS:
            break
        follow_up = await agent.next_question(answer)
        if follow_up is None:
            break
        agent.record_question(follow_up)
        print()
        print(f"BA Agent: {follow_up}")  # noqa: T201

    review_warnings: List[str] = []

    async def _produce_reviewed_specification() -> str:
        nonlocal review_warnings
        review_warnings = []
        attempt_count = 0
        seen_signatures: set[str] = set()
        spec_text_local = ""
        while True:
            spec_text_local = await agent.summarize()
            print()
            print("Functional specification draft:\n")
            print(spec_text_local)
            print()
            review = await agent.review_spec(spec_text_local)
            print(f"Reviewer Agent: {review.feedback_for_interviewer}")
            if not review.requires_follow_up:
                agent.clear_review_corrections()
                spec_text_local = await agent.finalize_current_summary()
                print()
                print("Functional specification draft (AS-IS confirmed):\n")
                print(spec_text_local)
                print()
                return spec_text_local

            if review.missing_subjects:
                missing = ", ".join(review.missing_subjects)
                print(f"Missing subjects flagged: {missing}")
            if (not review.table_valid) and review.table_feedback:
                print(f"Table guidance: {review.table_feedback}")

            review_signature = review.fingerprint()
            if review_signature in seen_signatures:
                print(
                    "Reviewer Agent: Same feedback repeated. "
                    "Stopping automatic retries to avoid a loop."
                )
                review_warnings = review.outstanding_items()
                break
            seen_signatures.add(review_signature)

            if attempt_count >= settings.review_max_passes:
                print(
                    "Reviewer Agent: Maximum review passes reached "
                    f"({settings.review_max_passes})."
                )
                review_warnings = review.outstanding_items()
                break

            attempt_count += 1
            agent.apply_review_feedback(review)
            follow_ups = review.follow_up_questions or []
            if not follow_ups:
                follow_ups = [
                    FollowUpQuestion(
                        question=(
                            "Please share the additional details requested "
                            "by the reviewer so the specification can be "
                            "completed."
                        )
                    )
                ]
            for follow_up in follow_ups:
                print()
                print(f"BA Agent: {follow_up.question}")
                if follow_up.reason:
                    print(f"  (Reviewer note: {follow_up.reason})")
                answer = input("You: ")  # noqa: PLW1514
                agent.record_manual_follow_up(
                    follow_up.question,
                    answer,
                    subject_name=follow_up.subject or "",
                )
            print()
            print(
                "BA Agent: Thanks. I'll incorporate that information "
                "before regenerating the specification."
            )

        if review_warnings:
            print()
            print(
                "Reviewer Agent: Unable to auto-resolve the remaining "
                "feedback. Please handle these items manually:"
            )
            for note in review_warnings:
                print(f"  - {note}")
            print()
        spec_text_local = await agent.finalize_current_summary()
        return spec_text_local

    spec_text = await _produce_reviewed_specification()

    print(f"BA Agent: {CLOSING_PROMPT}")
    closing_response_raw = input("You: ")  # noqa: PLW1514
    agent.record_question(CLOSING_PROMPT, answer=closing_response_raw)
    closing_response = closing_response_raw.strip().lower().rstrip(".! ")
    wants_update = closing_response not in NEGATIVE_FEEDBACK_RESPONSES
    if wants_update:
        print()
        print(
            "BA Agent: Thanks! I'll incorporate that feedback into the "
            "specification."
        )
        spec_text = await _produce_reviewed_specification()
    else:
        print()
        print("BA Agent: Understood. We'll keep the specification as-is.")
        print()

    artifacts = agent.export_spec(spec_text)
    record_id = agent.persist_transcript(
        spec_text=spec_text,
        spec_path=artifacts.markdown_path,
    )
    print("Interview complete. Functional specification saved to:")
    print(f" - {artifacts.markdown_path}")
    if artifacts.pdf_path is not None:
        print(f" - {artifacts.pdf_path}")
    if record_id:
        print(f"Transcript archived with id: {record_id}")
    return spec_text
