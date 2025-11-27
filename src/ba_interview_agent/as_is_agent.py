"""Utilities for deriving and validating AS-IS (current state) summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, cast

from .config import AppSettings, InterviewScope
from .maf_client import ChatMessage, MAFChatClient


@dataclass(slots=True)
class AsIsProcess:
    """Describes an AS-IS business process with happy/unhappy paths."""

    name: str
    happy_path: List[str]
    unhappy_path: List[str]


@dataclass(slots=True)
class AsIsDraft:
    """LLM-generated AS-IS information prior to stakeholder confirmation."""

    items: List[str]
    processes: List[AsIsProcess]


@dataclass(slots=True)
class AsIsReviewResult:
    """Outcome returned after confirming an AS-IS summary."""

    items: List[str]
    processes: List[AsIsProcess]
    stakeholder_comment: str


class AsIsDerivationAgent:
    """Generates AS-IS details from the current specification context."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        scope: InterviewScope,
    ) -> None:
        self._chat_client = MAFChatClient(settings.model)
        self._scope = scope

    async def derive(
        self,
        *,
        summary_data: Dict[str, Any],
        conversation_excerpt: str,
        fallback_items: Sequence[str],
        fallback_processes: Sequence[AsIsProcess],
    ) -> AsIsDraft:
        payload_json = json.dumps(summary_data, ensure_ascii=False, indent=2)
        excerpt = conversation_excerpt.strip()
        if not excerpt:
            excerpt = "(No additional conversation excerpt supplied.)"
        prompt = (
            "You are a senior Business Analyst documenting the AS-IS (current "
            "state) for an engagement. Study the structured functional "
            "specification summary and conversation excerpt below. Craft "
            "3-6 concise bullet statements that capture the current "
            "processes, systems, pain points, and workarounds that exist "
            "today. Focus on the present state only—avoid future or to-be "
            "language. Respond ONLY with JSON in the shape "
            '{"current_state": [string, ...], '
            '"processes": [{"name": string, '
            '"happy_path": [string], "unhappy_path": [string]}, ...]}. '
            "Each bullet should stay under 220 characters and be "
            "stakeholder-friendly. "
            "For each process, outline 3-6 steps for the "
            "primary (happy) path and key exception/edge cases (unhappy path)."
        )
        user_message = (
            f"Engagement scope: {self._scope.value}.\n\n"
            "Structured functional specification summary (JSON):\n"
            f"{payload_json}\n\n"
            "Conversation excerpt:\n"
            f"{excerpt}"
        )
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You translate conversations into precise AS-IS summaries "
                    "that reflect current operations."
                ),
            ),
            ChatMessage(role="user", content=prompt),
            ChatMessage(role="user", content=user_message),
        ]
        response = await self._chat_client.complete(messages)
        draft = self._parse_response(response.content)
        if draft is None:
            fallback_list = [
                AsIsProcess(
                    name=process.name,
                    happy_path=list(process.happy_path),
                    unhappy_path=list(process.unhappy_path),
                )
                for process in fallback_processes
            ]
            bullet_items = [
                str(entry).strip()
                for entry in fallback_items
                if str(entry).strip()
            ]
            if not bullet_items:
                bullet_items = [
                    "Current state details pending stakeholder confirmation."
                ]
            return AsIsDraft(items=bullet_items, processes=fallback_list)
        if not draft.items:
            draft.items = [
                str(entry).strip()
                for entry in fallback_items
                if str(entry).strip()
            ]
        if not draft.items:
            draft.items = [
                "Current state details pending stakeholder confirmation."
            ]
        if not draft.processes:
            draft.processes = [
                AsIsProcess(
                    name=process.name,
                    happy_path=list(process.happy_path),
                    unhappy_path=list(process.unhappy_path),
                )
                for process in fallback_processes
            ]
        return draft

    @staticmethod
    def _parse_response(raw: str) -> AsIsDraft | None:
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
            data = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        current_state = data.get("current_state")
        items: List[str] = []
        if isinstance(current_state, list):
            current_state_entries = cast(List[Any], current_state)
            for entry in current_state_entries:
                text_value = str(entry).strip()
                if not text_value:
                    continue
                sanitized = text_value.replace("|", "/").replace("\n", " ")
                items.append(sanitized)
        processes: List[AsIsProcess] = []
        processes_raw = data.get("processes")
        if isinstance(processes_raw, list):
            process_entries = cast(List[Any], processes_raw)
            for process_entry in process_entries:
                if not isinstance(process_entry, dict):
                    continue
                process_dict = cast(Dict[str, Any], process_entry)
                name = str(process_dict.get("name", "")).strip()
                if not name:
                    continue
                happy_steps = AsIsDerivationAgent._sanitize_steps(
                    process_dict.get("happy_path")
                )
                unhappy_steps = AsIsDerivationAgent._sanitize_steps(
                    process_dict.get("unhappy_path")
                )
                if not happy_steps and not unhappy_steps:
                    continue
                processes.append(
                    AsIsProcess(
                        name=name.replace("|", "/"),
                        happy_path=happy_steps,
                        unhappy_path=unhappy_steps,
                    )
                )
        return AsIsDraft(items=items, processes=processes)

    @staticmethod
    def _sanitize_steps(raw_steps: Any) -> List[str]:
        steps: List[str] = []
        if isinstance(raw_steps, list):
            for entry in cast(List[Any], raw_steps):
                text = str(entry).strip()
                if text:
                    steps.append(text.replace("|", "/").replace("\n", " "))
        elif isinstance(raw_steps, str):
            segments = [segment.strip() for segment in raw_steps.split(";")]
            for segment in segments:
                if segment:
                    steps.append(segment.replace("|", "/"))
        return steps


