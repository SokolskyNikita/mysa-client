"""Optional client for Mysa's legacy AWS IoT MQTT protocol."""

from .auth import AwsCredentials, user_pool_sub
from .client import (
    CommandFailure,
    CommandResult,
    EchoResult,
    MysaMqttClient,
    PublishResult,
)
from .errors import (
    MqttAuthenticationError,
    MqttConnectionError,
    MqttError,
    MqttNotConnectedError,
    MqttProtocolError,
)
from .protocol import (
    DeviceCommandState,
    MessageKind,
    MqttMessage,
    MqttTelemetry,
    encode_thermostat_command,
    parse_message,
    supports_commands,
)

__all__ = [
    "AwsCredentials",
    "CommandFailure",
    "CommandResult",
    "DeviceCommandState",
    "EchoResult",
    "MessageKind",
    "MqttAuthenticationError",
    "MqttConnectionError",
    "MqttError",
    "MqttMessage",
    "MqttNotConnectedError",
    "MqttProtocolError",
    "MqttTelemetry",
    "MysaMqttClient",
    "PublishResult",
    "encode_thermostat_command",
    "parse_message",
    "supports_commands",
    "user_pool_sub",
]
