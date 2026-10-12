"""Optional cross-path command routing and late-write reconciliation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import inspect
import time
from typing import Any, Generic, Protocol, TypeVar

from .events import NullHistory, RuntimeHistory, as_string_mapping

Key = TypeVar("Key")
Value = TypeVar("Value")
Cloud = TypeVar("Cloud")
State = TypeVar("State")


class LocalTransport(Protocol[Key]):
    """Semantic local transport operations needed by the router."""

    @property
    def available(self) -> bool:
        """Whether the local connection can currently serve commands."""

    @property
    def ready(self) -> bool:
        """Whether required device characteristics are available."""

    @property
    def values(self) -> Mapping[Key, Any]:
        """Current semantic values."""

    def supports(self, field: Key) -> bool:
        """Return whether this field can be written locally."""

    def start_write(self, values: Mapping[Key, Any]) -> asyncio.Task[None]:
        """Start a write that remains alive if its caller times out."""

    async def async_read(self, fields: Iterable[Key]) -> Mapping[Key, Any]:
        """Read current semantic values."""

    def mark_available(self) -> None:
        """Mark the local path healthy after a successful read-back."""

    def mark_unavailable(self, error: str) -> None:
        """Mark the local path unavailable after an uncertain write."""

    async def async_stop(self) -> None:
        """Close the local session."""


class RouteOutcome(StrEnum):
    """Typed result of a command-routing decision."""

    LOCAL_ACCEPTED = "local_accepted"
    LOCAL_CONFIRMED = "local_confirmed"
    CLOUD_REPORTED = "cloud_reported"
    CLOUD_WRITTEN = "cloud_written"
    LOCAL_REJECTED = "local_rejected"
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Outcome plus an optional transport error for adapter translation."""

    outcome: RouteOutcome
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class RouteRequest(Generic[Key, Value, Cloud, State]):
    """One command's local write, cloud fallback, and supersession intent."""

    local_values: Mapping[Key, Value] | None
    cloud_write: Callable[[Cloud], Awaitable[None]] | None
    cloud_applied: Callable[[State], bool] | None
    intent: Mapping[str, Any] | None = None


