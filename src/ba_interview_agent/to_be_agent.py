"""Utilities for deriving and validating TO-BE (future state) summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, cast

from .config import AppSettings, InterviewScope
from .maf_client import ChatMessage, MAFChatClient


@dataclass(slots=True)
class ToBeProcess:
    """Describes a TO-BE business process with happy/unhappy scenarios."""

    name: str
    happy_path: List[str]
    unhappy_path: List[str]


@dataclass(slots=True)
class ToBeDraft:
    """LLM-generated TO-BE information prior to stakeholder confirmation."""

    items: List[str]
    processes: List[ToBeProcess]


@dataclass(slots=True)
class ToBeReviewResult:
    """Outcome returned after confirming a TO-BE summary."""

    items: List[str]
    processes: List[ToBeProcess]
    stakeholder_comment: str


class ToBeDerivationAgent:
    """Generates TO-BE bullet points from the current specification context."""

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
        fallback_processes: Sequence[ToBeProcess],
    ) -> ToBeDraft:
        payload_json = json.dumps(summary_data, ensure_ascii=False, indent=2)
        excerpt = conversation_excerpt.strip()
        if not excerpt:
            excerpt = "(No additional conversation excerpt supplied.)"
        prompt = (
            "You are a senior Business Analyst defining the TO-BE (future "
            "state) for an engagement. Study the structured functional "
            "specification summary and conversation excerpt below. Craft "
            "3-6 concise bullet statements that capture the desired future "
            "capabilities, process improvements, and outcomes. Ensure the "
            "bullets are actionable and written in stakeholder-friendly "
            "language. Respond ONLY with JSON in the shape "
            '{"future_state": [string, ...], "future_processes": ['
            '{"name": string, "happy_path": [string], '
            '"unhappy_path": [string]}, ...]}. Each bullet should stay under '
            "220 characters and processes should outline 3-6 steps for both "
            "happy and unhappy paths when applicable."
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
                    "You translate requirements into clear TO-BE summaries "
                    "that describe the desired future experience."
                ),
            ),
            ChatMessage(role="user", content=prompt),
            ChatMessage(role="user", content=user_message),
        ]
        response = await self._chat_client.complete(messages)
        draft = self._parse_response(response.content)
        if draft is None:
            fallback_process_list = [
                ToBeProcess(
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
                    "Future state details pending stakeholder confirmation."
                ]
            return ToBeDraft(
                items=bullet_items,
                processes=fallback_process_list,
            )
        if not draft.items:
            draft.items = [
                str(entry).strip()
                for entry in fallback_items
                if str(entry).strip()
            ]
        if not draft.items:
            draft.items = [
                "Future state details pending stakeholder confirmation."
            ]
        if not draft.processes:
            draft.processes = [
                ToBeProcess(
                    name=process.name,
                    happy_path=list(process.happy_path),
                    unhappy_path=list(process.unhappy_path),
                )
                for process in fallback_processes
            ]
        return draft

    @staticmethod
    def _parse_response(raw: str) -> ToBeDraft | None:
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
        future_state = data.get("future_state")
        items: List[str] = []
        if isinstance(future_state, list):
            future_state_entries = cast(List[Any], future_state)
        else:
            future_state_entries = []
        items: List[str] = []
        for entry in future_state_entries:
            text_value = str(entry).strip()
            if not text_value:
                continue
            sanitized = text_value.replace("|", "/").replace("\n", " ")
            items.append(sanitized)
        processes: List[ToBeProcess] = []
        processes_raw = data.get("future_processes")
        if isinstance(processes_raw, list):
            for process_entry in cast(List[Any], processes_raw):
                if not isinstance(process_entry, dict):
                    continue
                process_dict = cast(Dict[str, Any], process_entry)
                name = str(process_dict.get("name", "")).strip()
                if not name:
                    continue
                happy_steps = ToBeDerivationAgent._sanitize_steps(
                    process_dict.get("happy_path")
                )
                unhappy_steps = ToBeDerivationAgent._sanitize_steps(
                    process_dict.get("unhappy_path")
                )
                if not happy_steps and not unhappy_steps:
                    happy_steps = [
                        "Future-state steps pending definition."
                    ]
                processes.append(
                    ToBeProcess(
                        name=name.replace("|", "/"),
                        happy_path=happy_steps,
                        unhappy_path=unhappy_steps,
                    )
                )
        return ToBeDraft(items=items, processes=processes)

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


class ConsoleToBeReviewer:
    """Prompts a human stakeholder to confirm or extend the TO-BE summary."""

    async def confirm_to_be(
        self,
        *,
        proposed_items: Sequence[str],
        proposed_processes: Sequence[ToBeProcess],
        spec_text: str,
        question: str,
    ) -> ToBeReviewResult:
        _ = spec_text
        print()  # noqa: T201 - CLI UX spacing
        print(  # noqa: T201
            "BA Agent: Let's align on the desired TO-BE experience."
        )
        print(question)  # noqa: T201
        print("\nProposed TO-BE summary:")  # noqa: T201
        for index, item in enumerate(proposed_items, start=1):
            print(f"  {index}. {item}")  # noqa: T201
        items = [entry for entry in proposed_items if str(entry).strip()]
        processes: List[ToBeProcess] = [
            ToBeProcess(
                name=process.name,
                happy_path=list(process.happy_path),
                unhappy_path=list(process.unhappy_path),
            )
            for process in proposed_processes
        ]
        print("\nTarget TO-BE processes:")  # noqa: T201
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
            addition = input(  # noqa: PLW1514 - intentional CLI input
                "Add another TO-BE detail (leave blank to continue): "
            ).strip()
            if not addition:
                break
            items.append(addition)
        while True:
            new_process_name = input(  # noqa: PLW1514
                "Add a TO-BE process (leave blank to continue): "
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
                step.strip() for step in happy_input.split(";") if step.strip()
            ]
            unhappy_steps = [
                step.strip()
                for step in unhappy_input.split(";")
                if step.strip()
            ]
            processes.append(
                ToBeProcess(
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
        return ToBeReviewResult(
            items=items,
            processes=processes,
            stakeholder_comment=stakeholder_comment,
        )
