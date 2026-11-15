"""Classify the library's exceptions into stable, credential-free categories.

``error_category()`` names the kind of failure without formatting the
exception or its arguments, so the result is safe for logs and diagnostics.
``is_outcome_unknown()`` says whether a failed cloud write may still have been
applied. Neither function imports the HomeKit or MQTT packages: their
exception types are recognised only once the application has imported them,
which it must have done before one of them could be raised.
"""

from __future__ import annotations

import sys

from mysa_client.cloud.exceptions import (
    MysaApiError,
    MysaAuthError,
    MysaConnectionError,
    MysaNotFoundError,
    MysaRateLimitError,
)
from mysa_client.cloud.modes import UnknownModeError
from mysa_client.cloud.schedule_workflow import ScheduleWorkflowError
from mysa_client.cloud.setpoints import SetpointError

#: Every value ``error_category()`` can return.
ERROR_CATEGORIES: frozenset[str] = frozenset(
    {
        "none",
        "authentication_failed",
        "rate_limited",
        "not_found",
        "request_rejected",
        "validation_failed",
        "connection_error",
        "request_failed",
        "unexpected_error",
    }
)

_HOMEKIT = "mysa_client.homekit.exceptions"
_MQTT = "mysa_client.mqtt.errors"


def _loaded(module: str, *names: str) -> tuple[type[BaseException], ...]:
    """Exception classes from a module, or none if it was never imported."""
    loaded = sys.modules.get(module)
    if loaded is None:
        return ()
    return tuple(
        found
        for name in names
        if isinstance(found := getattr(loaded, name, None), type)
        and issubclass(found, BaseException)
    )


def error_category(error: BaseException | None) -> str:
    """Name the kind of failure, without formatting the error or its arguments.

    Checks run in this order, so a subclass gets the first matching category:

    - ``"none"``: no error (``None``).
    - ``"authentication_failed"``: ``MysaAuthError`` and its subclasses,
      ``HomeKitAuthenticationError``, ``MqttAuthenticationError``.
    - ``"rate_limited"``: ``MysaRateLimitError``, ``HomeKitBusyError``,
      ``HomeKitMaxTriesError``.
    - ``"not_found"``: ``MysaNotFoundError``.
    - ``"request_rejected"``: ``MysaApiError``, ``HomeKitWriteRejectedError``,
      ``HomeKitUnavailableError``, ``HomeKitMaxPeersError``,
      ``MqttProtocolError``.
    - ``"validation_failed"``: ``SetpointError`` and its subclasses, ``UnknownModeError``,
      ``HomeKitMalformedPinError``.
    - ``"connection_error"``: ``MysaConnectionError``,
      ``HomeKitConnectionError`` (including ``HomeKitNotFoundError``),
      ``MqttConnectionError``, ``MqttNotConnectedError``, and any ``OSError``
      (which includes ``TimeoutError``).
    - ``"request_failed"``: ``HomeKitCharacteristicReadError``,
      ``ScheduleWorkflowError``.
    - ``"unexpected_error"``: anything else, including the base classes
      ``MysaError``, ``HomeKitError`` and ``MqttError``.
    """
    if error is None:
        return "none"
    if isinstance(
        error,
        (
            MysaAuthError,
            *_loaded(_HOMEKIT, "HomeKitAuthenticationError"),
            *_loaded(_MQTT, "MqttAuthenticationError"),
        ),
    ):
        return "authentication_failed"
    if isinstance(
        error,
        (
            MysaRateLimitError,
            *_loaded(_HOMEKIT, "HomeKitBusyError", "HomeKitMaxTriesError"),
        ),
    ):
        return "rate_limited"
    if isinstance(error, MysaNotFoundError):
        return "not_found"
    if isinstance(
        error,
        (
            MysaApiError,
            *_loaded(
                _HOMEKIT,
                "HomeKitWriteRejectedError",
                "HomeKitUnavailableError",
                "HomeKitMaxPeersError",
            ),
            *_loaded(_MQTT, "MqttProtocolError"),
        ),
    ):
        return "request_rejected"
    if isinstance(
        error,
        (
            SetpointError,
            UnknownModeError,
            *_loaded(_HOMEKIT, "HomeKitMalformedPinError"),
        ),
    ):
        return "validation_failed"
    if isinstance(
        error,
        (
            MysaConnectionError,
            OSError,
            *_loaded(_HOMEKIT, "HomeKitConnectionError"),
            *_loaded(_MQTT, "MqttConnectionError", "MqttNotConnectedError"),
        ),
    ):
        return "connection_error"
    if isinstance(
        error,
        (
            ScheduleWorkflowError,
            *_loaded(_HOMEKIT, "HomeKitCharacteristicReadError"),
        ),
    ):
        return "request_failed"
    return "unexpected_error"


def is_outcome_unknown(error: BaseException | None) -> bool:
    """Whether a cloud request that raised this error may still have been applied.

    True for ``MysaConnectionError`` (no answer, a timeout or a server error:
    Mysa may have processed the request) except ``MysaRateLimitError``, which
    Mysa refused before processing it. False for ``None`` and every other
    error, which either reports a definite refusal or happened before anything
    was sent. Only the error itself is examined; pass ``error.__cause__`` to
    judge an error that wraps a library error.

    HomeKit and MQTT errors are always False here. After a HomeKit write has
    started, ``CommandRouter`` treats any failure other than a rejection as an
    unknown outcome on its own.
    """
    return isinstance(error, MysaConnectionError) and not isinstance(
        error, MysaRateLimitError
    )


__all__ = ["ERROR_CATEGORIES", "error_category", "is_outcome_unknown"]