class CommandRouter(Generic[Key, Value, Cloud, State]):
    """Serialize commands and repair stale outcomes after delayed local writes.

    The router knows only semantic values and injected callbacks. It does not
    import a HomeKit SDK, an application framework, or a particular cloud API.
    """

    def __init__(
        self,
        *,
        device_id: str,
        name: str,
        local: LocalTransport[Key] | None,
        cloud_getter: Callable[[], Cloud | None],
        cloud_read: Callable[[Cloud], Awaitable[State]],
        history: RuntimeHistory | None = None,
        on_change: Callable[[], None] | None = None,
        on_reconcile_ready: Callable[[], None] | None = None,
        on_reconcile_error: Callable[[BaseException], None] | None = None,
        write_timeout: float = 8.0,
        is_local_error: Callable[[BaseException], bool] | None = None,
        is_local_rejection: Callable[[BaseException], bool] | None = None,
        is_cloud_confirmation_error: Callable[[BaseException], bool] | None = None,
        is_ambiguous_cloud_error: Callable[[BaseException], bool] | None = None,
        is_reconcile_error: Callable[[BaseException], bool] | None = None,
        error_category: Callable[[BaseException | None], str] | None = None,
    ) -> None:
        """Prepare a router without starting network activity."""
        self.device_id = device_id
        self.name = name
        self.local = local
        self._cloud_getter = cloud_getter
        self._cloud_read = cloud_read
        self.history = history if history is not None else NullHistory()
        self._on_change = on_change or (lambda: None)
        self._on_reconcile_ready = on_reconcile_ready or (lambda: None)
        self._on_reconcile_error = on_reconcile_error or (lambda _error: None)
        self._write_timeout = write_timeout
        self._is_local_error = is_local_error or (lambda _error: False)
        self._is_local_rejection = is_local_rejection or (lambda _error: False)
        self._is_cloud_confirmation_error = is_cloud_confirmation_error or (
            lambda _error: False
        )
        self._is_ambiguous_cloud_error = is_ambiguous_cloud_error or (
            lambda _error: False
        )
        self._is_reconcile_error = is_reconcile_error or (lambda _error: False)
        self._error_category = error_category or (
            lambda error: "none" if error is None else "unexpected_error"
        )
        self._write_lock = asyncio.Lock()
        self._pending_local: asyncio.Task[None] | None = None
        self._pending_values: dict[str, Any] = {}
        self._late_local: asyncio.Task[None] | None = None
        self._reconcile: dict[str, Callable[[Cloud], Awaitable[None]]] = {}
        self._reconcile_ids: dict[str, str] = {}
        self._stopped = False

    @property
    def cloud(self) -> Cloud | None:
        """Return the currently available cloud transport."""
        return self._cloud_getter()

    @property
    def local_ok(self) -> bool:
        """Whether the local path is ready and no newer intent needs repair."""
        return (
            self.local is not None
            and self.local.available
            and self.local.ready
            and not self._reconcile
        )

    @property
    def pending_local(self) -> asyncio.Task[None] | None:
        """The local request still capable of changing device state."""
        return self._pending_local

    @property
    def pending_values(self) -> Mapping[str, Any]:
        """Values carried by the outstanding local request."""
        return self._pending_values

    @property
    def reconcile(self) -> Mapping[str, Callable[[Cloud], Awaitable[None]]]:
        """Latest cloud writes needed after an outstanding local request."""
        return self._reconcile

    @property
    def reconcile_ids(self) -> Mapping[str, str]:
        """Correlation IDs for pending reconciliation writes."""
        return self._reconcile_ids

    def stop(self) -> None:
        """Prevent new commands and background reconciliation."""
        self._stopped = True

    async def async_stop(self) -> None:
        """Close the local session before cancelling an outstanding write."""
        self.stop()
        async with self._write_lock:
            if self.local is not None:
                await self.local.async_stop()
            if self._pending_local is not None and not self._pending_local.done():
                self._pending_local.cancel()
                await asyncio.gather(self._pending_local, return_exceptions=True)
            await self._async_reconcile_locked(allow_stopped=True)

    async def async_route(
        self,
        *,
        local_values: Mapping[Key, Value] | None,
        cloud_write: Callable[[Cloud], Awaitable[None]] | None,
        cloud_applied: Callable[[State], bool] | None,
        intent: Mapping[str, Any] | None = None,
    ) -> RouteResult:
        """Route a fully prepared command through local then cloud paths."""
        request = RouteRequest(local_values, cloud_write, cloud_applied, intent)
        return await self.async_route_prepared(lambda: request)

    async def async_route_prepared(
        self,
        prepare: Callable[
            [],
            RouteRequest[Key, Value, Cloud, State]
            | Awaitable[RouteRequest[Key, Value, Cloud, State]],
        ],
    ) -> RouteResult:
        """Prepare dependent values under the same lock that serializes writes."""
        command_id = self.history.command_id()
        with self.history.command(command_id, self.device_id):
            async with self._write_lock:
                if self._stopped:
                    return RouteResult(RouteOutcome.STOPPED)
                prepared = prepare()
                request = await prepared if inspect.isawaitable(prepared) else prepared
                try:
                    result = await self._async_route_locked(request)
                except asyncio.CancelledError:
                    if self._pending_local is not None:
                        self.history.record(
                            "command",
                            operation="local_write",
                            transport="homekit",
                            status="ambiguous",
                            error="cancelled",
                        )
                    self._remember_intent(request)
                    raise
                except Exception as error:
                    if self._is_ambiguous_cloud_error(error):
                        self._remember_intent(request)
                    raise
                else:
                    self._remember_intent(request)
                    return result

    async def async_reconcile(self, *, allow_stopped: bool = False) -> None:
        """Reassert successful newer cloud commands after a local write settles."""
        async with self._write_lock:
            await self._async_reconcile_locked(allow_stopped=allow_stopped)

    def schedule_reconciliation(self) -> None:
        """Ask the host adapter to schedule reconciliation when ready."""
        if (
            self._stopped
            or self._pending_local is None
            or not self._pending_local.done()
        ):
            return
        self._on_reconcile_ready()

    async def _async_route_locked(
        self, request: RouteRequest[Key, Value, Cloud, State]
    ) -> RouteResult:
        """Try local I/O, read back uncertainty, then use cloud if needed."""
        local_values = request.local_values
        cloud_write = request.cloud_write
        cloud_applied = request.cloud_applied
        outcome_unknown = False
        local = self.local

        if (
            local_values is not None
            and local is not None
            and self.local_ok
            and self._pending_local is None
            and all(local.supports(key) for key in local_values)
        ):
            values = as_string_mapping(local_values)
            self.history.record(
                "command",
                operation="local_write",
                transport="homekit",
                status="submitted",
                requested=values,
                baseline={
                    str(getattr(key, "value", key)): {
                        "value": local.values.get(key),
                        "source": "homekit",
                        "observed_at": None,
                    }
                    for key in local_values
                },
            )
            started = time.monotonic()
            task = local.start_write(local_values)
            self._pending_local = task
            pending = request.intent or values
            self._pending_values = dict(as_string_mapping(pending))
            task.add_done_callback(self._local_write_done)
            done, _ = await asyncio.wait({task}, timeout=self._write_timeout)
            if task in done:
                self._pending_local = None
                self._pending_values.clear()
                error = task.exception()
                duration = round((time.monotonic() - started) * 1000, 1)
                if error is None:
                    self.history.record(
                        "command",
                        operation="local_write",
                        transport="homekit",
                        status="accepted",
                        duration_ms=duration,
                        evidence="none",
                    )
                    self._on_change()
                    return RouteResult(RouteOutcome.LOCAL_ACCEPTED)
                self.history.record(
                    "command",
                    operation="local_write",
                    transport="homekit",
                    status="failed" if self._is_local_rejection(error) else "ambiguous",
                    error=self._error_category(error),
                    duration_ms=duration,
                )
                if self._is_local_rejection(error):
                    return RouteResult(RouteOutcome.LOCAL_REJECTED, error)
                local.mark_unavailable(str(error) or type(error).__name__)
            else:
                outcome_unknown = True
                self._late_local = task
                self.history.record(
                    "command",
                    operation="local_write",
                    transport="homekit",
                    status="ambiguous",
                    error="connection_error",
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
                local.mark_unavailable("write timed out")
                try:
                    observed = await local.async_read(tuple(local_values))
                except Exception as error:
                    if not self._is_local_error(error):
                        raise
                    observed = {}
                if observed and all(
                    observed.get(key) == value for key, value in local_values.items()
                ):
                    self.history.record(
                        "command",
                        operation="local_write",
                        transport="homekit",
                        status="confirmed",
                        evidence="device_report",
                        observations={
                            str(getattr(key, "value", key)): {
                                "value": value,
                                "source": "homekit",
                                "observed_at": None,
                                "fetched_at": self._utcnow(),
                            }
                            for key, value in observed.items()
                        },
                    )
                    local.mark_available()
                    self._on_change()
                    return RouteResult(RouteOutcome.LOCAL_CONFIRMED)

        cloud = self.cloud
        if cloud_write is None or cloud is None:
            return RouteResult(RouteOutcome.UNAVAILABLE)

        if outcome_unknown and cloud_applied is not None:
            try:
                state = await self._cloud_read(cloud)
            except Exception as error:
                if not self._is_cloud_confirmation_error(error):
                    raise
            else:
                if cloud_applied(state):
                    observations = getattr(state, "observations", {})
                    self.history.record(
                        "readback",
                        operation="local_write",
                        transport="cloud",
                        status="reported",
                        observations=observations,
                        evidence="none",
                    )
                    return RouteResult(RouteOutcome.CLOUD_REPORTED)

        self.history.record(
            "route",
            transport="cloud",
            status="fallback" if local_values is not None else "write",
        )
        await cloud_write(cloud)
        return RouteResult(RouteOutcome.CLOUD_WRITTEN)

    def _remember_intent(self, request: RouteRequest[Key, Value, Cloud, State]) -> None:
        """Keep later cloud intent while an older local write can still land."""
        if (
            self._pending_local is None
            or request.cloud_write is None
            or (request.intent is None and request.local_values is None)
        ):
            return
        intent = request.intent
        if intent is None:
            intent = as_string_mapping(request.local_values or {})
        values = as_string_mapping(intent)
        for key, value in values.items():
            self._reconcile.pop(key, None)
            self._reconcile_ids.pop(key, None)
            if key not in self._pending_values or value != self._pending_values[key]:
                self._reconcile[key] = request.cloud_write
                self._reconcile_ids[key] = self.history.command_id()
        self._on_change()

    async def _async_reconcile_locked(self, *, allow_stopped: bool = False) -> None:
        if (
            (self._stopped and not allow_stopped)
            or self._pending_local is None
            or not self._pending_local.done()
        ):
            return
        if self._reconcile:
            cloud = self.cloud
            if cloud is None:
                return
            assert self.local is not None
            self.local.mark_unavailable("confirming newer cloud commands")
            writes: list[Callable[[Cloud], Awaitable[None]]] = []
            for write in self._reconcile.values():
                if not any(write is pending for pending in writes):
                    writes.append(write)
            for write in writes:
                correlation_id = next(
                    self._reconcile_ids[key]
                    for key, pending in self._reconcile.items()
                    if pending is write
                )
                with self.history.command(correlation_id, self.device_id):
                    self.history.record(
                        "retry", operation="reconcile", transport="cloud"
                    )
                    try:
                        await write(cloud)
                    except Exception as error:
                        if not self._is_reconcile_error(error):
                            raise
                        self._on_reconcile_error(error)
                        return
                for key, pending in list(self._reconcile.items()):
                    if pending is write:
                        del self._reconcile[key]
                        self._reconcile_ids.pop(key, None)
        self._pending_local = None
        self._pending_values.clear()
        self._on_change()

    def _local_write_done(self, task: asyncio.Task[None]) -> None:
        """Capture late responses and let the host schedule reconciliation."""
        if not task.cancelled():
            error = task.exception()
            if task is self._late_local:
                self.history.record(
                    "request",
                    operation="local_write",
                    transport="homekit",
                    status="failed" if error else "accepted",
                    detail="late_response",
                    error=self._error_category(error),
                )
        if task is self._late_local:
            self._late_local = None
        self.schedule_reconciliation()

    @staticmethod
    def _utcnow() -> datetime:
        """Return a timezone-aware timestamp without depending on an app clock."""
        return datetime.now(UTC)
