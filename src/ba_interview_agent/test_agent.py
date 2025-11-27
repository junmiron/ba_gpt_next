"""LLM-driven stakeholder simulator for exercising the interview workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, cast

from .config import AppSettings, InterviewScope
from .interview_agent import (
    BusinessAnalystInterviewAgent,
    CLOSING_PROMPT,
    NEGATIVE_FEEDBACK_RESPONSES,
)
from .maf_client import ChatMessage, MAFChatClient
from .spec_review_agent import FollowUpQuestion

ConversationTurn = tuple[str, str]

_DEFAULT_PERSONA_DICT: Dict[str, object] = {
    "project_name": "Unified Collaboration Platform",
    "company": "Contoso Labs",
    "stakeholder_role": "Director of Employee Experience",
    "context": (
        "Deploying a cross-department platform to streamline onboarding "
        "and service requests across HR, IT, and Facilities."
    ),
    "goals": [
        "Launch an intuitive portal that reduces employee support tickets.",
        "Consolidate duplicate workflows across business units.",
        "Improve reporting on service-level expectations.",
    ],
    "risks": [
        "Change fatigue from several recent system rollouts.",
        "Integration delays with legacy HR and finance systems.",
        "Compliance gaps for regional data retention rules.",
    ],
    "preferences": [
        "Values plain language and concrete next steps.",
        "Prefers weekly check-ins with clear status indicators.",
        "Wants decisions backed by pilot metrics.",
    ],
    "tone": "Be pragmatic, candid, and collaborative.",
}


def _coerce_list(value: object, fallback: List[str]) -> List[str]:
    items: List[str] = []
    if isinstance(value, list):
        typed_list = cast(List[Any], value)
        for element in typed_list:
            element_str = str(element).strip()
            if element_str:
                items.append(element_str)
    elif isinstance(value, str):
        items = [
            segment.strip()
            for segment in value.split(";")
            if segment.strip()
        ]
    else:
        items = []
    return items or fallback


@dataclass(slots=True)
class SimulatedProjectPersona:
    """Stores persona information for the simulated stakeholder."""

    project_name: str
    company: str
    stakeholder_role: str
    context: str
    goals: List[str]
    risks: List[str]
    preferences: List[str]
    tone: str

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SimulatedProjectPersona":
        raw = {**_DEFAULT_PERSONA_DICT, **data}
        project_name = str(raw.get("project_name", "Project")).strip()
        company = str(raw.get("company", "Company")).strip()
        stakeholder_role = str(
            raw.get("stakeholder_role", "Stakeholder")
        ).strip()
        context = str(raw.get("context", "Context")).strip()
        default_goals = _coerce_list(_DEFAULT_PERSONA_DICT["goals"], [])
        default_risks = _coerce_list(_DEFAULT_PERSONA_DICT["risks"], [])
        default_preferences = _coerce_list(
            _DEFAULT_PERSONA_DICT["preferences"], []
        )
        goals = _coerce_list(raw.get("goals"), default_goals)
        risks = _coerce_list(raw.get("risks"), default_risks)
        preferences = _coerce_list(
            raw.get("preferences"),
            default_preferences,
        )
        tone = str(raw.get("tone", _DEFAULT_PERSONA_DICT["tone"]))
        tone = tone.strip() or str(_DEFAULT_PERSONA_DICT["tone"])
        if not project_name:
            project_name = "Strategic Initiative"
        if not company:
            company = "Contoso"
        if not stakeholder_role:
            stakeholder_role = "Business Sponsor"
        if not context:
            context = "Driving a cross-functional change programme."
        if not goals:
            goals = ["Clarify requirements and adoption expectations."]
        if not risks:
            risks = ["Unclear ownership across departments."]
        if not preferences:
            preferences = ["Appreciates concise updates with clear owners."]
        return cls(
            project_name=project_name,
            company=company,
            stakeholder_role=stakeholder_role,
            context=context,
            goals=goals,
            risks=risks,
            preferences=preferences,
            tone=tone,
        )

    def summary_lines(self) -> List[str]:
        return [
            f"Project: {self.project_name}",
            f"Company: {self.company}",
            f"Stakeholder role: {self.stakeholder_role}",
            f"Context: {self.context}",
            "Goals: " + "; ".join(self.goals),
            "Risks: " + "; ".join(self.risks),
            "Preferences: " + "; ".join(self.preferences),
            f"Tone: {self.tone}",
        ]

    def summary(self) -> str:
        return "\n".join(self.summary_lines())


def _extract_json_object(raw: str) -> Optional[Dict[str, object]]:
    text = raw.strip()
    if not text:
        return None
    candidate = text
    if not candidate.lstrip().startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        typed_data = cast(Dict[Any, Any], data)
        normalized: Dict[str, object] = {}
        for key, value in typed_data.items():
            normalized[str(key)] = cast(object, value)
        return normalized
    return None


async def _generate_persona_dict(
    chat_client: MAFChatClient,
    scope: InterviewScope,
    seed: Optional[int],
) -> Dict[str, object]:
    scope_label = scope.value.replace("_", " ")
    seed_line = ""
    if seed is not None:
        seed_line = f"Use creative seed {seed} to add variation.\n"
    prompt = (
        "Create a realistic stakeholder persona for a business analyst "
        "interview.\n"
        f"The interview focuses on a {scope_label} initiative.\n"
        f"{seed_line}"
        "Return a compact JSON object with fields: project_name, company, "
        "stakeholder_role, context, goals, risks, preferences, and tone.\n"
        "Make goals, risks, and preferences arrays of 3 short phrases each."
    )
    messages = [
        ChatMessage(
            role="system",
            content="You design detailed yet concise stakeholder personas.",
        ),
        ChatMessage(role="user", content=prompt),
    ]
    response = await chat_client.complete(messages)
    data = _extract_json_object(response.content)
    if data is None:
        return dict(_DEFAULT_PERSONA_DICT)
    return data


class SimulatedStakeholderResponder:
    """LLM-backed stakeholder that answers interview questions."""

    def __init__(
        self,
        *,
        persona: SimulatedProjectPersona,
        chat_client: MAFChatClient,
    ) -> None:
        self._persona = persona
        self._chat_client = chat_client

    @property
    def persona(self) -> SimulatedProjectPersona:
        return self._persona

    @classmethod
    async def create(
        cls,
        *,
        settings: AppSettings,
        scope: InterviewScope,
        seed: Optional[int],
        persona_override: Optional[Dict[str, object]] = None,
    ) -> "SimulatedStakeholderResponder":
        chat_client = MAFChatClient(settings.model)
        if persona_override is not None:
            persona_data = persona_override
        else:
            persona_data = await _generate_persona_dict(
                chat_client,
                scope,
                seed,
            )
        persona = SimulatedProjectPersona.from_dict(persona_data)
        return cls(persona=persona, chat_client=chat_client)

    def _build_system_prompt(self) -> str:
        goals = "; ".join(self._persona.goals)
        risks = "; ".join(self._persona.risks)
        preferences = "; ".join(self._persona.preferences)
        return (
            f"You are {self._persona.stakeholder_role} at "
            f"{self._persona.company}.\n"
            f"You are collaborating on the '{self._persona.project_name}' "
            "initiative.\n"
            f"Context: {self._persona.context}\n"
            f"Goals: {goals}\n"
            f"Risks: {risks}\n"
            f"Preferences: {preferences}\n"
            f"Tone guidance: {self._persona.tone}\n"
            "Respond like a human stakeholder, using 2-4 sentences with "
            "practical, domain-informed detail. Never mention that you "
            "are an AI model."
        )

    def _build_history_messages(
        self,
        history: Sequence[ConversationTurn],
    ) -> List[ChatMessage]:
        recent = list(history)[-4:]
        messages: List[ChatMessage] = []
        for question, answer in recent:
            messages.append(ChatMessage(role="user", content=question))
            messages.append(ChatMessage(role="assistant", content=answer))
        return messages

    def _format_question_prompt(self, question: str) -> str:
        return (
            "Interviewer question: "
            f"{question.strip()}\n"
            "Reply as the stakeholder in 2-4 sentences. Reference goals, "
            "risks, and preferences when relevant, and keep the tone "
            "collaborative."
        )

    async def answer(
        self,
        question: str,
        history: Sequence[ConversationTurn],
    ) -> str:
        messages = [
            ChatMessage(role="system", content=self._build_system_prompt())
        ]
        messages.extend(self._build_history_messages(history))
        messages.append(
            ChatMessage(
                role="user",
                content=self._format_question_prompt(question),
            )
        )
        response = await self._chat_client.complete(messages)
        return response.content.strip()

    async def closing_feedback(
        self,
        *,
        spec_text: str,
        conversation: Sequence[ConversationTurn],
    ) -> str:
        spec_excerpt = spec_text.strip()
        if len(spec_excerpt) > 1500:
            spec_excerpt = spec_excerpt[:1500] + "\n..."
        history_lines: List[str] = []
        for question, answer in conversation[-4:]:
            history_lines.append(f"BA: {question}")
            history_lines.append(f"Stakeholder: {answer}")
        history_text = "\n".join(history_lines)
        prompt = (
            "You have just reviewed the interview summary shown below. "
            "Provide a short (max two sentences) reaction that confirms "
            "what feels complete and any final requests.\n"
            "Conversation recap:\n"
            f"{history_text}\n\n"
            "Draft specification:\n-----\n"
            f"{spec_excerpt}\n-----"
        )
        messages = [
            ChatMessage(role="system", content=self._build_system_prompt()),
            ChatMessage(role="user", content=prompt),
        ]
        response = await self._chat_client.complete(messages)
        return response.content.strip()


async def simulate_interview(
    *,
    settings: AppSettings,
    scope: InterviewScope,
    responder: SimulatedStakeholderResponder,
    verbose: bool = True,
) -> Dict[str, object]:
    """Run an interview using an LLM-backed stakeholder persona."""

    agent = BusinessAnalystInterviewAgent(settings=settings, scope=scope)
    transcript: List[ConversationTurn] = []

    if verbose:
        print("Simulated stakeholder persona:\n")
        for line in responder.persona.summary_lines():
            print(f"  {line}")
        print()

    question = await agent.kickoff()
    agent.record_question(question)
    if verbose:
        print(f"BA Agent: {question}")

    while True:
        answer = await responder.answer(question, transcript)
        transcript.append((question, answer))
        if verbose:
            print(f"Test Agent: {answer}\n")
        follow_up = await agent.next_question(answer)
        if follow_up is None:
            break
        agent.record_question(follow_up)
        question = follow_up
        if verbose:
            print(f"BA Agent: {question}")

    review_warnings: List[str] = []

    async def _produce_reviewed_specification() -> str:
        nonlocal review_warnings
        review_warnings = []
        attempt_count = 0
        seen_signatures: set[str] = set()
        spec_text_local = ""
        while True:
            spec_text_local = await agent.summarize()
            if verbose:
                print("\nFunctional specification draft:\n")
                print(spec_text_local)
                print()
            review = await agent.review_spec(spec_text_local)
            if verbose:
                print(
                    f"Reviewer Agent: {review.feedback_for_interviewer}"
                )
            if not review.requires_follow_up:
                agent.clear_review_corrections()
                final_spec = await agent.finalize_current_summary()
                if verbose:
                    print()
                    print(
                        "Functional specification draft "
                        "(AS-IS confirmed):\n"
                    )
                    print(final_spec)
                    print()
                return final_spec

            if review.missing_subjects and verbose:
                missing = ", ".join(review.missing_subjects)
                print(f"Missing subjects flagged: {missing}")
            if (not review.table_valid) and review.table_feedback and verbose:
                print(f"Table guidance: {review.table_feedback}")

            review_signature = review.fingerprint()
            if review_signature in seen_signatures:
                if verbose:
                    print(
                        "Reviewer Agent: Same feedback repeated. "
                        "Stopping automatic retries to avoid a loop."
                    )
                review_warnings = review.outstanding_items()
                break
            seen_signatures.add(review_signature)

            if attempt_count >= settings.review_max_passes:
                if verbose:
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
                if verbose:
                    print()
                    print(f"BA Agent: {follow_up.question}")
                    if follow_up.reason:
                        print(f"  (Reviewer note: {follow_up.reason})")
                follow_answer = await responder.answer(
                    follow_up.question,
                    transcript,
                )
                agent.record_manual_follow_up(
                    follow_up.question,
                    follow_answer,
                    subject_name=follow_up.subject or "",
                )
                transcript.append((follow_up.question, follow_answer))
                if verbose:
                    print(f"Test Agent: {follow_answer}\n")
            if verbose:
                print(
                    "BA Agent: Additional details captured. Regenerating "
                    "the specification..."
                )

        if review_warnings and verbose:
            print()
            print(
                "Reviewer Agent: Unable to auto-resolve the remaining "
                "feedback. Please handle these items manually:"
            )
            for note in review_warnings:
                print(f"  - {note}")
            print()

        final_spec = await agent.finalize_current_summary()
        return final_spec

    spec_text = await _produce_reviewed_specification()

    closing_answer = await responder.closing_feedback(
        spec_text=spec_text,
        conversation=transcript,
    )
    agent.record_question(CLOSING_PROMPT, answer=closing_answer)
    if closing_answer.strip().lower() not in NEGATIVE_FEEDBACK_RESPONSES:
        spec_text = await _produce_reviewed_specification()
    spec_artifacts = agent.export_spec(spec_text)
    spec_path = spec_artifacts.markdown_path
    record_id = agent.persist_transcript(
        spec_text=spec_text,
        spec_path=spec_path,
    )

    if verbose:
        print(f"BA Agent: {CLOSING_PROMPT}")
        print(f"Test Agent: {closing_answer}\n")
        print("Simulation complete. Functional specification saved to:")
        print(f" - {spec_path}")
        if spec_artifacts.pdf_path is not None:
            print(f" - {spec_artifacts.pdf_path}")
        if record_id:
            print(f"Transcript id: {record_id}")

    return {
        "persona": responder.persona,
        "spec_path": spec_path,
        "pdf_path": spec_artifacts.pdf_path,
        "transcript": transcript,
        "closing_feedback": closing_answer,
        "record_id": record_id,
        "review_warnings": review_warnings,
    }


def _load_persona_file(path: Path) -> Dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError as exc:
        raise SystemExit(f"Failed to read persona file: {path}") from exc
    except json.JSONDecodeError as exc:  # pragma: no cover - CLI guard
        raise SystemExit(f"Persona file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Persona file must contain a JSON object.")
    typed_data = cast(Dict[Any, Any], data)
    normalized: Dict[str, object] = {}
    for key, value in typed_data.items():
        normalized[str(key)] = cast(object, value)
    return normalized


def run_test_agent_cli(
    settings: AppSettings,
    argv: Optional[List[str]] = None,
) -> None:
    """CLI entry point for running automated interview simulations."""

    parser = argparse.ArgumentParser(
        prog="ba-interview-agent simulate",
        description=(
            "Simulate interviews by answering questions with an LLM-driven "
            "stakeholder persona."
        ),
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of simulations to run (default: 1).",
    )
    parser.add_argument(
        "--scope",
        choices=[scope.value for scope in InterviewScope],
        help="Interview scope to simulate (default: environment default).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Seed for persona generation (each run increments the seed).",
    )
    parser.add_argument(
        "--persona-file",
        help="Optional JSON file describing the stakeholder persona.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-question logs during the run.",
    )
    args = parser.parse_args(argv)

    if args.count < 1:
        raise SystemExit("--count must be >= 1")

    scope = InterviewScope.from_string(
        args.scope,
        default=settings.default_scope,
    )

    persona_override: Optional[Dict[str, object]] = None
    if args.persona_file:
        persona_override = _load_persona_file(Path(args.persona_file))

    async def _runner() -> None:
        base_seed = args.seed
        for index in range(1, args.count + 1):
            seed_value: Optional[int]
            if base_seed is None:
                seed_value = None
            else:
                seed_value = base_seed + index - 1
            responder = await SimulatedStakeholderResponder.create(
                settings=settings,
                scope=scope,
                seed=seed_value,
                persona_override=persona_override,
            )
            verbose = not args.quiet
            result = await simulate_interview(
                settings=settings,
                scope=scope,
                responder=responder,
                verbose=verbose,
            )
            if not verbose:
                spec_path = result.get("spec_path")
                pdf_path = result.get("pdf_path")
                warnings = cast(List[str], result.get("review_warnings", []))
                summary = (
                    f"Simulation {index}/{args.count} -> "
                    f"{responder.persona.project_name} => {spec_path}"
                )
                if warnings:
                    summary += " (review warnings)"
                print(summary)
                if pdf_path:
                    print(f"    PDF: {pdf_path}")
                for note in warnings:
                    print(f"    - {note}")

    asyncio.run(_runner())
