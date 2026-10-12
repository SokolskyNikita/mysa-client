"""Small optional event-history interface used by runtime workflows."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol
from uuid import uuid4


class RuntimeHistory(Protocol):
    """The minimal evidence interface consumed by runtime workflows."""

    def command_id(self) -> str:
        """Return an identifier for a command or retry."""

    def command(
        self, command_id: str, device_id: str | None
    ) -> AbstractContextManager[None]:
        """Associate events emitted in this context with a command."""

    def label(self, device_id: str) -> str:
        """Return a safe display label for a device."""

    def record(
        self,
        event: str,
        *,
        device_id: str | None = None,
        noisy: bool = False,
        **fields: Any,
    ) -> None:
        """Record sanitized runtime evidence."""


class NullHistory:
    """No-op history implementation for standalone callers."""

    def command_id(self) -> str:
        """Return an identifier even when command evidence is disabled."""
        return uuid4().hex

    @contextmanager
    def command(self, command_id: str, device_id: str | None) -> Iterator[None]:
        """Provide a no-op command context."""
        yield

    def label(self, device_id: str) -> str:
        """Return a generic label without retaining the device identifier."""
        return "thermostat"

    def record(
        self,
        event: str,
        *,
        device_id: str | None = None,
        noisy: bool = False,
        **fields: Any,
    ) -> None:
        """Discard an event."""


def as_string_mapping(values: Mapping[Any, Any]) -> dict[str, Any]:
    """Convert semantic enum keys to stable string keys for pending intents."""
    return {str(getattr(key, "value", key)): value for key, value in values.items()}
