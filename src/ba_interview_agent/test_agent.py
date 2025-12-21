"""LLM-driven stakeholder simulator for exercising the interview workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, cast

from .config import AppSettings, InterviewScope
from .as_is_agent import AsIsProcess, AsIsReviewResult
from .interview_agent import (
    BusinessAnalystInterviewAgent,
    NEGATIVE_FEEDBACK_RESPONSES,
)
from .maf_client import ChatMessage, MAFChatClient
from .spec_review_agent import FollowUpQuestion
from .to_be_agent import ToBeProcess, ToBeReviewResult
from .prompts import DEFAULT_LANGUAGE, resolve_language_code

ConversationTurn = tuple[str, str]

_DEFAULT_PERSONA_EN: Dict[str, object] = {
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

_DEFAULT_PERSONA_ES: Dict[str, object] = {
    "project_name": "Plataforma Unificada de Colaboracion",
    "company": "Contoso Labs",
    "stakeholder_role": "Directora de Experiencia del Empleado",
    "context": (
        "Implementar una plataforma transversal que agilice la incorporacion "
        "y las solicitudes de servicio entre RR. HH., TI y Operaciones."
    ),
    "goals": [
        "Lanzar un portal intuitivo que reduzca los tickets de soporte.",
        "Unificar flujos duplicados entre unidades de negocio.",
        "Mejorar los reportes sobre expectativas de nivel de servicio.",
    ],
    "risks": [
        "Cansancio por el cambio tras despliegues recientes.",
        "Retrasos de integracion con sistemas heredados de RR. HH. y finanzas.",
        "Brechas de cumplimiento en reglas regionales de retencion de datos.",
    ],
    "preferences": [
        "Valora el lenguaje claro y los proximos pasos concretos.",
        "Prefiere reuniones semanales con indicadores de estado claros.",
        "Quiere decisiones respaldadas por metricas piloto.",
    ],
    "tone": "Se pragmatica, franca y colaborativa.",
}

_DEFAULT_PERSONA_BY_LANG: Dict[str, Dict[str, object]] = {
    "en": _DEFAULT_PERSONA_EN,
    "es": _DEFAULT_PERSONA_ES,
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
    language: str = "en"

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, object],
        *,
        language: str = DEFAULT_LANGUAGE,
    ) -> "SimulatedProjectPersona":
        lang = resolve_language_code(language)
        defaults = _DEFAULT_PERSONA_BY_LANG.get(lang, _DEFAULT_PERSONA_BY_LANG[DEFAULT_LANGUAGE])
        raw = {**defaults, **data}
        project_name = str(raw.get("project_name", "Project")).strip()
        company = str(raw.get("company", "Company")).strip()
        stakeholder_role = str(
            raw.get("stakeholder_role", "Stakeholder")
        ).strip()
        context = str(raw.get("context", "Context")).strip()
        default_goals = _coerce_list(defaults.get("goals"), [])
        default_risks = _coerce_list(defaults.get("risks"), [])
        default_preferences = _coerce_list(
            defaults.get("preferences"), []
        )
        goals = _coerce_list(raw.get("goals"), default_goals)
        risks = _coerce_list(raw.get("risks"), default_risks)
        preferences = _coerce_list(
            raw.get("preferences"),
            default_preferences,
        )
        tone = str(raw.get("tone", defaults.get("tone", "Maintain a candid tone.")))
        tone = tone.strip() or str(defaults.get("tone", "Maintain a candid tone."))
        if not project_name:
            project_name = str(defaults.get("project_name", "Strategic Initiative"))
        if not company:
            company = str(defaults.get("company", "Contoso"))
        if not stakeholder_role:
            stakeholder_role = str(defaults.get("stakeholder_role", "Business Sponsor"))
        if not context:
            context = str(defaults.get("context", "Driving a cross-functional change programme."))
        if not goals:
            fallback_goal = default_goals[0] if default_goals else "Clarify requirements and adoption expectations."
            goals = [fallback_goal]
        if not risks:
            fallback_risk = default_risks[0] if default_risks else "Unclear ownership across departments."
            risks = [fallback_risk]
        if not preferences:
            fallback_pref = (
                default_preferences[0]
                if default_preferences
                else "Appreciates concise updates with clear owners."
            )
            preferences = [fallback_pref]
        return cls(
            project_name=project_name,
            company=company,
            stakeholder_role=stakeholder_role,
            context=context,
            goals=goals,
            risks=risks,
            preferences=preferences,
            tone=tone,
            language=lang,
        )

    def summary_lines(self) -> List[str]:
        if self.language == "es":
            return [
                f"Proyecto: {self.project_name}",
                f"Empresa: {self.company}",
                f"Rol de la parte interesada: {self.stakeholder_role}",
                f"Contexto: {self.context}",
                "Metas: " + "; ".join(self.goals),
                "Riesgos: " + "; ".join(self.risks),
                "Preferencias: " + "; ".join(self.preferences),
                f"Guia de tono: {self.tone}",
            ]
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
    language: str,
) -> Dict[str, object]:
    language_code = resolve_language_code(language)
    scope_label = scope.value.replace("_", " ")
    seed_line = ""
    if seed is not None:
        seed_line = f"Use creative seed {seed} to add variation.\n"
    if language_code == "es":
        language_line = (
            "Escribe todos los campos en espanol neutro y mantente conciso. "
            "Asegurate de que metas, riesgos y preferencias sean arreglos con "
            "tres frases cortas en espanol."
        )
        system_prompt = (
            "Disenas personas de stakeholders realistas con enfoque en negocios. "
            "Siempre respondes en espanol.")
    else:
        language_line = (
            "Write all fields in clear international English. Ensure goals, risks, "
            "and preferences are arrays of three short English phrases."
        )
        system_prompt = "You design detailed yet concise stakeholder personas."
    prompt = (
        "Create a realistic stakeholder persona for a business analyst "
        "interview.\n"
        f"The interview focuses on a {scope_label} initiative.\n"
        f"{seed_line}"
        "Return a compact JSON object with fields: project_name, company, "
        "stakeholder_role, context, goals, risks, preferences, and tone.\n"
        f"{language_line}"
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=prompt),
    ]
    response = await chat_client.complete(messages)
    data = _extract_json_object(response.content)
    if data is None:
        return dict(_DEFAULT_PERSONA_BY_LANG.get(language_code, _DEFAULT_PERSONA_EN))
    return data


class SimulatedStakeholderResponder:
    """LLM-backed stakeholder that answers interview questions."""

    def __init__(
        self,
        *,
        persona: SimulatedProjectPersona,
        chat_client: MAFChatClient,
        language: str,
    ) -> None:
        self._persona = persona
        self._chat_client = chat_client
        self._language = language

    @property
    def persona(self) -> SimulatedProjectPersona:
        return self._persona

    @property
    def language(self) -> str:
        return self._language

    @classmethod
    async def create(
        cls,
        *,
        settings: AppSettings,
        scope: InterviewScope,
        seed: Optional[int],
        persona_override: Optional[Dict[str, object]] = None,
        language: Optional[str] = None,
    ) -> "SimulatedStakeholderResponder":
        language_code = resolve_language_code(language or DEFAULT_LANGUAGE)
        chat_client = MAFChatClient(settings.model)
        if persona_override is not None:
            persona_data = persona_override
        else:
            persona_data = await _generate_persona_dict(
                chat_client,
                scope,
                seed,
                language_code,
            )
        persona = SimulatedProjectPersona.from_dict(
            persona_data,
            language=language_code,
        )
        return cls(
            persona=persona,
            chat_client=chat_client,
            language=language_code,
        )

    def _build_system_prompt(self) -> str:
        goals = "; ".join(self._persona.goals)
        risks = "; ".join(self._persona.risks)
        preferences = "; ".join(self._persona.preferences)
        if self._language == "es":
            return (
                f"Eres {self._persona.stakeholder_role} en "
                f"{self._persona.company}.\n"
                f"Colaboras en la iniciativa '{self._persona.project_name}'.\n"
                f"Contexto: {self._persona.context}\n"
                f"Metas: {goals}\n"
                f"Riesgos: {risks}\n"
                f"Preferencias: {preferences}\n"
                f"Guia de tono: {self._persona.tone}\n"
                "Responde como una persona real usando 2-4 oraciones con detalle practico. "
                "No menciones que eres un modelo y mantente siempre en espanol neutro."
            )
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
            "Respond like a human stakeholder, using 2-4 sentences with practical, "
            "domain-informed detail. Never mention that you are an AI model."
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
        query = question.strip()
        if self._language == "es":
            return (
                "Pregunta del analista: "
                f"{query}\n"
                "Responde como la parte interesada en 2-4 oraciones, enlazando metas, riesgos y preferencias cuando apliquen. "
                "Mantente colaborativa y usa espanol neutro."
            )
        return (
            "Interviewer question: "
            f"{query}\n"
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
        if self._language == "es":
            prompt = (
                "Acabas de revisar el resumen de la entrevista mostrado abajo. "
                "Redacta una reaccion breve (maximo dos oraciones) que confirme lo que se siente completo y cualquier solicitud final.\n"
                "Recapitulacion de la conversacion:\n"
                f"{history_text}\n\n"
                "Borrador de la especificacion:\n-----\n"
                f"{spec_excerpt}\n-----\n"
                "Responde en espanol neutro."
            )
        else:
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
    language: Optional[str] = None,
    verbose: bool = True,
    observer: Optional[
        Callable[[str, Dict[str, object]], Awaitable[None] | None]
    ] = None,
) -> Dict[str, object]:
    """Run an interview using an LLM-backed stakeholder persona."""

    language_code = resolve_language_code(
        language or getattr(responder, "language", None) or DEFAULT_LANGUAGE
    )
    agent = BusinessAnalystInterviewAgent(
        settings=settings,
        scope=scope,
        language=language_code,
    )
    transcript: List[ConversationTurn] = []

    async def _emit(event_type: str, **payload: object) -> None:
        if observer is None:
            return
        try:
            payload_map: Dict[str, object] = {
                key: value for key, value in payload.items()
            }
            outcome = observer(event_type, payload_map)
            if asyncio.iscoroutine(outcome):
                await outcome
        except Exception:  # pragma: no cover - observer failures should not abort simulation
            logging.exception("Test agent observer failed for event '%s'", event_type)

    class _SimulatedAsIsReviewer:
        async def confirm_as_is(
            self,
            *,
            proposed_items: Sequence[str],
            proposed_processes: Sequence[AsIsProcess],
            spec_text: str,
            question: str,
        ) -> AsIsReviewResult:
            await _emit(
                "status",
                content="Confirming AS-IS understanding with the stakeholder...",
            )
            question_text = question.strip() or (
                "Could you confirm the current-state summary?"
            )
            await _emit("message", role="assistant", content=question_text)

            items = [str(item).strip() for item in proposed_items if str(item).strip()]
            if not items:
                items = ["Current state details pending stakeholder validation."]

            processes = [
                AsIsProcess(
                    name=process.name,
                    happy_path=list(process.happy_path),
                    unhappy_path=list(process.unhappy_path),
                )
                for process in proposed_processes
            ]

            summary_lines: List[str] = []
            summary_lines.append("Proposed AS-IS summary:")
            for item in items:
                summary_lines.append(f"- {item}")
            if processes:
                summary_lines.append("\nIdentified AS-IS processes:")
            for process in processes:
                summary_lines.append(f"- {process.name}")
                if process.happy_path:
                    summary_lines.append(
                        "  Happy path: " + "; ".join(step.strip() for step in process.happy_path if step.strip())
                    )
                if process.unhappy_path:
                    summary_lines.append(
                        "  Exceptions: " + "; ".join(
                            step.strip() for step in process.unhappy_path if step.strip()
                        )
                    )

            excerpt = spec_text.strip()
            if len(excerpt) > 1200:
                excerpt = excerpt[:1200].rstrip() + "\n..."

            prompt_parts = [question_text]
            if summary_lines:
                prompt_parts.append("\n".join(summary_lines))
            if excerpt:
                prompt_parts.append(
                    "Functional specification excerpt:\n" + excerpt
                )
            prompt_parts.append(
                "Responde en 1-2 oraciones confirmando si el resumen AS-IS refleja la realidad. "
                "Menciona solo los vacios criticos si quedan." if responder.language == "es"
                else "Respond in 1-2 sentences confirming whether the AS-IS summary captures reality. "
                "Call out only the most critical gaps if any remain."
            )
            prompt = "\n\n".join(part for part in prompt_parts if part)

            try:
                comment = await responder.answer(prompt, transcript)
            except Exception:  # pragma: no cover - fall back to default comment
                logging.exception("Simulated AS-IS reviewer failed; using default acknowledgement.")
                comment = "This matches how things work today."

            stakeholder_comment = comment.strip() or "This matches how things work today."
            transcript.append((question_text, stakeholder_comment))
            await _emit("message", role="user", content=stakeholder_comment)

            return AsIsReviewResult(
                items=items,
                processes=processes,
                stakeholder_comment=stakeholder_comment,
            )

    class _SimulatedToBeReviewer:
        async def confirm_to_be(
            self,
            *,
            proposed_items: Sequence[str],
            proposed_processes: Sequence[ToBeProcess],
            spec_text: str,
            question: str,
        ) -> ToBeReviewResult:
            await _emit(
                "status",
                content="Reviewing the target TO-BE vision with the stakeholder...",
            )
            question_text = question.strip() or (
                "Can you confirm the desired future-state vision?"
            )
            await _emit("message", role="assistant", content=question_text)

            items = [str(item).strip() for item in proposed_items if str(item).strip()]
            if not items:
                items = ["Future state details pending stakeholder validation."]

            processes = [
                ToBeProcess(
                    name=process.name,
                    happy_path=list(process.happy_path),
                    unhappy_path=list(process.unhappy_path),
                )
                for process in proposed_processes
            ]

            summary_lines: List[str] = []
            summary_lines.append("Proposed TO-BE summary:")
            for item in items:
                summary_lines.append(f"- {item}")
            if processes:
                summary_lines.append("\nTarget TO-BE processes:")
            for process in processes:
                summary_lines.append(f"- {process.name}")
                if process.happy_path:
                    summary_lines.append(
                        "  Happy path: " + "; ".join(step.strip() for step in process.happy_path if step.strip())
                    )
                if process.unhappy_path:
                    summary_lines.append(
                        "  Exceptions: " + "; ".join(
                            step.strip() for step in process.unhappy_path if step.strip()
                        )
                    )

            excerpt = spec_text.strip()
            if len(excerpt) > 1200:
                excerpt = excerpt[:1200].rstrip() + "\n..."

            prompt_parts = [question_text]
            if summary_lines:
                prompt_parts.append("\n".join(summary_lines))
            if excerpt:
                prompt_parts.append(
                    "Functional specification excerpt:\n" + excerpt
                )
            prompt_parts.append(
                "Responde en 1-2 oraciones confirmando la vision TO-BE esperada. Destaca solo ajustes criticos si son necesarios."
                if responder.language == "es"
                else "Reply in 1-2 sentences confirming the target TO-BE vision. Highlight only critical adjustments if needed."
            )
            prompt = "\n\n".join(part for part in prompt_parts if part)

            try:
                comment = await responder.answer(prompt, transcript)
            except Exception:  # pragma: no cover - fall back to default comment
                logging.exception("Simulated TO-BE reviewer failed; using default acknowledgement.")
                comment = "The future-state vision looks aligned with expectations."

            stakeholder_comment = (
                comment.strip()
                or "The future-state vision looks aligned with expectations."
            )
            transcript.append((question_text, stakeholder_comment))
            await _emit("message", role="user", content=stakeholder_comment)

            return ToBeReviewResult(
                items=items,
                processes=processes,
                stakeholder_comment=stakeholder_comment,
            )

    # Replace interactive reviewers with simulated stakeholder versions.
    agent._as_is_reviewer = _SimulatedAsIsReviewer()  # type: ignore[attr-defined]
    agent._to_be_reviewer = _SimulatedToBeReviewer()  # type: ignore[attr-defined]

    await _emit("persona", persona=responder.persona)

    if verbose:
        print("Simulated stakeholder persona:\n")
        for line in responder.persona.summary_lines():
            print(f"  {line}")
        print()

    question = await agent.kickoff()
    agent.record_question(question)
    await _emit("message", role="assistant", content=question)
    if verbose:
        print(f"BA Agent: {question}")

    while True:
        answer = await responder.answer(question, transcript)
        transcript.append((question, answer))
        await _emit("message", role="user", content=answer)
        if verbose:
            print(f"Test Agent: {answer}\n")
        follow_up = await agent.next_question(answer)
        if follow_up is None:
            break
        agent.record_question(follow_up)
        await _emit("message", role="assistant", content=follow_up)
        question = follow_up
        if verbose:
            print(f"BA Agent: {question}")

    review_warnings: List[str] = []

    await _emit(
        "status",
        content="Generating functional specification draft...",
    )

    async def _produce_reviewed_specification() -> str:
        nonlocal review_warnings
        review_warnings = []
        attempt_count = 0
        seen_signatures: set[str] = set()
        spec_text_local = ""
        while True:
            spec_text_local = await agent.summarize()
            await _emit("spec_draft", content=spec_text_local)
            if verbose:
                print("\nFunctional specification draft:\n")
                print(spec_text_local)
                print()
            review = await agent.review_spec(spec_text_local)
            await _emit(
                "review_feedback",
                content=review.feedback_for_interviewer,
            )
            if verbose:
                print(
                    f"Reviewer Agent: {review.feedback_for_interviewer}"
                )
            if not review.requires_follow_up:
                agent.clear_review_corrections()
                final_spec = await agent.finalize_current_summary()
                await _emit("spec_final", content=final_spec)
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
                await _emit(
                    "review_warning",
                    note=f"Missing subjects flagged: {missing}",
                )
            if (not review.table_valid) and review.table_feedback and verbose:
                print(f"Table guidance: {review.table_feedback}")
                await _emit(
                    "review_warning",
                    note=review.table_feedback,
                )

            review_signature = review.fingerprint()
            if review_signature in seen_signatures:
                if verbose:
                    print(
                        "Reviewer Agent: Same feedback repeated. "
                        "Stopping automatic retries to avoid a loop."
                    )
                review_warnings = review.outstanding_items()
                for note in review_warnings:
                    await _emit("review_warning", note=note)
                break
            seen_signatures.add(review_signature)

            if attempt_count >= settings.review_max_passes:
                if verbose:
                    print(
                        "Reviewer Agent: Maximum review passes reached "
                        f"({settings.review_max_passes})."
                    )
                review_warnings = review.outstanding_items()
                for note in review_warnings:
                    await _emit("review_warning", note=note)
                break

            attempt_count += 1
            await _emit(
                "status",
                content="Reviewer requested additional details...",
            )
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
                await _emit("message", role="assistant", content=follow_up.question)
                if follow_up.reason:
                    await _emit("review_note", note=follow_up.reason)
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
                await _emit("message", role="user", content=follow_answer)
                if verbose:
                    print(f"Test Agent: {follow_answer}\n")
            if verbose:
                print(
                    "BA Agent: Additional details captured. Regenerating "
                    "the specification..."
                )
            await _emit(
                "status",
                content="Additional details captured. Regenerating the specification...",
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
        for note in review_warnings:
            await _emit("review_warning", note=note)

        final_spec = await agent.finalize_current_summary()
        await _emit("spec_final", content=final_spec)
        return final_spec

    spec_text = await _produce_reviewed_specification()

    await _emit("message", role="assistant", content=agent.closing_prompt)
    await _emit(
        "status",
        content="Awaiting stakeholder closing feedback...",
    )

    closing_answer = await responder.closing_feedback(
        spec_text=spec_text,
        conversation=transcript,
    )
    agent.record_question(agent.closing_prompt, answer=closing_answer)
    await _emit("message", role="user", content=closing_answer)
    if closing_answer.strip().lower() not in NEGATIVE_FEEDBACK_RESPONSES:
        await _emit(
            "status",
            content="Stakeholder approved summary. Refreshing specification...",
        )
        spec_text = await _produce_reviewed_specification()
    spec_artifacts = agent.export_spec(spec_text)
    spec_path = spec_artifacts.markdown_path
    record_id = agent.persist_transcript(
        spec_text=spec_text,
        spec_path=spec_path,
    )
    await _emit(
        "artifact",
        kind="spec_markdown",
        path=str(spec_path),
    )
    if spec_artifacts.pdf_path is not None:
        await _emit(
            "artifact",
            kind="spec_pdf",
            path=str(spec_artifacts.pdf_path),
        )
    if record_id:
        await _emit(
            "artifact",
            kind="transcript_record",
            record_id=str(record_id),
        )
    await _emit("status", content="Simulation complete.")

    if verbose:
        print(f"BA Agent: {agent.closing_prompt}")
        print(f"Test Agent: {closing_answer}\n")
        print(agent.finalize_header)
        print(f" - {agent.finalize_saved_label}: {spec_path}")
        if spec_artifacts.pdf_path is not None:
            print(f" - {agent.finalize_pdf_label}: {spec_artifacts.pdf_path}")
        if record_id:
            print(f"{agent.finalize_record_label}: {record_id}")

    return {
        "persona": responder.persona,
        "spec_path": spec_path,
        "pdf_path": spec_artifacts.pdf_path,
        "transcript": transcript,
        "closing_feedback": closing_answer,
        "record_id": record_id,
        "review_warnings": review_warnings,
        "language": language_code,
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
                language=responder.language,
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
