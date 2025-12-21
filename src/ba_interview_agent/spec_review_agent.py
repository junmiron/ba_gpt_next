"""Functional specification review agent."""

from __future__ import annotations

import json

from dataclasses import dataclass
from string import Template
from typing import Any, Dict, List, Optional, Sequence, cast

from .config import AppSettings, InterviewScope
from .maf_client import ChatMessage, MAFChatClient
from .prompts import DEFAULT_LANGUAGE, resolve_language_code


@dataclass(slots=True)
class FollowUpQuestion:
    """Represents a targeted follow-up question suggested by the reviewer."""

    question: str
    subject: Optional[str] = None
    reason: Optional[str] = None


@dataclass(slots=True)
class ReviewerLanguageConfig:
    """Localized assets for the reviewer agent."""

    system_prompt: str
    prompt_template: Template
    outstanding_missing_template: str
    outstanding_table_generic: str
    outstanding_follow_subject_template: str
    outstanding_follow_generic_template: str
    fallback_no_content_table: str
    fallback_no_content_feedback: str
    fallback_parse_error: str
    fallback_feedback_missing_subjects: str
    fallback_feedback_table_invalid: str
    fallback_feedback_follow_up: str
    fallback_feedback_success: str

    def build_prompt(
        self,
        *,
        scope_label: str,
        subject_lines: str,
        spec_payload: str,
    ) -> str:
        return self.prompt_template.substitute(
            scope_label=scope_label,
            subject_lines=subject_lines,
            spec_payload=spec_payload,
        )


REVIEWER_LANGUAGE_CONFIGS: Dict[str, ReviewerLanguageConfig] = {
    "en": ReviewerLanguageConfig(
        system_prompt="You enforce specification QA standards.",
        prompt_template=Template(
            """
You are an experienced Business Analyst quality reviewer. Evaluate the functional specification for completeness and formatting.

Engagement scope: $scope_label.

Interview subjects that must be represented in the final document (use these names exactly when referencing them):
$subject_lines

Checklist:
- Confirm each subject above is covered with actionable detail.
- Verify the specification includes a Markdown table titled 'Functional Requirements' with columns 'Spec ID', 'Specification Description', and 'Business Rules/Data Dependency'.
- Each Spec ID must follow the sequential pattern FR-1, FR-2, etc., without skipping numbers.
- The description column should be concise (1-3 sentences).
- The third column must clearly state validation or business rules and relevant data dependencies.
- If anything is missing, craft targeted follow-up question(s) the interviewer can ask the stakeholder to resolve it.

Respond ONLY with JSON using this schema:{
  "all_subjects_present": bool,
  "missing_subjects": [string],
  "table_valid": bool,
  "table_feedback": string,
  "follow_up_questions": [
    {
      "question": string,
      "subject": string | null,
      "reason": string
    }
  ],
  "feedback_for_interviewer": string
}
If the table needs changes, set table_valid to false and provide actionable guidance in table_feedback.
Provide at least one follow_up_question whenever information is missing or the table needs corrections.
Do not include any text outside of the JSON object.

Functional specification to review:
<<<
$spec_payload
>>>""".strip()
        ),
        outstanding_missing_template="Subjects still missing: {subjects}",
        outstanding_table_generic=(
            "Functional Requirements table still requires updates to pass validation."
        ),
        outstanding_follow_subject_template="Follow up on '{subject}': {detail}",
        outstanding_follow_generic_template="Follow up needed: {detail}",
        fallback_no_content_table=(
            "Specification review returned no content and could not complete the checklist."
        ),
        fallback_no_content_feedback="Specification review returned no content.",
        fallback_parse_error="Could not parse reviewer JSON output.",
        fallback_feedback_missing_subjects=(
            "Specification appears to miss one or more interview subjects."
        ),
        fallback_feedback_table_invalid="Requirements table formatting appears incorrect.",
        fallback_feedback_follow_up=(
            "Additional clarifications are recommended based on the draft."
        ),
        fallback_feedback_success="Specification meets the review checklist.",
    ),
    "es": ReviewerLanguageConfig(
        system_prompt="Supervisas el aseguramiento de calidad de la especificacion.",
        prompt_template=Template(
            """
Actuas como una persona revisora experta en Analisis de Negocio. Evalua la especificacion funcional para validar completitud y formato.

Ambito de la colaboracion: $scope_label.

Temas de entrevista que deben aparecer en el documento final (usa exactamente estos nombres al referirte a ellos):
$subject_lines

Lista de verificacion:
- Confirma que cada tema anterior este cubierto con detalle accionable.
- Verifica que la especificacion incluya una tabla Markdown titulada 'Functional Requirements' con las columnas 'Spec ID', 'Specification Description' y 'Business Rules/Data Dependency'.
- Cada Spec ID debe seguir la secuencia FR-1, FR-2, etc., sin saltar numeros.
- La columna de descripcion debe ser concisa (1-3 oraciones).
- La tercera columna debe detallar claramente reglas de validacion o de negocio y dependencias de datos relevantes.
- Si falta informacion, formula preguntas puntuales que la persona entrevistadora pueda hacer a la parte interesada para resolverlo.

Responde SOLO con JSON usando este esquema:{
  "all_subjects_present": bool,
  "missing_subjects": [string],
  "table_valid": bool,
  "table_feedback": string,
  "follow_up_questions": [
    {
      "question": string,
      "subject": string | null,
      "reason": string
    }
  ],
  "feedback_for_interviewer": string
}
Si la tabla necesita ajustes, establece table_valid en false y proporciona orientaciones accionables en table_feedback.
Incluye al menos una follow_up_question siempre que falte informacion o la tabla requiera correcciones.
No agregues texto fuera del objeto JSON.

Especificacion funcional a revisar:
<<<
$spec_payload
>>>""".strip()
        ),
        outstanding_missing_template="Temas pendientes: {subjects}",
        outstanding_table_generic=(
            "La tabla de Functional Requirements aun necesita ajustes para pasar la validacion."
        ),
        outstanding_follow_subject_template="Dar seguimiento a '{subject}': {detail}",
        outstanding_follow_generic_template="Se requiere seguimiento: {detail}",
        fallback_no_content_table=(
            "La revision de la especificacion no devolvio contenido y no pudo completar la lista de verificacion."
        ),
        fallback_no_content_feedback="La revision de la especificacion no devolvio contenido.",
        fallback_parse_error="No se pudo analizar la salida JSON de la persona revisora.",
        fallback_feedback_missing_subjects=(
            "Parece que faltan temas de entrevista en la especificacion."
        ),
        fallback_feedback_table_invalid="El formato de la tabla de requisitos parece incorrecto.",
        fallback_feedback_follow_up=(
            "Se recomiendan aclaraciones adicionales con base en el borrador."
        ),
        fallback_feedback_success="La especificacion cumple con la lista de verificacion.",
    ),
}


