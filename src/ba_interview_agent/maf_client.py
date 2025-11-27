"""Thin wrappers around Microsoft Agent Framework chat completion clients.

This module centralizes the integration with the Microsoft Agent Framework
(MAF) so the rest of the application can stay framework-agnostic. It attempts
to load the appropriate client implementation at runtime based on the
configured provider. If the import fails, a descriptive error is raised to
guide the user through the required dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Iterable, List

from agent_framework import ChatMessage as MAFChatMessage, Role

from .config import ModelSettings


def _coerce_role(role: str) -> Role:
    try:
        return Role(role)
    except ValueError as exc:
        raise ValueError(
            "Unsupported role for MAF chat message: {role}".format(role=role)
        ) from exc


@dataclass(slots=True)
class ChatMessage:
    """Simple representation of a chat message compatible with this app."""

    role: str
    content: str


class MAFIntegrationError(RuntimeError):
    """Raised when the MAF client cannot be initialized."""


class MAFChatClient:
    """Wrapper that dispatches chat completion calls through MAF clients."""

    def __init__(self, settings: ModelSettings) -> None:
        self._settings = settings
        self._client = self._create_client(settings)

    @staticmethod
    def _create_client(settings: ModelSettings):
        provider = settings.provider.lower()
        try:
            if provider in {"azure-openai", "azure_openai", "azure"}:
                module = import_module("agent_framework.azure")
                client_cls = getattr(module, "AzureOpenAIChatClient")
                return client_cls(
                    api_key=settings.api_key,
                    deployment_name=settings.model,
                    endpoint=settings.endpoint,
                    api_version=settings.api_version,
                )
            if provider in {"openai", "oai"}:
                module = import_module("agent_framework.openai")
                client_cls = getattr(module, "OpenAIChatClient")
                return client_cls(
                    api_key=settings.api_key,
                    model_id=settings.model,
                    base_url=settings.endpoint,
                )
        except ModuleNotFoundError as exc:  # pragma: no cover - MAF runtime
            missing = exc.name or "a required dependency"
            raise MAFIntegrationError(
                "Microsoft Agent Framework dependency '{missing}' is missing. "
                "Reinstall the project dependencies (e.g. `pip install -e .`)."
                .format(missing=missing)
            ) from exc
        raise MAFIntegrationError(
            f"Unsupported MAF provider '{settings.provider}'."
        )

    @staticmethod
    def _merge_consecutive_roles(
        messages: Iterable[ChatMessage],
    ) -> List[ChatMessage]:
        """Combine adjacent messages that share the same role.

        The Microsoft Agent Framework templates expect user/assistant roles to
        alternate. When higher-level code emits multiple messages from the same
        role back-to-back (for example, a transcript followed by an instruction
        for the assistant), we merge their content to preserve intent while
        keeping the required alternation.
        """

        merged: List[ChatMessage] = []
        for message in messages:
            if merged and merged[-1].role == message.role:
                previous = merged[-1]
                previous.content = (
                    f"{previous.content}\n\n{message.content}".strip()
                )
                continue
            merged.append(
                ChatMessage(role=message.role, content=message.content)
            )
        return merged

    async def complete(self, messages: Iterable[ChatMessage]) -> ChatMessage:
        """Execute a chat completion call through the underlying MAF client."""

        merged_messages = self._merge_consecutive_roles(messages)
        payload: List[MAFChatMessage] = [
            MAFChatMessage(role=_coerce_role(msg.role), text=msg.content)
            for msg in merged_messages
        ]
        # Microsoft Agent Framework chat clients expose an async `get_response`
        # method that returns a ChatResponse object with a convenience `.text`
        # property. Using the framework primitives keeps us aligned with the
        # documented API surface.  # noqa: E501
        response = await self._client.get_response(messages=payload)
        return ChatMessage(role="assistant", content=response.text or "")
