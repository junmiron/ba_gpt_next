"""Functional specification review agent."""

from __future__ import annotations

import json

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, cast

from .config import AppSettings, InterviewScope
from .maf_client import ChatMessage, MAFChatClient


@dataclass(slots=True)
class FollowUpQuestion:
    """Represents a targeted follow-up question suggested by the reviewer."""

    question: str
    subject: Optional[str] = None
    reason: Optional[str] = None


@dataclass(slots=True)
class SpecificationReview:
    """Result of reviewing a functional specification draft."""

    all_subjects_present: bool
    missing_subjects: List[str]
    table_valid: bool
    table_feedback: str
    follow_up_questions: List[FollowUpQuestion]
    feedback_for_interviewer: str

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

        items: List[str] = []
        if self.missing_subjects:
            missing = ", ".join(sorted(set(self.missing_subjects)))
            items.append(f"Subjects still missing: {missing}")
        if not self.table_valid:
            if self.table_feedback:
                items.append(self.table_feedback)
            else:
                items.append(
                    "Functional Requirements table still requires "
                    "updates to pass validation."
                )
        for follow_up in self.follow_up_questions:
            detail = follow_up.question
            if follow_up.reason:
                detail = f"{detail} ({follow_up.reason})"
            if follow_up.subject:
                items.append(
                    f"Follow up on '{follow_up.subject}': {detail}"
                )
            else:
                items.append(f"Follow up needed: {detail}")
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
    ) -> None:
        self._chat_client = MAFChatClient(settings.model)
        self._subjects = list(subjects)
        self._scope = scope

    async def review(self, specification_markdown: str) -> SpecificationReview:
        """Inspect a functional specification and flag missing items."""

        subject_lines = "\n".join(f"- {name}" for name in self._subjects)
        spec_payload = specification_markdown.strip()
        prompt = (
            "You are an experienced Business Analyst quality reviewer. "
            "Evaluate the functional specification for completeness and "
            "formatting.\n\n"
            f"Engagement scope: {self._scope.value}.\n\n"
            "Interview subjects that must be represented in the final "
            "document (use these names exactly when referencing them):\n"
            f"{subject_lines}\n\n"
            "Checklist:\n"
            "- Confirm each subject above is covered with actionable detail.\n"
            "- Verify the specification includes a Markdown table titled "
            "'Functional Requirements' with columns 'Spec ID', "
            "'Specification Description', and 'Business Rules/Data "
            "Dependency'.\n"
            "- Each Spec ID must follow the sequential pattern FR-1, FR-2, "
            "etc., without skipping numbers.\n"
            "- The description column should be concise (1-3 sentences).\n"
            "- The third column must clearly state validation or business "
            "rules and relevant data dependencies.\n"
            "- If anything is missing, craft targeted follow-up question(s) "
            "the interviewer can ask the stakeholder to resolve it.\n\n"
            "Respond ONLY with JSON using this schema:{\n"
            "  \"all_subjects_present\": bool,\n"
            "  \"missing_subjects\": [string],\n"
            "  \"table_valid\": bool,\n"
            "  \"table_feedback\": string,\n"
            "  \"follow_up_questions\": [\n"
            "    {\n"
            "      \"question\": string,\n"
            "      \"subject\": string | null,\n"
            "      \"reason\": string\n"
            "    }\n"
            "  ],\n"
            "  \"feedback_for_interviewer\": string\n"
            "}\n"
            "If the table needs changes, set table_valid to false and provide "
            "actionable guidance in table_feedback.\n"
            "Provide at least one follow_up_question whenever information is "
            "missing or the table needs corrections.\n"
            "Do not include any text outside of the JSON object.\n\n"
            "Functional specification to review:\n<<<\n"
            f"{spec_payload}\n>>>")
        messages = [
            ChatMessage(
                role="system",
                content="You enforce specification QA standards.",
            ),
            ChatMessage(role="user", content=prompt),
        ]
        response = await self._chat_client.complete(messages)
        return self._parse_response(response.content)

    def _parse_response(self, raw: str) -> SpecificationReview:
        text = raw.strip()
        if not text:
            return SpecificationReview(
                all_subjects_present=False,
                missing_subjects=self._subjects.copy(),
                table_valid=False,
                table_feedback=(
                    "Specification review returned no content and could not "
                    "complete the checklist."
                ),
                follow_up_questions=[],
                feedback_for_interviewer=(
                    "Specification review returned no content."
                ),
            )
        json_candidate = text
        if not json_candidate.lstrip().startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_candidate = text[start:end + 1]
        try:
            data = json.loads(json_candidate)
        except json.JSONDecodeError:
            return SpecificationReview(
                all_subjects_present=False,
                missing_subjects=self._subjects.copy(),
                table_valid=False,
                table_feedback="Could not parse reviewer JSON output.",
                follow_up_questions=[],
                feedback_for_interviewer=text,
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
                feedback = (
                    "Specification appears to miss one or more interview "
                    "subjects."
                )
            elif not table_valid:
                feedback = "Requirements table formatting appears incorrect."
            elif follow_up_questions:
                feedback = (
                    "Additional clarifications are recommended based on the "
                    "draft."
                )
            else:
                feedback = "Specification meets the review checklist."
        return SpecificationReview(
            all_subjects_present=all_subjects_present,
            missing_subjects=missing_subjects,
            table_valid=table_valid,
            table_feedback=table_feedback,
            follow_up_questions=follow_up_questions,
            feedback_for_interviewer=feedback,
        )
