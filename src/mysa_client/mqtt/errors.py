"""Public, credential-safe errors raised by the optional MQTT client."""


class MqttError(Exception):
    """Base error for MQTT operations."""


class MqttAuthenticationError(MqttError):
    """Authentication failed without exposing tokens or AWS credentials."""


class MqttConnectionError(MqttError):
    """The broker connection or a required subscription failed."""


class MqttNotConnectedError(MqttError):
    """An operation requires an active broker connection."""


class MqttProtocolError(MqttError):
    """The requested operation cannot be represented by the legacy protocol."""
