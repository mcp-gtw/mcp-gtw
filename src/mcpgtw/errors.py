class GatewayError(RuntimeError):
    """Base error raised by the gateway."""


class GatewayConfigurationError(GatewayError):
    """Raised when the gateway is configured with an invalid combination of settings."""


class ChannelOfflineError(GatewayError):
    """Raised when a request cannot be delivered to the channel provider."""


class ChannelReplacedError(GatewayError):
    """Raised when a new provider connection replaces the current one."""


class ProviderMessageError(GatewayError):
    """Raised when a provider message is malformed or rejected, including registration."""


class ProviderRequestError(GatewayError):
    """Raised when a request relayed to the provider fails, times out or is refused."""


class ChannelCapacityError(GatewayError):
    """Raised when a channel or the registry exceeds a configured limit."""
