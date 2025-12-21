"""Prompt scaffolding for the Business Analyst interview agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

from .config import InterviewScope

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES: Tuple[str, ...] = ("en", "es")


@dataclass(slots=True)
class ScopePromptPack:
    """Container for interview prompt templates bound to a scope."""

    kickoff: str
    follow_up: str
    summarization: str


@dataclass(slots=True)
class GuidanceMessages:
    """Prompt bundle used to steer the LLM orchestration flow."""

    system: str
    interviewer: str


@dataclass(slots=True)
class LanguagePack:
    """Aggregated prompt assets for a supported language."""

    prompts: Mapping[InterviewScope, ScopePromptPack]
    guidance: GuidanceMessages
    closing_prompt: str
    as_is_review_prompt: str
    to_be_review_prompt: str
    feedback_ack_positive: str
    feedback_ack_negative: str
    finalize_header: str
    finalize_saved_label: str
    finalize_pdf_label: str
    finalize_record_label: str


STRUCTURED_SUMMARY_INSTRUCTIONS: Dict[str, str] = {
    "en": (
        "Respond ONLY with valid JSON matching the schema below. Populate every "
        "field even if the information is incomplete—use short placeholder text "
        "such as 'Pending clarification' rather than leaving a value empty. Do "
        "not wrap the JSON in markdown fences or include commentary.\n"
        "{\n"
        '  "title": string,\n'
        '  "project_overview": string,\n'
        '  "project_objective": string,\n'
        '  "scope": {\n'
        '    "overview": string,\n'
        '    "in_scope": string,\n'
        '    "out_of_scope": string\n'
        '  },\n'
        '  "current_state": [string],\n'
        '  "current_processes": [\n'
        '    {"name": string, "happy_path": [string], "unhappy_path": [string]}\n'
        '  ],\n'
        '  "future_state": [string],\n'
        '  "future_processes": [\n'
        '    {"name": string, "happy_path": [string], "unhappy_path": [string]}\n'
        '  ],\n'
        '  "personas": [\n'
        '    {"name": string, "description": string}\n'
        '  ],\n'
        '  "functional_overview": string,\n'
        '  "non_functional_requirements": [string],\n'
        '  "assumptions": [string],\n'
        '  "risks": [string],\n'
        '  "open_issues": [string],\n'
        '  "functional_requirements": [\n'
        '    {"description": string, "business_rules": string}\n'
        '  ]\n'
        "}\n"
        "Guidance: ensure scope.overview concisely summarizes the initiative, "
        "and the scope.in_scope / scope.out_of_scope values are bullet-ready "
        "sentences. Describe the current_state with at least three concise "
        "bullets covering the existing process, systems, or pain points. Capture "
        "the future_state with 3-6 action-oriented bullet statements that outline "
        "the envisioned improvements, capabilities, or outcomes. Include "
        "current_processes entries that list each process name with 3-6 "
        "happy-path steps and key unhappy-path exceptions when available. Include "
        "future_processes entries that document the target process design with "
        "clear happy-path steps and important exceptions. Provide at least "
        "three personas when available, a minimum of three "
        "non_functional_requirements entries, and at least five "
        "functional_requirements. Each functional requirement should reference "
        "key systems, data dependencies, and validation expectations."
    ),
    "es": (
        "Responde ÚNICAMENTE con JSON válido que cumpla el esquema siguiente. "
        "Completa todos los campos aunque la información esté incompleta; usa "
        "texto breve como 'Pendiente de aclaración' en lugar de dejar valores "
        "vacíos. No encierres el JSON en bloques de markdown ni añadas comentarios.\n"
        "{\n"
        '  "title": string,\n'
        '  "project_overview": string,\n'
        '  "project_objective": string,\n'
        '  "scope": {\n'
        '    "overview": string,\n'
        '    "in_scope": string,\n'
        '    "out_of_scope": string\n'
        '  },\n'
        '  "current_state": [string],\n'
        '  "current_processes": [\n'
        '    {"name": string, "happy_path": [string], "unhappy_path": [string]}\n'
        '  ],\n'
        '  "future_state": [string],\n'
        '  "future_processes": [\n'
        '    {"name": string, "happy_path": [string], "unhappy_path": [string]}\n'
        '  ],\n'
        '  "personas": [\n'
        '    {"name": string, "description": string}\n'
        '  ],\n'
        '  "functional_overview": string,\n'
        '  "non_functional_requirements": [string],\n'
        '  "assumptions": [string],\n'
        '  "risks": [string],\n'
        '  "open_issues": [string],\n'
        '  "functional_requirements": [\n'
        '    {"description": string, "business_rules": string}\n'
        '  ]\n'
        "}\n"
        "Guía: asegúrate de que scope.overview resuma con precisión la iniciativa y "
        "de que scope.in_scope y scope.out_of_scope sean oraciones listas para usar "
        "como viñetas. Describe current_state con al menos tres viñetas concisas que "
        "cubran el proceso existente, los sistemas o los puntos de dolor. Redacta "
        "future_state con entre 3 y 6 viñetas orientadas a la acción que indiquen "
        "las mejoras, capacidades u objetivos esperados. Incluye current_processes "
        "enumerando cada proceso con 3 a 6 pasos de camino feliz y las excepciones "
        "críticas del camino alterno cuando existan. Incluye future_processes con el "
        "diseño objetivo del proceso, pasos claros de camino feliz y excepciones clave. "
        "Proporciona al menos tres personas cuando existan, un mínimo de tres entradas "
        "en non_functional_requirements y al menos cinco functional_requirements. Cada "
        "requisito funcional debe referenciar sistemas clave, dependencias de datos y "
        "expectativas de validación. Redacta todos los valores en español neutro."
    ),
}


PROMPTS_EN: Dict[InterviewScope, ScopePromptPack] = {
    InterviewScope.PROJECT: ScopePromptPack(
        kickoff=(
            """
            You are a senior Business Analyst. Start a discovery interview to
            understand the project scope. Clarify objectives, business drivers,
            stakeholders, success metrics, timeline expectations, and risks.
            Ask one question at a time, observe the user's responses, and adapt
            your wording to stay conversational and professional.
            """.strip()
        ),
        follow_up=(
            """
            Given the conversation so far, craft the next probing question that
            uncovers missing details about requirements, constraints, budget,
            dependencies, or acceptance criteria. Reference prior answers when
            appropriate to show active listening.
            """.strip()
        ),
        summarization=(
            """
            Summarize the collected information into a structured functional
            specification for a project. Highlight scope boundaries,
            objectives, personas/stakeholders, functional requirements,
            requirements, assumptions, risks, and open issues.
            {summary_instruction}
            """.strip()
        ).format(summary_instruction=STRUCTURED_SUMMARY_INSTRUCTIONS["en"]),
    ),
    InterviewScope.PROCESS: ScopePromptPack(
        kickoff=(
            """
            You are interviewing a process owner to map and improve an existing
            process. Begin by clarifying the process goals, triggers, inputs,
            outputs, stakeholders, key steps, pain points, and compliance
            needs.
            Be warm yet concise.
            """.strip()
        ),
        follow_up=(
            """
            Propose the next insightful question that helps document the
            current process, exceptions, systems involved, hand-offs, metrics,
            improvement opportunities. Reference what you have already learned.
            """.strip()
        ),
        summarization=(
            """
            Create a functional specification for a process initiative. Include
            context, goals, scope, process overview, detailed requirements,
            supporting systems, metrics, risks, and recommended improvements.
            {summary_instruction}
            """.strip()
        ).format(summary_instruction=STRUCTURED_SUMMARY_INSTRUCTIONS["en"]),
    ),
    InterviewScope.CHANGE_REQUEST: ScopePromptPack(
        kickoff=(
            """
            Act as a Business Analyst qualifying a change request. Begin by
            confirming the requestor, business justification, impacted systems,
            desired outcomes, urgency, and constraints. Maintain a consultative
            tone.
            """.strip()
        ),
        follow_up=(
            """
            Formulate the next diagnostic question to capture affected user
            journeys, process updates, data impacts, dependencies, testing
            needs, rollout considerations, and success indicators.
            """.strip()
        ),
        summarization=(
            """
            Summarize the change request details as a functional specification.
            Cover current state, requested change, rationale, scope, impacted
            personas, requirements, technical considerations, risks, metrics,
            validation steps, and outstanding questions.
            {summary_instruction}
            """.strip()
        ).format(summary_instruction=STRUCTURED_SUMMARY_INSTRUCTIONS["en"]),
    ),
}


PROMPTS_ES: Dict[InterviewScope, ScopePromptPack] = {
    InterviewScope.PROJECT: ScopePromptPack(
        kickoff=(
            """
            Actúas como un Analista de Negocio senior. Inicia una entrevista de
            descubrimiento para entender el alcance del proyecto. Aclara
            objetivos, impulsores del negocio, stakeholders, métricas de éxito,
            expectativas de tiempo y riesgos. Haz una pregunta a la vez,
            observa las respuestas de la persona usuaria y adapta tu redacción
            para mantener un tono conversacional y profesional. Formula todas
            las preguntas únicamente en español.
            """.strip()
        ),
        follow_up=(
            """
            Con base en la conversación hasta ahora, formula la siguiente
            pregunta de sondeo que descubra detalles faltantes sobre
            requisitos, restricciones, presupuesto, dependencias o criterios de
            aceptación. Haz referencia a respuestas previas cuando corresponda
            para demostrar escucha activa. Escribe la pregunta en español
            claro y profesional.
            """.strip()
        ),
        summarization=(
            """
            Resume la información recopilada en una especificación funcional
            para un proyecto. Destaca los límites del alcance, los objetivos,
            las personas/stakeholders, los requisitos funcionales, los
            requisitos no funcionales, los supuestos, los riesgos y los temas
            pendientes. Entrega todo el contenido en español neutro.
            {summary_instruction}
            """.strip()
        ).format(summary_instruction=STRUCTURED_SUMMARY_INSTRUCTIONS["es"]),
    ),
    InterviewScope.PROCESS: ScopePromptPack(
        kickoff=(
            """
            Estás entrevistando a la persona dueña de un proceso para mapearlo
            y mejorarlo. Comienza aclarando los objetivos del proceso,
            detonadores, entradas, salidas, stakeholders, pasos clave, puntos de
            dolor y necesidades de cumplimiento. Mantén un tono cordial y
            conciso. Formula tus preguntas solo en español.
            """.strip()
        ),
        follow_up=(
            """
            Propón la siguiente pregunta reveladora que ayude a documentar el
            proceso actual, sus excepciones, los sistemas involucrados, las
            transferencias, métricas y oportunidades de mejora. Haz referencia a
            lo que ya aprendiste. Asegúrate de expresarla en español.
            """.strip()
        ),
        summarization=(
            """
            Crea una especificación funcional para una iniciativa de procesos.
            Incluye contexto, objetivos, alcance, visión general del proceso,
            requisitos detallados, sistemas de apoyo, métricas, riesgos y
            mejoras recomendadas. Redacta la especificación en español.
            {summary_instruction}
            """.strip()
        ).format(summary_instruction=STRUCTURED_SUMMARY_INSTRUCTIONS["es"]),
    ),
    InterviewScope.CHANGE_REQUEST: ScopePromptPack(
        kickoff=(
            """
            Actúa como Analista de Negocio calificando una solicitud de cambio.
            Comienza confirmando quién solicita, la justificación de negocio,
            los sistemas impactados, los resultados deseados, la urgencia y las
            restricciones. Mantén un tono consultivo y comunica todo en español.
            """.strip()
        ),
        follow_up=(
            """
            Formula la siguiente pregunta diagnóstica para capturar viajes de
            usuario afectados, actualizaciones de procesos, impactos en datos,
            dependencias, necesidades de pruebas, consideraciones de despliegue
            e indicadores de éxito. Escribe la pregunta en español.
            """.strip()
        ),
        summarization=(
            """
            Resume los detalles de la solicitud de cambio como una
            especificación funcional. Cubre el estado actual, el cambio
            solicitado, la justificación, el alcance, las personas impactadas,
            los requisitos, consideraciones técnicas, riesgos, métricas, pasos
            de validación y preguntas pendientes. Entrega toda la redacción en
            español neutro.
            {summary_instruction}
            """.strip()
        ).format(summary_instruction=STRUCTURED_SUMMARY_INSTRUCTIONS["es"]),
    ),
}


LANGUAGE_PACKS: Dict[str, LanguagePack] = {
    "en": LanguagePack(
        prompts=PROMPTS_EN,
        guidance=GuidanceMessages(
            system=(
                "You orchestrate a structured requirements interview. Maintain a "
                "professional Business Analyst persona, summarize frequently, "
                "and capture actionable requirements."
            ),
            interviewer=(
                "Stay focused on eliciting requirements. Ask concise, empathetic "
                "questions. Confirm understanding before moving on."
            ),
        ),
        closing_prompt=(
            "Before we wrap up, is there anything you'd like to add or "
            "change in the specification?"
        ),
        as_is_review_prompt=(
            "Do these bullet points capture the current AS-IS state accurately? "
            "Please add, edit, or approve them so we document today's reality "
            "correctly."
        ),
        to_be_review_prompt=(
            "Do these bullet points reflect the desired TO-BE experience? Please "
            "add, edit, or approve them so we capture the future-state vision "
            "correctly."
        ),
        feedback_ack_positive=(
            "BA Agent: Thanks! I'll incorporate that feedback into the specification."
        ),
        feedback_ack_negative=(
            "BA Agent: Understood. We'll keep the specification as-is."
        ),
        finalize_header=(
            "Interview complete. Functional specification saved to:"
        ),
        finalize_saved_label="Markdown",
        finalize_pdf_label="PDF",
        finalize_record_label="Transcript id",
    ),
    "es": LanguagePack(
        prompts=PROMPTS_ES,
        guidance=GuidanceMessages(
            system=(
                "Orquestas una entrevista estructurada de requisitos. Mantén una "
                "persona profesional de Analista de Negocio, resume con frecuencia "
                "y captura requisitos accionables. Realiza todas tus preguntas, "
                "respuestas y resúmenes únicamente en español neutro."
            ),
            interviewer=(
                "Mantente enfocado en obtener requisitos. Haz preguntas concisas y "
                "empáticas. Confirma la comprensión antes de continuar y asegúrate "
                "de comunicarte siempre en español."
            ),
        ),
        closing_prompt=(
            "Antes de finalizar, ¿hay algo que quieras agregar o cambiar en la "
            "especificación?"
        ),
        as_is_review_prompt=(
            "¿Estas viñetas describen con precisión el estado ACTUAL (AS-IS)? "
            "Por favor agrega, edita o aprueba para documentar correctamente la "
            "situación actual."
        ),
        to_be_review_prompt=(
            "¿Estas viñetas reflejan la experiencia FUTURA (TO-BE)? Por favor agrega, "
            "edita o aprueba para capturar con exactitud la visión objetivo."
        ),
        feedback_ack_positive=(
            "Agente BA: ¡Gracias! Incorporaré ese comentario en la especificación."
        ),
        feedback_ack_negative=(
            "Agente BA: Entendido. Mantendremos la especificación sin cambios."
        ),
        finalize_header=(
            "Entrevista completa. Especificación funcional guardada en:"
        ),
        finalize_saved_label="Markdown",
        finalize_pdf_label="PDF",
        finalize_record_label="Id de transcripción",
    ),
}


def _normalize_language_code(language: object | None) -> str | None:
    if isinstance(language, str):
        normalized = language.strip().lower()
        if not normalized:
            return None
        normalized = normalized.replace("_", "-")
        normalized = normalized.split("-")[0]
        if normalized in LANGUAGE_PACKS:
            return normalized
    return None


def resolve_language_code(language: object | None) -> str:
    """Return a supported language code, falling back to the default."""

    return _normalize_language_code(language) or DEFAULT_LANGUAGE


def get_language_pack(language: object | None) -> Tuple[str, LanguagePack]:
    """Resolve and return the language resources for the given code."""

    code = resolve_language_code(language)
    return code, LANGUAGE_PACKS[code]