class ConsoleAsIsReviewer:
    """Prompts a human stakeholder to confirm or extend the AS-IS summary."""

    async def confirm_as_is(
        self,
        *,
        proposed_items: Sequence[str],
        proposed_processes: Sequence[AsIsProcess],
        spec_text: str,
        question: str,
    ) -> AsIsReviewResult:
        _ = spec_text
        print()  # noqa: T201 - CLI UX spacing
        print(
            "BA Agent: I'd like to confirm the current AS-IS understanding."
        )  # noqa: T201
        print(question)  # noqa: T201
        print("\nProposed AS-IS summary:")  # noqa: T201
        for index, item in enumerate(proposed_items, start=1):
            print(f"  {index}. {item}")  # noqa: T201
        items = [entry for entry in proposed_items if str(entry).strip()]
        while True:
            addition = input(  # noqa: PLW1514 - intentional CLI input
                "Add another AS-IS detail (leave blank to continue): "
            ).strip()
            if not addition:
                break
            items.append(addition)
        processes: List[AsIsProcess] = [
            AsIsProcess(
                name=process.name,
                happy_path=list(process.happy_path),
                unhappy_path=list(process.unhappy_path),
            )
            for process in proposed_processes
        ]
        print("\nIdentified AS-IS processes:")  # noqa: T201
        if not processes:
            print("  (none captured yet)")  # noqa: T201
        else:
            for index, process in enumerate(processes, start=1):
                print(f"  {index}. {process.name}")  # noqa: T201
                if process.happy_path:
                    print("     Happy path:")  # noqa: T201
                    for step_num, step in enumerate(
                        process.happy_path, start=1
                    ):
                        print(f"       {step_num}. {step}")  # noqa: T201
                if process.unhappy_path:
                    print("     Unhappy path / exceptions:")  # noqa: T201
                    for step_num, step in enumerate(
                        process.unhappy_path, start=1
                    ):
                        print(f"       {step_num}. {step}")  # noqa: T201
        while True:
            new_process_name = input(  # noqa: PLW1514
                "Add another AS-IS process (leave blank to continue): "
            ).strip()
            if not new_process_name:
                break
            happy_input = input(  # noqa: PLW1514
                "  Enter happy-path steps separated by ';' (optional): "
            ).strip()
            unhappy_input = input(  # noqa: PLW1514
                "  Enter unhappy-path steps separated by ';' (optional): "
            ).strip()
            happy_steps = [
                step.strip()
                for step in happy_input.split(";")
                if step.strip()
            ]
            unhappy_steps = [
                step.strip()
                for step in unhappy_input.split(";")
                if step.strip()
            ]
            processes.append(
                AsIsProcess(
                    name=new_process_name,
                    happy_path=happy_steps,
                    unhappy_path=unhappy_steps,
                )
            )
        stakeholder_comment = input(  # noqa: PLW1514 - intentional CLI input
            "Stakeholder response/approval note: "
        ).strip()
        if not stakeholder_comment:
            stakeholder_comment = "Understood and approved."
        return AsIsReviewResult(
            items=items,
            processes=processes,
            stakeholder_comment=stakeholder_comment,
        )