def _get_reviewer_config(language: str) -> ReviewerLanguageConfig:
    return REVIEWER_LANGUAGE_CONFIGS.get(language, REVIEWER_LANGUAGE_CONFIGS[DEFAULT_LANGUAGE])


@dataclass(slots=True)
class SpecificationReview:
    """Result of reviewing a functional specification draft."""

    all_subjects_present: bool
    missing_subjects: List[str]
    table_valid: bool
    table_feedback: str
    follow_up_questions: List[FollowUpQuestion]
    feedback_for_interviewer: str
    language: str = DEFAULT_LANGUAGE

    @property
    def requires_follow_up(self) -> bool:
        return (
            (not self.all_subjects_present)
            or (not self.table_valid)
            or bool(self.follow_up_questions)
        )

    def fingerprint(self) -> str:
        """Generate a stable signature representing the review outcome."""

        follow_up_payload: List[Dict[str, object]] = [
            {
                "question": item.question,
                "subject": item.subject,
                "reason": item.reason,
            }
            for item in self.follow_up_questions
        ]
        payload: Dict[str, object] = {
            "all_subjects_present": self.all_subjects_present,
            "missing_subjects": sorted(set(self.missing_subjects)),
            "table_valid": self.table_valid,
            "table_feedback": self.table_feedback,
            "follow_up_questions": follow_up_payload,
            "feedback_for_interviewer": self.feedback_for_interviewer,
        }
        return json.dumps(payload, sort_keys=True)

    def outstanding_items(self) -> List[str]:
        """Summarize review concerns that remain unresolved."""

        config = _get_reviewer_config(self.language)
        items: List[str] = []
        if self.missing_subjects:
            missing = ", ".join(sorted(set(self.missing_subjects)))
            items.append(config.outstanding_missing_template.format(subjects=missing))
        if not self.table_valid:
            if self.table_feedback:
                items.append(self.table_feedback)
            else:
                items.append(config.outstanding_table_generic)
        for follow_up in self.follow_up_questions:
            detail = follow_up.question
            if follow_up.reason:
                detail = f"{detail} ({follow_up.reason})"
            if follow_up.subject:
                items.append(
                    config.outstanding_follow_subject_template.format(
                        subject=follow_up.subject,
                        detail=detail,
                    )
                )
            else:
                items.append(
                    config.outstanding_follow_generic_template.format(
                        detail=detail
                    )
                )
        if not items and self.feedback_for_interviewer:
            items.append(self.feedback_for_interviewer)
        return items


