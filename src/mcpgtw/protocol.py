from __future__ import annotations

from typing import Any, Final

# gateway → provider
HELLO_ACK: Final = "hello.ack"
ACK: Final = "ack"
REQUEST: Final = "request"
CANCEL: Final = "cancel"
RESPONSE: Final = "response"
PONG: Final = "pong"
PROTOCOL_ERROR: Final = "protocol.error"

# provider → gateway
REGISTER: Final = "register"
RESULT: Final = "result"
CALL: Final = "call"
NOTIFY: Final = "notify"
PING: Final = "ping"

# registries a provider may publish
TOOLS: Final = "tools"
RESOURCES: Final = "resources"
RESOURCE_TEMPLATES: Final = "resourceTemplates"
PROMPTS: Final = "prompts"

# methods the gateway asks the provider to run (request)
CALL_TOOL: Final = "tools/call"
READ_RESOURCE: Final = "resources/read"
GET_PROMPT: Final = "prompts/get"
COMPLETE: Final = "completion/complete"
SUBSCRIBE: Final = "resources/subscribe"
UNSUBSCRIBE: Final = "resources/unsubscribe"

# methods the provider asks the gateway to run against the MCP client (call)
CREATE_MESSAGE: Final = "sampling/createMessage"
ELICIT: Final = "elicitation/create"

# one-way notifications the provider emits (notify)
RESOURCE_UPDATED: Final = "notifications/resources/updated"
PROGRESS: Final = "notifications/progress"
MESSAGE: Final = "notifications/message"


def hello_ack(protocol_version: str, channel_id: str) -> dict[str, Any]:
    return {"type": HELLO_ACK, "protocolVersion": protocol_version, "channelId": channel_id}


def ack(registry: str, count: int) -> dict[str, Any]:
    return {"type": ACK, "registry": registry, "count": count}


def request(request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"type": REQUEST, "requestId": request_id, "method": method, "params": params}


def cancel(request_id: str, reason: str) -> dict[str, Any]:
    return {"type": CANCEL, "requestId": request_id, "reason": reason}


def response(request_id: str, *, result: Any = None, error: str | None = None) -> dict[str, Any]:
    if error is not None:
        return {"type": RESPONSE, "requestId": request_id, "error": error}

    return {"type": RESPONSE, "requestId": request_id, "result": result}


def protocol_error(message: str) -> dict[str, Any]:
    return {"type": PROTOCOL_ERROR, "message": message}
