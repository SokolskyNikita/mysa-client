"""error_category() and is_outcome_unknown() across every library exception."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import subprocess
import sys

import pytest

import mysa_client
from mysa_client.cloud import (
    MysaApiError,
    MysaAuthError,
    MysaConnectionError,
    MysaNotFoundError,
    MysaRateLimitError,
)
from mysa_client.homekit.exceptions import (
    HomeKitAuthenticationError,
    HomeKitConnectionError,
    HomeKitWriteRejectedError,
)
from mysa_client.runtime import routing
from mysa_client.runtime.errors import (
    ERROR_CATEGORIES,
    error_category,
    is_outcome_unknown,
)

# Every exception class the library defines, and its category.
EXPECTED = {
    "MysaError": "unexpected_error",
    "MysaConnectionError": "connection_error",
    "MysaRateLimitError": "rate_limited",
    "MysaAuthError": "authentication_failed",
    "MysaInvalidCredentialsError": "authentication_failed",
    "MysaUnsupportedChallengeError": "authentication_failed",
    "MysaNotFoundError": "not_found",
    "MysaApiError": "request_rejected",
    "ScheduleWorkflowError": "request_failed",
    "SetpointError": "validation_failed",
    "SetpointValueError": "validation_failed",
    "SetpointConfigError": "validation_failed",
    "SetpointRangeEmptyError": "validation_failed",
    "SetpointOutOfRangeError": "validation_failed",
    "SetpointLimitOrderError": "validation_failed",
    "SetpointModeError": "validation_failed",
    "SetpointGapTooSmallError": "validation_failed",
    "SetpointGapUnknownError": "validation_failed",
    "UnknownModeError": "validation_failed",
    "HomeKitError": "unexpected_error",
    "HomeKitConnectionError": "connection_error",
    "HomeKitNotFoundError": "connection_error",
    "HomeKitAuthenticationError": "authentication_failed",
    "HomeKitUnavailableError": "request_rejected",
    "HomeKitBusyError": "rate_limited",
    "HomeKitMaxTriesError": "rate_limited",
    "HomeKitMaxPeersError": "request_rejected",
    "HomeKitMalformedPinError": "validation_failed",
    "HomeKitWriteRejectedError": "request_rejected",
    "HomeKitCharacteristicReadError": "request_failed",
    "MqttError": "unexpected_error",
    "MqttAuthenticationError": "authentication_failed",
    "MqttConnectionError": "connection_error",
    "MqttNotConnectedError": "connection_error",
    "MqttProtocolError": "request_rejected",
}


def _library_exceptions() -> dict[str, type[BaseException]]:
    found: dict[str, type[BaseException]] = {}
    for module in pkgutil.walk_packages(mysa_client.__path__, "mysa_client."):
        loaded = importlib.import_module(module.name)
        found.update(
            (name, value)
            for name, value in vars(loaded).items()
            if inspect.isclass(value)
            and issubclass(value, BaseException)
            and value.__module__.startswith("mysa_client.")
        )
    return found


def _instance(cls: type[BaseException]) -> BaseException:
    # Constructors differ; classification only needs the type.
    return cls.__new__(cls)


def _mysa_local_0_9(error: BaseException | None) -> str:
    """``event_history.error_category`` from Mysa Local 0.9, verbatim logic."""
    if isinstance(error, MysaAuthError | HomeKitAuthenticationError):
        return "authentication_failed"
    if isinstance(error, MysaRateLimitError):
        return "rate_limited"
    if isinstance(error, MysaNotFoundError):
        return "not_found"
    if isinstance(error, MysaApiError | HomeKitWriteRejectedError):
        return "request_rejected"
    if isinstance(error, MysaConnectionError | HomeKitConnectionError | OSError):
        return "connection_error"
    return "unexpected_error"


def test_every_library_exception_has_a_category() -> None:
    """New exception classes must be added to the table (and maybe the rules)."""
    exceptions = _library_exceptions()
    assert set(exceptions) == set(EXPECTED)
    for name, cls in exceptions.items():
        assert error_category(_instance(cls)) == EXPECTED[name], name
    assert set(EXPECTED.values()) | {"none"} == ERROR_CATEGORIES


def test_matches_mysa_local_wherever_it_had_a_rule() -> None:
    """Types Mysa Local classified keep their category; the rest were unexpected."""
    samples: list[BaseException] = [
        _instance(cls) for cls in _library_exceptions().values()
    ]
    samples += [TimeoutError(), OSError(), ConnectionResetError(), ValueError()]
    for error in samples:
        old, new = _mysa_local_0_9(error), error_category(error)
        # Only types Mysa Local left as "unexpected_error" gain a category.
        assert old in {new, "unexpected_error"}, type(error).__name__
    assert error_category(TimeoutError()) == "connection_error"
    assert error_category(ValueError()) == "unexpected_error"
    assert error_category(None) == "none"


def test_outcome_unknown_only_for_cloud_connection_errors() -> None:
    """A rate limit was refused before processing; everything else is definite."""
    exceptions = _library_exceptions()
    unknown = {
        name for name, cls in exceptions.items() if is_outcome_unknown(_instance(cls))
    }
    assert unknown == {"MysaConnectionError"}
    assert not is_outcome_unknown(None)
    assert not is_outcome_unknown(TimeoutError())
    wrapped = RuntimeError("translated")
    wrapped.__cause__ = MysaConnectionError("lost")
    assert not is_outcome_unknown(wrapped)
    assert is_outcome_unknown(wrapped.__cause__)


def test_router_uses_them_by_default_and_callers_can_override() -> None:
    """CommandRouter's defaults are the library classifiers."""

    async def read(_cloud: object) -> object:
        return None

    default = routing.CommandRouter(
        device_id="d", name="n", local=None, cloud_getter=lambda: None, cloud_read=read
    )
    assert default._error_category is error_category
    assert default._is_ambiguous_cloud_error is is_outcome_unknown

    def mine(_error: BaseException | None) -> str:
        return "mine"

    def never(_error: BaseException) -> bool:
        return False

    custom = routing.CommandRouter(
        device_id="d",
        name="n",
        local=None,
        cloud_getter=lambda: None,
        cloud_read=read,
        error_category=mine,
        is_ambiguous_cloud_error=never,
    )
    assert custom._error_category is mine
    assert custom._is_ambiguous_cloud_error is never


@pytest.mark.parametrize("optional", ["homekit", "mqtt"])
def test_classifies_without_importing_optional_packages(optional: str) -> None:
    """A cloud-only install can classify; HomeKit and MQTT stay unimported."""
    script = f"""
import sys
from mysa_client.runtime.errors import error_category, is_outcome_unknown
from mysa_client.cloud import MysaRateLimitError
assert error_category(MysaRateLimitError("x")) == "rate_limited"
assert error_category(RuntimeError()) == "unexpected_error"
assert not is_outcome_unknown(MysaRateLimitError("x"))
assert "mysa_client.{optional}" not in sys.modules
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and import check
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
