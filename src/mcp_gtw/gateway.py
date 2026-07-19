from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mcp.types as types
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from mcp_gtw import protocol
from mcp_gtw.channel import Channel
from mcp_gtw.config import PROTOCOL_VERSION, GatewaySettings
from mcp_gtw.errors import (
    ChannelOfflineError,
    ChannelReplacedError,
    GatewayConfigurationError,
    GatewayError,
    ProviderMessageError,
)
from mcp_gtw.helpers.security import (
    constant_time_equals,
    extract_bearer_token,
    generate_token,
    origin_is_allowed,
)
from mcp_gtw.listeners import GatewayListener
from mcp_gtw.registry import ChannelRegistry

logger = logging.getLogger(__name__)

SCOPE_CHANNEL_KEY = "gateway_channel_id"

_WEB_DIR = Path(__file__).parent / "web"
_HOME_TEMPLATE = (_WEB_DIR / "index.html").read_text(encoding="utf-8")


class Gateway(GatewayListener):
    """A generic MCP gateway that relays dynamic MCP capabilities between clients and providers.

    Subclass it to build a real application. Override the lifecycle hooks to attach
    domain state, override ``serve`` to run background tasks, and override
    ``register_routes`` or ``home`` to add your own HTTP surface. Swap ``channel_class``
    or ``registry_class`` to customize how sessions are stored.
    """

    settings_class: type[GatewaySettings] = GatewaySettings
    registry_class: type[ChannelRegistry] = ChannelRegistry
    channel_class: type[Channel] = Channel

    mcp_server_name: str = "mcp-gtw"

    def __init__(self, settings: GatewaySettings | None = None) -> None:
        self.settings = settings or self.settings_class()

        if self.settings.admin_enabled and self.settings.admin_key is None:
            raise GatewayConfigurationError(
                "GATEWAY_ADMIN_KEY must be set when GATEWAY_ADMIN_ENABLED is true"
            )

        self.registry = self.registry_class(
            self.settings,
            listeners=[self],
            channel_class=self.channel_class,
        )
        self.server = self._build_server()
        self.manager = self._build_manager()
        self._home_html = _HOME_TEMPLATE.format(
            name=self.settings.app_name,
            initial=self.settings.app_name[:1].upper(),
        )

    async def create_channel(self, **kwargs: Any) -> Channel:
        return await self.registry.create_channel(**kwargs)

    def create_app(self) -> FastAPI:
        app = FastAPI(
            title=self.settings.app_name,
            version=self.settings.app_version,
            lifespan=self.lifespan,
        )
        app.state.gateway = self
        self.add_cors(app)
        self.register_routes(app)
        return app

    def add_cors(self, app: FastAPI) -> None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=self.settings.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def register_routes(self, app: FastAPI) -> None:
        app.add_api_websocket_route("/provider", self.provider_endpoint)
        app.add_api_route("/health", self.health, methods=["GET"])
        app.add_api_route("/", self.home, methods=["GET"], include_in_schema=False)
        app.add_api_route("/logo.svg", self.logo, methods=["GET"], include_in_schema=False)
        app.mount("/mcp", self.mcp_asgi)

        if self.settings.admin_enabled:
            app.add_api_route("/admin", self.admin_page, methods=["GET"], include_in_schema=False)
            app.add_api_route("/admin/stats", self.admin_stats_endpoint, methods=["GET"])

    async def home(self) -> Response:
        return HTMLResponse(self._home_html)

    async def logo(self) -> FileResponse:
        return FileResponse(_WEB_DIR / "logo.svg", media_type="image/svg+xml")

    async def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "channels": self.registry.channel_count,
            "providersConnected": self.registry.connected_provider_count,
        }

    async def admin_page(self, request: Request) -> FileResponse:
        self._require_admin(request)
        return FileResponse(_WEB_DIR / "admin.html")

    async def admin_stats_endpoint(self, request: Request) -> dict[str, Any]:
        self._require_admin(request)
        return self.admin_stats()

    def admin_stats(self) -> dict[str, Any]:
        channels = self.registry.admin_channels()
        return {
            "app": {"name": self.settings.app_name, "version": self.settings.app_version},
            "totals": {
                "channels": len(channels),
                "providersConnected": sum(1 for c in channels if c["providerConnected"]),
                "tools": sum(c["toolCount"] for c in channels),
                "pendingCalls": sum(c["pendingCalls"] for c in channels),
            },
            "channels": channels,
        }

    def _require_admin(self, request: Request) -> None:
        if not constant_time_equals(request.query_params.get("key"), self.settings.admin_key):
            raise HTTPException(status_code=403, detail="Invalid admin key")

    def instructions(self) -> str:
        return (
            "Tools are registered dynamically by the provider session bound to your token. "
            "Keep the session page open while invoking tools."
        )

    @contextlib.asynccontextmanager
    async def lifespan(self, _: FastAPI) -> AsyncIterator[None]:
        async with self.manager.run():
            logger.info("MCP Streamable HTTP session manager started")
            reaper = asyncio.create_task(self._reap_expired_channels())

            try:
                async with self.serve():
                    yield
            finally:
                reaper.cancel()

                with contextlib.suppress(asyncio.CancelledError):
                    await reaper

                await self.registry.close_all()

    @contextlib.asynccontextmanager
    async def serve(self) -> AsyncIterator[None]:
        yield

    async def _reap_expired_channels(self) -> None:
        while True:
            await asyncio.sleep(self.settings.reaper_interval_seconds)

            try:
                removed = await self.registry.purge_expired()
            except Exception:
                logger.exception("Channel reaper iteration failed")
                continue

            if removed:
                logger.info("Reaped %d expired channels", removed)

    def _build_server(self) -> Server:
        server = Server(
            self.mcp_server_name,
            version=self.settings.app_version,
            instructions=self.instructions(),
        )
        gateway = self

        def context() -> tuple[Channel, Any, str | int | None]:
            request = server.request_context
            channel = gateway.channel_for_scope(request.request.scope)
            channel.remember_mcp_session(request.session)
            progress_token = request.meta.progressToken if request.meta else None
            return channel, request.session, progress_token

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            channel, _, _ = context()
            return channel.list_tools()

        # input validation is done per channel because a single server serves every channel
        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            channel, session, progress_token = context()
            return await channel.execute_tool(
                name=name, arguments=arguments or {}, session=session, progress_token=progress_token
            )

        @server.list_resources()
        async def list_resources() -> list[types.Resource]:
            channel, _, _ = context()
            return channel.list_resources()

        @server.list_resource_templates()
        async def list_resource_templates() -> list[types.ResourceTemplate]:
            channel, _, _ = context()
            return channel.list_resource_templates()

        @server.read_resource()
        async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
            channel, session, progress_token = context()
            return await channel.read_resource(
                str(uri), session=session, progress_token=progress_token
            )

        @server.subscribe_resource()
        async def subscribe_resource(uri: AnyUrl) -> None:
            channel, session, _ = context()
            await channel.subscribe(str(uri), session)

        @server.unsubscribe_resource()
        async def unsubscribe_resource(uri: AnyUrl) -> None:
            channel, session, _ = context()
            await channel.unsubscribe(str(uri), session)

        @server.list_prompts()
        async def list_prompts() -> list[types.Prompt]:
            channel, _, _ = context()
            return channel.list_prompts()

        @server.get_prompt()
        async def get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
            channel, session, progress_token = context()
            return await channel.get_prompt(
                name, arguments, session=session, progress_token=progress_token
            )

        @server.completion()
        async def complete(
            ref: types.PromptReference | types.ResourceTemplateReference,
            argument: types.CompletionArgument,
            argument_context: types.CompletionContext | None,
        ) -> types.Completion:
            channel, _, _ = context()
            dump = {"mode": "json", "by_alias": True, "exclude_none": True}
            return await channel.complete(
                ref.model_dump(**dump),
                argument.model_dump(**dump),
                argument_context.model_dump(**dump) if argument_context else None,
            )

        @server.set_logging_level()
        async def set_logging_level(level: types.LoggingLevel) -> None:
            channel, session, _ = context()
            channel.set_log_level(session, level)

        return server

    def _build_manager(self) -> StreamableHTTPSessionManager:
        return StreamableHTTPSessionManager(
            app=self.server,
            event_store=None,
            json_response=self.settings.mcp_json_response,
            stateless=self.settings.mcp_stateless,
            session_idle_timeout=self.settings.mcp_session_idle_timeout_seconds,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    def channel_for_scope(self, scope: Scope) -> Channel:
        channel = self.registry.get(scope[SCOPE_CHANNEL_KEY])

        if channel is None:
            raise GatewayError(f"Channel is no longer available: {scope[SCOPE_CHANNEL_KEY]}")

        return channel

    async def mcp_asgi(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        request = Request(scope)

        token = extract_bearer_token(request.headers.get("authorization"))
        channel = self.registry.resolve_mcp_token(token)

        if channel is None:
            await self._json_response(
                send,
                401,
                {"error": "Unauthorized MCP client"},
                {"www-authenticate": 'Bearer realm="mcp"'},
            )
            return

        requested_channel = scope["path"].removeprefix(scope.get("root_path", "")).strip("/")

        if requested_channel and requested_channel != channel.channel_id:
            await self._json_response(send, 404, {"error": "Unknown MCP service"})
            return

        if not origin_is_allowed(request.headers.get("origin"), self.settings.allowed_mcp_origins):
            await self._json_response(send, 403, {"error": "Origin not allowed"})
            return

        scope[SCOPE_CHANNEL_KEY] = channel.channel_id
        await self.manager.handle_request(scope, receive, send)

    async def provider_endpoint(self, websocket: WebSocket) -> None:
        channel = self.registry.resolve_provider_token(websocket.query_params.get("token"))

        if channel is None:
            await websocket.close(code=1008, reason="Invalid channel token")
            return

        origin = websocket.headers.get("origin")

        if not origin_is_allowed(origin, self.settings.allowed_provider_origins):
            await websocket.close(code=1008, reason="Origin not allowed")
            return

        provider_id = websocket.query_params.get("providerId") or generate_token(12)
        provider_name = websocket.query_params.get("providerName")

        await websocket.accept()
        await channel.attach(websocket, provider_id=provider_id, provider_name=provider_name)
        await self.registry.provider_connected(channel)

        try:
            await channel.send_to_provider(protocol.hello_ack(PROTOCOL_VERSION, channel.channel_id))
            await self._pump_provider_messages(websocket, channel)
        except WebSocketDisconnect:
            logger.info("Provider websocket disconnected: channel=%s", channel.channel_id)
        except (ChannelOfflineError, ChannelReplacedError):
            logger.info("Provider channel closed: channel=%s", channel.channel_id)
        finally:
            if await channel.detach(websocket):
                await self.registry.provider_disconnected(channel)

    async def _pump_provider_messages(self, websocket: WebSocket, channel: Channel) -> None:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(code=message.get("code", 1005))

            text = message.get("text")

            if text is not None and self._message_too_large(text):
                await websocket.close(code=1009, reason="Message too large")
                return

            try:
                await self._handle_provider_frame(channel, text)
            except (ChannelOfflineError, ChannelReplacedError):
                return

    def _message_too_large(self, text: str) -> bool:
        maximum = self.settings.maximum_websocket_message_bytes
        return len(text) > maximum or len(text.encode()) > maximum

    async def _handle_provider_frame(self, channel: Channel, text: str | None) -> None:
        if text is None:
            await channel.send_to_provider(
                protocol.protocol_error("Only text messages are supported")
            )
            return

        try:
            payload = protocol.decode_message(text, self.settings.maximum_json_depth)
            await channel.handle_provider_message(payload)
        except (ValueError, ProviderMessageError) as exc:
            await channel.send_to_provider(protocol.protocol_error(str(exc)))

    @staticmethod
    async def _json_response(
        send: Send,
        status_code: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode()
        headers = [(b"content-type", b"application/json")]

        for key, value in (extra_headers or {}).items():
            headers.append((key.encode(), value.encode()))

        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})