class FunctionalSpecificationReviewAgent:
    """Reviews functional specifications for completeness and format."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        scope: InterviewScope,
        subjects: Sequence[str],
        language: Optional[str] = None,
    ) -> None:
        self._chat_client = MAFChatClient(settings.model)
        self._subjects = list(subjects)
        self._scope = scope
        self._language = DEFAULT_LANGUAGE
        self._review_config = _get_reviewer_config(DEFAULT_LANGUAGE)
        self.set_language(language)

    def set_language(self, language: Optional[str]) -> None:
        code = resolve_language_code(language or self._language)
        if code == self._language:
            return
        self._language = code
        self._review_config = _get_reviewer_config(code)

    async def review(self, specification_markdown: str) -> SpecificationReview:
        """Inspect a functional specification and flag missing items."""

        subject_lines = "\n".join(f"- {name}" for name in self._subjects)
        spec_payload = specification_markdown.strip()
        scope_label = self._scope.value.replace("_", " ")
        prompt = self._review_config.build_prompt(
            scope_label=scope_label,
            subject_lines=subject_lines,
            spec_payload=spec_payload,
        )
        messages = [
            ChatMessage(role="system", content=self._review_config.system_prompt),
            ChatMessage(role="user", content=prompt),
        ]
        response = await self._chat_client.complete(messages)
        return self._parse_response(response.content)

    def _parse_response(self, raw: str) -> SpecificationReview:
        text = raw.strip()
        config = self._review_config
        if not text:
            return SpecificationReview(
                all_subjects_present=False,
                missing_subjects=self._subjects.copy(),
                table_valid=False,
                table_feedback=config.fallback_no_content_table,
                follow_up_questions=[],
                feedback_for_interviewer=config.fallback_no_content_feedback,
                language=self._language,
            )
        json_candidate = text
        if not json_candidate.lstrip().startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_candidate = text[start : end + 1]
        try:
            data = json.loads(json_candidate)
        except json.JSONDecodeError:
            return SpecificationReview(
                all_subjects_present=False,
                missing_subjects=self._subjects.copy(),
                table_valid=False,
                table_feedback=config.fallback_parse_error,
                follow_up_questions=[],
                feedback_for_interviewer=text,
                language=self._language,
            )
        all_subjects_present = bool(data.get("all_subjects_present", False))
        missing_subjects = [
            str(subject)
            for subject in data.get("missing_subjects", [])
            if str(subject).strip()
        ]
        table_valid = bool(data.get("table_valid", False))
        table_feedback = str(data.get("table_feedback", "")).strip()
        feedback = str(data.get("feedback_for_interviewer", "")).strip()
        follow_up_questions: List[FollowUpQuestion] = []
        raw_follow_ups_value = data.get("follow_up_questions")
        if not isinstance(raw_follow_ups_value, list):
            raw_follow_ups_value = []
        raw_follow_ups = cast(List[Any], raw_follow_ups_value)
        for raw_item in raw_follow_ups:
            if not isinstance(raw_item, dict):
                continue
            item = cast(Dict[str, Any], raw_item)
            question_text = str(item.get("question", "")).strip()
            if not question_text:
                continue
            subject_value = item.get("subject")
            subject_name = (
                str(subject_value).strip()
                if subject_value is not None and str(subject_value).strip()
                else None
            )
            reason = str(item.get("reason", "")).strip() or None
            follow_up_questions.append(
                FollowUpQuestion(
                    question=question_text,
                    subject=subject_name,
                    reason=reason,
                )
            )
        if not feedback:
            if not all_subjects_present:
                feedback = config.fallback_feedback_missing_subjects
            elif not table_valid:
                feedback = config.fallback_feedback_table_invalid
            elif follow_up_questions:
                feedback = config.fallback_feedback_follow_up
            else:
                feedback = config.fallback_feedback_success
        return SpecificationReview(
            all_subjects_present=all_subjects_present,
            missing_subjects=missing_subjects,
            table_valid=table_valid,
            table_feedback=table_feedback,
            follow_up_questions=follow_up_questions,
            feedback_for_interviewer=feedback,
            language=self._language,
        )
