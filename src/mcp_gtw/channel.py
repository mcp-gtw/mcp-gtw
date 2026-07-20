from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import mcp.types as types
from jsonschema import Draft202012Validator, ValidationError
from mcp.server.lowlevel.helper_types import ReadResourceContents

from mcp_gtw import protocol
from mcp_gtw.compiled_tool import CompiledTool
from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import (
    ChannelOfflineError,
    ChannelReplacedError,
    ProviderMessageError,
    ProviderRequestError,
)
from mcp_gtw.json_websocket import JsonWebSocket
from mcp_gtw.pending_request import PendingRequest

logger = logging.getLogger(__name__)

_LIST_CHANGED = {
    protocol.TOOLS: ("send_tool_list_changed",),
    protocol.RESOURCES: ("send_resource_list_changed",),
    protocol.RESOURCE_TEMPLATES: ("send_resource_list_changed",),
    protocol.PROMPTS: ("send_prompt_list_changed",),
}
_ALL_LIST_CHANGED = (
    "send_tool_list_changed",
    "send_resource_list_changed",
    "send_prompt_list_changed",
)
_LOG_LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")


@dataclass(slots=True)
class Channel:
    """Routes dynamic MCP capabilities between MCP clients and a single provider."""

    channel_id: str
    mcp_token: str
    provider_token: str
    settings: GatewaySettings
    metadata: dict[str, Any] = field(default_factory=dict)

    _websocket: JsonWebSocket | None = field(default=None, init=False)
    _provider_id: str | None = field(default=None, init=False)
    _provider_name: str | None = field(default=None, init=False)
    _tools: dict[str, CompiledTool] = field(default_factory=dict, init=False)
    _resources: dict[str, types.Resource] = field(default_factory=dict, init=False)
    _resource_templates: dict[str, types.ResourceTemplate] = field(default_factory=dict, init=False)
    _prompts: dict[str, types.Prompt] = field(default_factory=dict, init=False)
    _pending: dict[str, PendingRequest] = field(default_factory=dict, init=False)
    _subscriptions: dict[str, set[int]] = field(default_factory=dict, init=False)
    _log_levels: dict[int, str] = field(default_factory=dict, init=False)
    _mcp_sessions: dict[int, Any] = field(default_factory=dict, init=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _state_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def provider_connected(self) -> bool:
        return self._websocket is not None

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    def list_tools(self) -> list[types.Tool]:
        return [compiled.definition for compiled in self._tools.values()]

    def list_resources(self) -> list[types.Resource]:
        return list(self._resources.values())

    def list_resource_templates(self) -> list[types.ResourceTemplate]:
        return list(self._resource_templates.values())

    def list_prompts(self) -> list[types.Prompt]:
        return list(self._prompts.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            "channelId": self.channel_id,
            "providerConnected": self.provider_connected,
            "providerId": self._provider_id,
            "providerName": self._provider_name,
            "toolCount": self.tool_count,
            "resourceCount": len(self._resources),
            "tools": list(self._tools),
            "pendingCalls": len(self._pending),
        }

    async def attach(
        self,
        websocket: JsonWebSocket,
        *,
        provider_id: str,
        provider_name: str | None,
    ) -> None:
        async with self._state_lock:
            previous = self._websocket
            self._websocket = websocket
            self._provider_id = provider_id
            self._provider_name = provider_name
            replaced = previous is not None and previous is not websocket

            if replaced:
                self._fail_pending(
                    ChannelReplacedError("The channel provider was replaced by a new connection")
                )
                self._clear_registries()

        if replaced:
            with contextlib.suppress(Exception):
                await previous.close(code=1012, reason="Channel provider replaced")

            await self._notify(_ALL_LIST_CHANGED)

        logger.info(
            "Provider attached: channel=%s provider=%s name=%s",
            self.channel_id,
            provider_id,
            provider_name,
        )

    async def detach(self, websocket: JsonWebSocket) -> bool:
        async with self._state_lock:
            if websocket is not self._websocket:
                return False

            self._websocket = None
            self._provider_id = None
            self._provider_name = None
            self._clear_registries()
            self._fail_pending(ChannelOfflineError("The channel provider disconnected"))

        logger.info("Provider detached: channel=%s", self.channel_id)
        await self._notify(_ALL_LIST_CHANGED)
        return True

    def _clear_registries(self) -> None:
        self._tools = {}
        self._resources = {}
        self._resource_templates = {}
        self._prompts = {}

    def remember_mcp_session(self, session: Any) -> None:
        key = id(session)
        self._mcp_sessions.pop(key, None)
        self._mcp_sessions[key] = session  # re-insert to move it to the most-recently-used position

        maximum_sessions = self.settings.maximum_mcp_sessions_per_channel

        while maximum_sessions is not None and len(self._mcp_sessions) > maximum_sessions:
            oldest = next(iter(self._mcp_sessions))
            del self._mcp_sessions[oldest]
            self._forget_session(oldest)

    def _drop_sessions(self, keys: list[int]) -> None:
        for key in keys:
            self._mcp_sessions.pop(key, None)
            self._forget_session(key)

    async def _notify(self, methods: tuple[str, ...]) -> None:
        dead: list[int] = []

        for key, session in list(self._mcp_sessions.items()):
            try:
                for method in methods:
                    await getattr(session, method)()
            except Exception:
                logger.debug("Dropping inactive MCP session", exc_info=True)
                dead.append(key)

        self._drop_sessions(dead)

    async def register(self, registry: str, items: list[dict[str, Any]]) -> None:
        if registry == protocol.TOOLS:
            compiled = self._compile_tools(items)

            async with self._state_lock:
                self._tools = compiled
        elif registry == protocol.RESOURCES:
            resources = self._compile_named(items, types.Resource, "resource", lambda r: str(r.uri))

            async with self._state_lock:
                self._resources = resources
        elif registry == protocol.RESOURCE_TEMPLATES:
            templates = self._compile_named(
                items, types.ResourceTemplate, "resource template", lambda t: t.uriTemplate
            )

            async with self._state_lock:
                self._resource_templates = templates
        elif registry == protocol.PROMPTS:
            prompts = self._compile_named(items, types.Prompt, "prompt", lambda p: p.name)

            async with self._state_lock:
                self._prompts = prompts
        else:
            raise ProviderMessageError(f"Unknown registry: {registry!r}")

        logger.info("Channel %s registered %d %s", self.channel_id, len(items), registry)
        await self._notify(_LIST_CHANGED[registry])

    def _guard_size(self, item: dict[str, Any], label: str) -> None:
        maximum = self.settings.maximum_tool_definition_bytes

        if maximum is None:
            return

        encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode()

        if len(encoded) > maximum:
            raise ProviderMessageError(f"{label} exceeds the configured maximum size")

    def _guard_count(self, items: list[dict[str, Any]], label: str) -> None:
        maximum = self.settings.maximum_tools

        if maximum is not None and len(items) > maximum:
            raise ProviderMessageError(
                f"Too many {label}: received {len(items)}, maximum is {maximum}"
            )

    def _compile_tools(self, raw_tools: list[dict[str, Any]]) -> dict[str, CompiledTool]:
        self._guard_count(raw_tools, "tools")
        compiled: dict[str, CompiledTool] = {}

        for raw_tool in raw_tools:
            self._guard_size(raw_tool, "Tool definition")

            try:
                tool = types.Tool.model_validate(raw_tool)
            except Exception as exc:
                raise ProviderMessageError(f"Invalid MCP tool definition: {exc}") from exc

            if tool.name in compiled:
                raise ProviderMessageError(f"Duplicate tool name: {tool.name}")

            try:
                Draft202012Validator.check_schema(tool.inputSchema)

                if tool.outputSchema is not None:
                    Draft202012Validator.check_schema(tool.outputSchema)
            except Exception as exc:
                raise ProviderMessageError(
                    f"Invalid JSON Schema for tool '{tool.name}': {exc}"
                ) from exc

            compiled[tool.name] = CompiledTool(
                definition=tool,
                input_validator=Draft202012Validator(tool.inputSchema),
                output_validator=(
                    Draft202012Validator(tool.outputSchema)
                    if tool.outputSchema is not None
                    else None
                ),
            )

        return compiled

    def _compile_named(
        self,
        raw: list[dict[str, Any]],
        model: type[Any],
        label: str,
        key: Callable[[Any], str],
    ) -> dict[str, Any]:
        self._guard_count(raw, f"{label}s")
        compiled: dict[str, Any] = {}

        for item in raw:
            self._guard_size(item, f"{label.capitalize()} definition")

            try:
                entry = model.model_validate(item)
            except Exception as exc:
                raise ProviderMessageError(f"Invalid MCP {label}: {exc}") from exc

            identifier = key(entry)

            if identifier in compiled:
                raise ProviderMessageError(f"Duplicate {label}: {identifier}")

            compiled[identifier] = entry

        return compiled

    async def execute_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        session: Any = None,
        progress_token: str | int | None = None,
    ) -> types.CallToolResult:
        tool = self._tools.get(name)

        if tool is None:
            return self.error_result(f"Unknown tool: {name}")

        try:
            tool.input_validator.validate(arguments)
        except ValidationError as exc:
            return self.error_result(f"Input validation error for '{name}': {exc.message}")

        try:
            raw = await self._call_provider(
                protocol.CALL_TOOL,
                {"name": name, "arguments": arguments},
                session=session,
                progress_token=progress_token,
            )
        except ProviderRequestError as exc:
            return self.error_result(str(exc))

        try:
            result = self.normalize_result(raw)
            return self.validate_output(tool, result)
        except Exception as exc:
            return self.error_result(f"Invalid provider result: {exc}")

    async def read_resource(
        self, uri: str, *, session: Any = None, progress_token: str | int | None = None
    ) -> list[ReadResourceContents]:
        raw = await self._call_provider(
            protocol.READ_RESOURCE, {"uri": uri}, session=session, progress_token=progress_token
        )
        contents = raw.get("contents") if isinstance(raw, dict) else None

        if not isinstance(contents, list):
            raise ProviderRequestError("Provider returned an invalid resource result")

        return [_read_content(item) for item in contents]

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None,
        *,
        session: Any = None,
        progress_token: str | int | None = None,
    ) -> types.GetPromptResult:
        raw = await self._call_provider(
            protocol.GET_PROMPT,
            {"name": name, "arguments": arguments or {}},
            session=session,
            progress_token=progress_token,
        )

        try:
            return types.GetPromptResult.model_validate(raw)
        except Exception as exc:
            raise ProviderRequestError(
                f"Provider returned an invalid prompt result: {exc}"
            ) from exc

    async def complete(
        self, ref: dict[str, Any], argument: dict[str, Any], context: dict[str, Any] | None
    ) -> types.Completion:
        raw = await self._call_provider(
            protocol.COMPLETE, {"ref": ref, "argument": argument, "context": context}
        )

        try:
            return types.Completion.model_validate(raw)
        except Exception as exc:
            raise ProviderRequestError(
                f"Provider returned an invalid completion result: {exc}"
            ) from exc

    async def subscribe(self, uri: str, session: Any) -> None:
        maximum_subscriptions = self.settings.maximum_subscriptions_per_channel

        if (
            maximum_subscriptions is not None
            and uri not in self._subscriptions
            and len(self._subscriptions) >= maximum_subscriptions
        ):
            raise ProviderRequestError("Too many resource subscriptions for this channel")

        subscribers = self._subscriptions.setdefault(uri, set())
        subscribers.add(id(session))
        subscribed = False

        try:
            await self._call_provider(protocol.SUBSCRIBE, {"uri": uri})
            subscribed = True
        finally:
            if not subscribed:
                subscribers.discard(id(session))

                if not subscribers and self._subscriptions.get(uri) is subscribers:
                    del self._subscriptions[uri]

    async def unsubscribe(self, uri: str, session: Any) -> None:
        await self._call_provider(protocol.UNSUBSCRIBE, {"uri": uri})
        subscribers = self._subscriptions.get(uri)

        if subscribers is not None:
            subscribers.discard(id(session))

            if not subscribers:
                del self._subscriptions[uri]

    async def resync_subscriptions(self) -> None:
        for uri in list(self._subscriptions):
            await self.send_to_provider(
                protocol.request(uuid.uuid4().hex, protocol.SUBSCRIBE, {"uri": uri})
            )

    def call_timeout_seconds(self, method: str, params: dict[str, Any]) -> float | None:
        return self.settings.tool_call_timeout_seconds

    async def _call_provider(
        self,
        method: str,
        params: dict[str, Any],
        *,
        session: Any = None,
        progress_token: str | int | None = None,
    ) -> Any:
        if self._websocket is None:
            raise ProviderRequestError("The channel provider is offline")

        maximum_pending = self.settings.maximum_pending_calls_per_channel

        if maximum_pending is not None and len(self._pending) >= maximum_pending:
            raise ProviderRequestError("Too many pending calls for this channel")

        timeout = self.call_timeout_seconds(method, params)
        request_id = uuid.uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = PendingRequest(
            future=future, session=session, progress_token=progress_token
        )

        try:
            await self.send_to_provider(protocol.request(request_id, method, params))
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            await self._best_effort_cancel(request_id, "timeout")
            raise ProviderRequestError(f"Request timed out after {timeout:g} seconds") from None
        except (ChannelOfflineError, ChannelReplacedError) as exc:
            raise ProviderRequestError(str(exc)) from exc
        except asyncio.CancelledError:
            await self._best_effort_cancel(request_id, "mcp_client_cancelled")
            raise
        finally:
            self._pending.pop(request_id, None)

    async def send_to_provider(self, message: dict[str, Any]) -> None:
        websocket = self._websocket

        if websocket is None:
            raise ChannelOfflineError("No channel provider is connected")

        async with self._send_lock:
            if websocket is not self._websocket:
                raise ChannelOfflineError("The channel provider connection changed")

            try:
                await websocket.send_json(message)
            except Exception as exc:
                raise ChannelOfflineError("Failed to send to the channel provider") from exc

    async def handle_provider_message(
        self, websocket: JsonWebSocket, message: dict[str, Any]
    ) -> None:
        if websocket is not self._websocket:
            raise ChannelOfflineError("The channel provider connection changed")

        message_type = message.get("type")

        if message_type == protocol.REGISTER:
            registry = message.get("registry")
            items = message.get("items")

            if (
                not isinstance(registry, str)
                or not isinstance(items, list)
                or not all(isinstance(item, dict) for item in items)
            ):
                raise ProviderMessageError("'register' needs a registry and an array of objects")

            await self.register(registry, items)
            await self.send_to_provider(protocol.ack(registry, len(items)))
            return

        if message_type == protocol.RESULT:
            self.handle_result(message)
            return

        if message_type == protocol.CALL:
            await self.handle_provider_call(message)
            return

        if message_type == protocol.NOTIFY:
            await self.handle_provider_notification(message)
            return

        if message_type == protocol.PING:
            await self.send_to_provider({"type": protocol.PONG})
            return

        raise ProviderMessageError(f"Unsupported provider message type: {message_type!r}")

    def handle_result(self, message: dict[str, Any]) -> None:
        request_id = message.get("requestId")

        if not isinstance(request_id, str):
            raise ProviderMessageError("'result' requires a string requestId")

        pending = self._pending.get(request_id)

        if pending is None or pending.future.done():
            logger.debug("Ignoring late or unknown result: %s", request_id)
            return

        error = message.get("error")

        if error is not None:
            pending.future.set_exception(ProviderRequestError(str(error)))
            return

        pending.future.set_result(message.get("result"))

    async def handle_provider_call(self, message: dict[str, Any]) -> None:
        request_id = message.get("requestId")
        method = message.get("method")
        params = message.get("params")

        if not isinstance(request_id, str):
            raise ProviderMessageError("'call' requires a string requestId")

        if not isinstance(params, dict):
            raise ProviderMessageError("'call' requires an object params")

        session = self._call_target(message.get("originatingRequestId"))

        if session is None:
            await self.send_to_provider(
                protocol.response(request_id, error="No MCP client is connected")
            )
            return

        try:
            result = await self._run_client_call(session, method, params)
            await self.send_to_provider(protocol.response(request_id, result=result))
        except Exception as exc:
            await self.send_to_provider(protocol.response(request_id, error=str(exc)))

    def _call_target(self, originating_request_id: Any) -> Any | None:
        if isinstance(originating_request_id, str):
            pending = self._pending.get(originating_request_id)

            if pending is not None and pending.session is not None:
                return pending.session

        return self._latest_session()

    async def _run_client_call(self, session: Any, method: str, params: dict[str, Any]) -> Any:
        if method == protocol.CREATE_MESSAGE:
            result = await session.create_message(**_sampling_kwargs(params))
        elif method == protocol.ELICIT:
            result = await session.elicit(
                message=params["message"], requestedSchema=params["requestedSchema"]
            )
        else:
            raise ProviderRequestError(f"Unsupported client call: {method!r}")

        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _latest_session(self) -> Any | None:
        for key in reversed(self._mcp_sessions):
            return self._mcp_sessions[key]

        return None

    async def handle_provider_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")

        if not isinstance(params, dict):
            raise ProviderMessageError("'notify' requires an object params")

        if method == protocol.RESOURCE_UPDATED:
            uri = params.get("uri")

            if not isinstance(uri, str):
                raise ProviderMessageError("'resources/updated' requires a string uri")

            await self._notify_resource_updated(uri)
        elif method == protocol.MESSAGE:
            if params.get("level") not in _LOG_LEVELS:
                raise ProviderMessageError(f"Invalid log level: {params.get('level')!r}")

            await self._relay_log(params)
        elif method == protocol.PROGRESS:
            request_id = params.get("requestId")
            progress = params.get("progress")

            if not isinstance(request_id, str) or not isinstance(progress, int | float):
                raise ProviderMessageError(
                    "'progress' requires a string requestId and a numeric progress"
                )

            await self._relay_progress(params)
        else:
            raise ProviderMessageError(f"Unsupported notification: {method!r}")

    async def _relay_progress(self, params: dict[str, Any]) -> None:
        pending = self._pending.get(params["requestId"])

        if pending is None or pending.session is None or pending.progress_token is None:
            return

        with contextlib.suppress(Exception):
            await pending.session.send_progress_notification(
                progress_token=pending.progress_token,
                progress=params["progress"],
                total=params.get("total"),
                message=params.get("message"),
            )

    async def _notify_resource_updated(self, uri: str) -> None:
        subscribers = self._subscriptions.get(uri)

        if not subscribers:
            return

        for key in list(subscribers):
            session = self._mcp_sessions.get(key)

            if session is None:
                subscribers.discard(key)
                continue

            with contextlib.suppress(Exception):
                await session.send_resource_updated(types.AnyUrl(uri))

        if not subscribers and self._subscriptions.get(uri) is subscribers:
            del self._subscriptions[uri]

    def set_log_level(self, session: Any, level: str) -> None:
        self._log_levels[id(session)] = level

    async def _relay_log(self, params: dict[str, Any]) -> None:
        threshold = _LOG_LEVELS.index(params["level"])
        dead: list[int] = []

        for key, session in list(self._mcp_sessions.items()):
            if threshold < _LOG_LEVELS.index(self._log_levels.get(key, "debug")):
                continue

            try:
                await session.send_log_message(
                    level=params["level"], data=params.get("data"), logger=params.get("logger")
                )
            except Exception:
                logger.debug("Dropping inactive MCP session", exc_info=True)
                dead.append(key)

        self._drop_sessions(dead)

    def _forget_session(self, key: int) -> None:
        self._log_levels.pop(key, None)

        for uri in list(self._subscriptions):
            self._subscriptions[uri].discard(key)

            if not self._subscriptions[uri]:
                del self._subscriptions[uri]

    def validate_output(
        self, tool: CompiledTool, result: types.CallToolResult
    ) -> types.CallToolResult:
        if result.isError or tool.output_validator is None:
            return result

        name = tool.definition.name

        if result.structuredContent is None:
            return self.error_result(
                f"Output validation error for '{name}': outputSchema is defined "
                "but structuredContent is missing"
            )

        try:
            tool.output_validator.validate(result.structuredContent)
        except ValidationError as exc:
            return self.error_result(f"Output validation error for '{name}': {exc.message}")

        return result

    @staticmethod
    def normalize_result(value: Any) -> types.CallToolResult:
        if isinstance(value, dict) and "content" in value:
            return types.CallToolResult.model_validate(value)

        if isinstance(value, str):
            text = value
            structured_content = None
        else:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            structured_content = value if isinstance(value, dict) else None

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=structured_content,
            isError=False,
        )

    @staticmethod
    def error_result(message: str) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=message)],
            isError=True,
        )

    async def close(self) -> None:
        async with self._state_lock:
            websocket = self._websocket
            self._websocket = None
            self._provider_id = None
            self._provider_name = None
            self._clear_registries()
            self._subscriptions = {}
            self._mcp_sessions = {}
            self._log_levels = {}
            self._fail_pending(ChannelOfflineError("The channel was closed"))

        if websocket is not None:
            with contextlib.suppress(Exception):
                await websocket.close(code=1001, reason="Channel closed")

    async def _best_effort_cancel(self, request_id: str, reason: str) -> None:
        with contextlib.suppress(Exception):
            await self.send_to_provider(protocol.cancel(request_id, reason))

    def _fail_pending(self, error: Exception) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(error)

        self._pending.clear()


def _sampling_kwargs(params: dict[str, Any]) -> dict[str, Any]:
    preferences = params.get("modelPreferences")
    return {
        "messages": [types.SamplingMessage.model_validate(m) for m in params["messages"]],
        "max_tokens": params["maxTokens"],
        "system_prompt": params.get("systemPrompt"),
        "include_context": params.get("includeContext"),
        "temperature": params.get("temperature"),
        "stop_sequences": params.get("stopSequences"),
        "model_preferences": (
            types.ModelPreferences.model_validate(preferences) if preferences is not None else None
        ),
    }


def _read_content(item: Any) -> ReadResourceContents:
    if not isinstance(item, dict):
        raise ProviderRequestError("Resource content must be an object")

    mime_type = item.get("mimeType")
    text = item.get("text")

    if isinstance(text, str):
        return ReadResourceContents(content=text, mime_type=mime_type)

    blob = item.get("blob")

    if isinstance(blob, str):
        try:
            decoded = base64.b64decode(blob, validate=True)
        except binascii.Error as exc:
            raise ProviderRequestError("Resource blob is not valid base64") from exc

        return ReadResourceContents(content=decoded, mime_type=mime_type)

    raise ProviderRequestError("Resource content must include text or blob")
