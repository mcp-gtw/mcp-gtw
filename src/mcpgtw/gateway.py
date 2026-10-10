from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import mcp.types as types
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from mcpgtw import protocol
from mcpgtw.authenticator import Authenticator, TokenAuthenticator
from mcpgtw.channel import Channel
from mcpgtw.codec import JsonProtocolCodec, ProtocolCodec
from mcpgtw.config import PROTOCOL_VERSION, GatewaySettings
from mcpgtw.errors import (
    ChannelOfflineError,
    ChannelReplacedError,
    GatewayConfigurationError,
    GatewayError,
    OAuthRateLimitError,
    ProviderMessageError,
)
from mcpgtw.expiry import ExpiryPolicy, TtlExpiryPolicy
from mcpgtw.listeners import GatewayListener
from mcpgtw.oauth.access_error import McpAccessError
from mcpgtw.oauth.authorization_server import AuthorizationServer
from mcpgtw.oauth.canonical_endpoint import CanonicalMcpEndpoint
from mcpgtw.oauth.channel_access import ChannelAccessPolicy
from mcpgtw.oauth.client_access import McpClientAccessController, TokenMcpAccessController
from mcpgtw.oauth.error_response import rate_limit_response, unavailable_response
from mcpgtw.oauth.hybrid_client_access import HybridMcpAccessController
from mcpgtw.oauth.introspection_verifier import IntrospectionAccessTokenVerifier
from mcpgtw.oauth.jwt_verifier import JwtAccessTokenVerifier
from mcpgtw.oauth.log_filter import OAuthLogFilter
from mcpgtw.oauth.oauth_client_access import OAuthMcpAccessController
from mcpgtw.oauth.rate_limit import OAuthRateLimitPolicy, WindowOAuthRateLimitPolicy
from mcpgtw.oauth.resource_metadata import (
    OAuthMetadataPublisher,
    ProtectedResourceMetadataPublisher,
)
from mcpgtw.oauth.session_binding import McpSessionBindingStore, MemoryMcpSessionBindingStore
from mcpgtw.oauth.token_verifier import AccessTokenVerifier
from mcpgtw.oauth.tool_access import RequiredScopesToolAccess, ToolAccessPolicy
from mcpgtw.oauth.tool_metadata import OAuthToolMetadataMiddleware
from mcpgtw.origin import ListOriginPolicy, OriginPolicy
from mcpgtw.registry import ChannelRegistry
from mcpgtw.tokens import SecretsTokenProvider, TokenProvider

logger = logging.getLogger(__name__)
_OAUTH_LOG_FILTER = OAuthLogFilter()

SCOPE_CHANNEL_KEY = "gateway_channel_id"

_WEB_FILES: tuple[tuple[str, str, str], ...] = (
    ("/logo.svg", "logo.svg", "image/svg+xml"),
    ("/favicon.ico", "favicon.ico", "image/x-icon"),
    ("/favicon-16x16.png", "favicon-16x16.png", "image/png"),
    ("/favicon-32x32.png", "favicon-32x32.png", "image/png"),
    ("/apple-touch-icon.png", "apple-touch-icon.png", "image/png"),
    ("/android-chrome-192x192.png", "android-chrome-192x192.png", "image/png"),
    ("/android-chrome-512x512.png", "android-chrome-512x512.png", "image/png"),
    ("/site.webmanifest", "site.webmanifest", "application/manifest+json"),
)
_RESERVED_ROUTE_PATHS = frozenset(
    {"/", "/health", "/provider", *(path for path, _, _ in _WEB_FILES)}
)
_MCP_MOUNT_PATH = "/mcp"

_WEB_DIR = Path(__file__).parent / "web"
_HOME_TEMPLATE = (_WEB_DIR / "index.html").read_text(encoding="utf-8")


class Gateway(GatewayListener):
    """A generic MCP gateway that relays dynamic MCP capabilities between clients and providers.

    It is a composition root: every behaviour is a swappable strategy. Set a ``*_class``
    attribute on a subclass to change the default, or pass a built instance to ``__init__``
    for dependency injection. Override the lifecycle hooks to attach domain state, ``serve``
    to run background tasks, and ``register_routes`` or ``home`` to add your own HTTP surface.
    """

    settings_class: type[GatewaySettings] = GatewaySettings
    registry_class: type[ChannelRegistry] = ChannelRegistry
    channel_class: type[Channel] = Channel
    token_provider_class: type[TokenProvider] = SecretsTokenProvider
    origin_policy_class: type[OriginPolicy] = ListOriginPolicy
    expiry_policy_class: type[ExpiryPolicy] = TtlExpiryPolicy
    codec_class: type[ProtocolCodec] = JsonProtocolCodec
    authenticator_class: type[Authenticator] = TokenAuthenticator

    mcp_access_controller_class: type[McpClientAccessController] = TokenMcpAccessController
    tool_access_policy_class: type[ToolAccessPolicy] = RequiredScopesToolAccess
    oauth_rate_limit_class: type[OAuthRateLimitPolicy] = WindowOAuthRateLimitPolicy
    oauth_metadata_class: type[OAuthMetadataPublisher] = ProtectedResourceMetadataPublisher
    session_binding_store_class: type[McpSessionBindingStore] = MemoryMcpSessionBindingStore

    mcp_server_name: str = "mcp-gtw"

    def __init__(
        self,
        settings: GatewaySettings | None = None,
        *,
        tokens: TokenProvider | None = None,
        codec: ProtocolCodec | None = None,
        provider_origins: OriginPolicy | None = None,
        mcp_origins: OriginPolicy | None = None,
        expiry_policy: ExpiryPolicy | None = None,
        registry: ChannelRegistry | None = None,
        authenticator: Authenticator | None = None,
        mcp_access_controller: McpClientAccessController | None = None,
        access_token_verifier: AccessTokenVerifier | None = None,
        tool_access_policy: ToolAccessPolicy | None = None,
        channel_access: ChannelAccessPolicy | None = None,
        session_bindings: McpSessionBindingStore | None = None,
        oauth_rate_limit: OAuthRateLimitPolicy | None = None,
        oauth_metadata: OAuthMetadataPublisher | None = None,
        static_channel_eligible: Callable[[Channel], bool] | None = None,
        authorization_server: AuthorizationServer | None = None,
    ) -> None:
        self.settings = settings or self.settings_class()
        self.mcp_path = (
            urlsplit(self.settings.oauth_resource_url).path
            if self.settings.oauth_mode != "off"
            else "/mcp"
        )

        if self.settings.oauth_mode != "off":
            logging.getLogger("uvicorn.error").addFilter(_OAUTH_LOG_FILTER)

        if self.settings.admin_enabled and not self.settings.admin_key:
            raise GatewayConfigurationError(
                "GATEWAY_ADMIN_KEY must be a non-empty value when GATEWAY_ADMIN_ENABLED is true"
            )

        if self.settings.admin_enabled and (
            self._admin_path_conflicts(self.settings.admin_path)
            or self.settings.admin_path == self.mcp_path
            or self.settings.admin_path.startswith(self.mcp_path + "/")
        ):
            raise GatewayConfigurationError(
                f"GATEWAY_ADMIN_PATH {self.settings.admin_path!r} collides with a built-in route"
            )

        self.tokens = tokens or self.token_provider_class()
        self.codec = codec or self.codec_class(self.settings.maximum_json_depth)
        self.provider_origins = provider_origins or self.origin_policy_class(
            self.settings.allowed_provider_origins
        )
        self.mcp_origins = mcp_origins or self.origin_policy_class(
            self.settings.allowed_mcp_origins
        )
        self.registry = registry or self.registry_class(
            self.settings,
            channel_class=self.channel_class,
            tokens=self.tokens,
            expiry_policy=expiry_policy
            or self.expiry_policy_class(self.settings.offline_ttl_seconds),
        )
        self.registry.add_listener(self)
        self.authenticator = authenticator or self.authenticator_class(self.registry)
        self.oauth_rate_limit = oauth_rate_limit or self.oauth_rate_limit_class(
            self.settings.oauth_rate_limit_requests,
            self.settings.oauth_rate_limit_window_seconds,
            self.settings.oauth_rate_limit_maximum_keys,
            self.settings.oauth_rate_limit_backoff_seconds,
            self.settings.oauth_rate_limit_maximum_backoff_seconds,
        )
        if self.settings.oauth_mode == "off" and oauth_metadata is not None:
            raise GatewayConfigurationError("OAuth metadata requires an enabled OAuth mode")

        self.oauth_metadata = (
            (oauth_metadata or self.oauth_metadata_class(self.settings))
            if self.settings.oauth_mode != "off"
            else None
        )
        self.session_bindings = session_bindings or self.session_binding_store_class(
            self.settings.oauth_maximum_sessions, self.settings.mcp_session_idle_timeout_seconds
        )
        self.authorization_server = authorization_server
        self.tool_access_policy = tool_access_policy or self.tool_access_policy_class(
            frozenset(self.settings.oauth_required_scopes)
        )
        self._oauth_http_client: httpx.AsyncClient | None = None
        self.mcp_access_controller = self._build_access_controller(
            mcp_access_controller, access_token_verifier, channel_access, static_channel_eligible
        )
        self.server = self._build_server()
        self.manager = self._build_manager()
        self._home_html = _HOME_TEMPLATE.format(name=self.settings.app_name)

    def _build_access_controller(
        self,
        controller: McpClientAccessController | None,
        verifier: AccessTokenVerifier | None,
        channel_access: ChannelAccessPolicy | None,
        static_eligible: Callable[[Channel], bool] | None,
    ) -> McpClientAccessController:
        if self.settings.oauth_mode == "embedded":
            if self.authorization_server is None:
                raise GatewayConfigurationError(
                    "Embedded OAuth requires an injected authorization server"
                )

            if controller is not None or verifier is not None:
                raise GatewayConfigurationError("Embedded OAuth owns access token verification")

            verifier = self.authorization_server
        elif self.authorization_server is not None:
            raise GatewayConfigurationError("Authorization server requires embedded OAuth mode")

        if controller is not None:
            return controller

        token_access = self.mcp_access_controller_class(self.authenticator)

        if self.settings.oauth_mode == "off":
            return token_access

        if channel_access is None:
            raise GatewayConfigurationError("OAuth requires an explicit channel access policy")

        if self.settings.oauth_allow_static_mcp_tokens and static_eligible is None:
            raise GatewayConfigurationError(
                "Hybrid OAuth requires explicit static channel eligibility"
            )

        if verifier is None:
            if self.settings.oauth_token_verifier == "custom":
                raise GatewayConfigurationError("Custom OAuth verifier must be injected")

            self._oauth_http_client = httpx.AsyncClient(
                timeout=self.settings.oauth_http_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            )
            verifier = (
                JwtAccessTokenVerifier(self.settings, self._oauth_http_client)
                if self.settings.oauth_token_verifier == "jwt"
                else IntrospectionAccessTokenVerifier(self.settings, self._oauth_http_client)
            )

        self.registry.add_listener(channel_access)
        oauth_access = OAuthMcpAccessController(
            self.settings, self.registry, verifier, channel_access
        )

        if not self.settings.oauth_allow_static_mcp_tokens:
            return oauth_access

        return HybridMcpAccessController(
            self.registry,
            token_access,
            oauth_access,
            static_eligible,
            self.settings.oauth_max_token_bytes,
        )

    async def create_channel(self, **kwargs: Any) -> Channel:
        return await self.registry.create_channel(**kwargs)

    def create_app(self) -> FastAPI:
        app = FastAPI(title=self.settings.app_name, lifespan=self.lifespan)

        if self.settings.expose_version:
            app.version = self.settings.app_version

        if self.oauth_metadata is not None:
            app.add_exception_handler(OAuthRateLimitError, rate_limit_response)
            app.add_exception_handler(McpAccessError, unavailable_response)

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
            expose_headers=["WWW-Authenticate", "Mcp-Session-Id"]
            if self.settings.oauth_mode != "off"
            else [],
        )

    def register_routes(self, app: FastAPI) -> None:
        app.add_api_websocket_route("/provider", self.provider_endpoint)
        app.add_api_route("/health", self.health, methods=["GET"])
        app.add_api_route("/", self.home, methods=["GET"], include_in_schema=False)

        for path, filename, media_type in _WEB_FILES:
            app.add_api_route(
                path,
                self._web_file(filename, media_type),
                methods=["GET"],
                include_in_schema=False,
            )

        if self.oauth_metadata is not None:
            for path in {
                self.oauth_metadata.path,
                "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-protected-resource",
            }:
                if self.settings.admin_enabled and (
                    self.settings.admin_path == path
                    or self.settings.admin_path.startswith(path + "/")
                ):
                    raise GatewayConfigurationError("Admin path collides with OAuth metadata")

                app.add_api_route(path, self.oauth_metadata.metadata, methods=["GET"])

        if self.authorization_server is not None:
            self.authorization_server.register_routes(app)

        if self.oauth_metadata is not None:
            app.router.routes.append(
                Route(
                    self.mcp_path,
                    CanonicalMcpEndpoint(self.mcp_asgi, self.mcp_path),
                    methods=["GET", "POST", "DELETE"],
                )
            )

        app.mount(self.mcp_path, self.mcp_asgi)

        if self.settings.admin_enabled:
            admin_path = self.settings.admin_path
            app.add_api_route(admin_path, self.admin_page, methods=["GET"], include_in_schema=False)
            app.add_api_route(f"{admin_path}/stats", self.admin_stats_endpoint, methods=["GET"])

    @staticmethod
    def _admin_path_conflicts(path: str) -> bool:
        return (
            path in _RESERVED_ROUTE_PATHS
            or path == _MCP_MOUNT_PATH
            or path.startswith(f"{_MCP_MOUNT_PATH}/")
        )

    async def home(self) -> Response:
        return HTMLResponse(self._home_html)

    @staticmethod
    def _web_file(filename: str, media_type: str) -> Callable[[], Any]:
        async def serve() -> FileResponse:
            return FileResponse(_WEB_DIR / filename, media_type=media_type)

        return serve

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "channels": self.registry.channel_count}

    async def admin_page(self, request: Request) -> FileResponse:
        self._require_admin(request)
        return FileResponse(_WEB_DIR / "admin.html")

    async def admin_stats_endpoint(self, request: Request) -> dict[str, Any]:
        self._require_admin(request)
        return self.admin_stats()

    def admin_stats(self) -> dict[str, Any]:
        channels = self.registry.admin_channels()
        app: dict[str, Any] = {"name": self.settings.app_name}

        if self.settings.expose_version:
            app["version"] = self.settings.app_version

        return {
            "app": app,
            "totals": {
                "channels": len(channels),
                "providersConnected": sum(1 for c in channels if c["providerConnected"]),
                "tools": sum(c["toolCount"] for c in channels),
                "pendingCalls": sum(c["pendingCalls"] for c in channels),
            },
            "channels": channels,
        }

    def _require_admin(self, request: Request) -> None:
        if not self.tokens.equals(request.query_params.get("key"), self.settings.admin_key):
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
                self.session_bindings.clear()

                if self._oauth_http_client is not None:
                    await self._oauth_http_client.aclose()

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
        def context(ctx: Any) -> tuple[Channel, Any, str | int | None]:
            channel = self.channel_for_scope(ctx.request.scope)
            channel.remember_mcp_session(ctx.session)
            progress_token = ctx.meta.get("progress_token") if ctx.meta else None
            return channel, ctx.session, progress_token

        async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
            channel, _, _ = context(ctx)
            tools = channel.list_tools()
            access = ctx.request.scope.get("gateway_access_context")

            if self.oauth_metadata is not None and access.credential_kind == "oauth":
                return types.ListToolsResult(
                    tools=[self.tool_access_policy.describe(channel, tool) for tool in tools]
                )

            return types.ListToolsResult(tools=tools)

        async def call_tool(ctx: Any, params: Any) -> types.CallToolResult:
            channel, session, progress_token = context(ctx)
            access = ctx.request.scope.get("gateway_access_context")

            if self.oauth_metadata is not None and access.credential_kind == "oauth":
                scopes = self.tool_access_policy.required_scopes(channel, params.name)

                try:
                    current = await self.mcp_access_controller.authorize(ctx.request)

                    if (
                        current.channel is not channel
                        or current.context.principal_id != access.principal_id
                    ):
                        raise McpAccessError("channel_forbidden")

                    if not scopes <= current.context.scopes:
                        raise McpAccessError("insufficient_scope")

                except McpAccessError as exc:
                    return self.tool_access_policy.challenge(
                        self.oauth_metadata, exc.reason, scopes
                    )

            return await channel.execute_tool(
                name=params.name,
                arguments=params.arguments or {},
                session=session,
                progress_token=progress_token,
            )

        async def list_resources(ctx: Any, params: Any) -> types.ListResourcesResult:
            channel, _, _ = context(ctx)
            return types.ListResourcesResult(resources=channel.list_resources())

        async def list_templates(ctx: Any, params: Any) -> types.ListResourceTemplatesResult:
            channel, _, _ = context(ctx)
            return types.ListResourceTemplatesResult(
                resource_templates=channel.list_resource_templates()
            )

        async def read_resource(ctx: Any, params: Any) -> types.ReadResourceResult:
            channel, session, progress_token = context(ctx)
            contents = await channel.read_resource(
                str(params.uri), session=session, progress_token=progress_token
            )
            return types.ReadResourceResult(
                contents=[
                    types.TextResourceContents(
                        uri=params.uri, text=item.content, mime_type=item.mime_type, meta=item.meta
                    )
                    if isinstance(item.content, str)
                    else types.BlobResourceContents(
                        uri=params.uri,
                        blob=base64.b64encode(item.content).decode(),
                        mime_type=item.mime_type,
                        meta=item.meta,
                    )
                    for item in contents
                ]
            )

        async def subscribe(ctx: Any, params: Any) -> types.EmptyResult:
            channel, session, _ = context(ctx)
            await channel.subscribe(str(params.uri), session)
            return types.EmptyResult()

        async def unsubscribe(ctx: Any, params: Any) -> types.EmptyResult:
            channel, session, _ = context(ctx)
            await channel.unsubscribe(str(params.uri), session)
            return types.EmptyResult()

        async def list_prompts(ctx: Any, params: Any) -> types.ListPromptsResult:
            channel, _, _ = context(ctx)
            return types.ListPromptsResult(prompts=channel.list_prompts())

        async def get_prompt(ctx: Any, params: Any) -> types.GetPromptResult:
            channel, session, progress_token = context(ctx)
            return await channel.get_prompt(
                params.name, params.arguments, session=session, progress_token=progress_token
            )

        async def complete(ctx: Any, params: Any) -> types.CompleteResult:
            channel, _, _ = context(ctx)
            dump = {"mode": "json", "by_alias": True, "exclude_none": True}
            result = await channel.complete(
                params.ref.model_dump(**dump),
                params.argument.model_dump(**dump),
                params.context.model_dump(**dump) if params.context else None,
            )
            return types.CompleteResult(completion=result)

        async def set_logging(ctx: Any, params: Any) -> types.EmptyResult:
            channel, session, _ = context(ctx)
            channel.set_log_level(session, params.level)
            return types.EmptyResult()

        server = Server(
            self.mcp_server_name,
            version=self.settings.app_version,
            instructions=self.instructions(),
            on_list_tools=list_tools,
            on_call_tool=call_tool,
            on_list_resources=list_resources,
            on_list_resource_templates=list_templates,
            on_read_resource=read_resource,
            on_subscribe_resource=subscribe,
            on_unsubscribe_resource=unsubscribe,
            on_list_prompts=list_prompts,
            on_get_prompt=get_prompt,
            on_completion=complete,
            on_set_logging_level=set_logging,
        )

        if self.oauth_metadata is not None:
            server.middleware.append(OAuthToolMetadataMiddleware())

        return server

    def _build_manager(self) -> StreamableHTTPSessionManager:
        idle_timeout = (
            None if self.settings.mcp_stateless else self.settings.mcp_session_idle_timeout_seconds
        )

        return StreamableHTTPSessionManager(
            app=self.server,
            event_store=None,
            json_response=self.settings.mcp_json_response,
            stateless=self.settings.mcp_stateless,
            session_idle_timeout=idle_timeout,
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

        if not self.mcp_origins.allows(request.headers.get("origin")):
            await self._json_response(send, 403, {"error": "Origin not allowed"})
            return

        address = request.client.host if request.client else "unknown"

        if self.oauth_metadata is not None and not self.oauth_rate_limit.admit(address):
            await self._json_response(
                send,
                429,
                {"error": "Request budget exceeded"},
                {
                    "retry-after": str(self.oauth_rate_limit.retry_after(address)),
                    "cache-control": "no-store",
                },
            )
            return

        try:
            authorized = await self.mcp_access_controller.authorize(request)
        except McpAccessError as exc:
            challenge = (
                self.oauth_metadata.challenge(exc.reason)
                if self.oauth_metadata
                else 'Bearer realm="mcp"'
            )
            await self._json_response(
                send,
                exc.status_code,
                {"error": "MCP access denied"},
                {"www-authenticate": challenge, "cache-control": "no-store"},
            )
            return

        if authorized.context.credential_kind == "oauth":
            for key in (
                "client:" + authorized.context.issuer + "\0" + authorized.context.client_id,
                "principal:" + authorized.context.principal_id,
            ):
                if not self.oauth_rate_limit.admit(key):
                    await self._json_response(
                        send,
                        429,
                        {"error": "Request budget exceeded"},
                        {
                            "retry-after": str(self.oauth_rate_limit.retry_after(key)),
                            "cache-control": "no-store",
                        },
                    )
                    return

        channel = authorized.channel
        requested_channel = scope["path"].removeprefix(scope.get("root_path", "")).strip("/")

        if requested_channel and requested_channel != channel.channel_id:
            await self._json_response(send, 404, {"error": "Unknown MCP service"})
            return

        scope[SCOPE_CHANNEL_KEY] = channel.channel_id
        scope["gateway_access_context"] = authorized.context

        if self.oauth_metadata is None:
            await self.manager.handle_request(scope, receive, send)
            return

        session_ids = request.headers.getlist("mcp-session-id")
        session_id = session_ids[0] if len(session_ids) == 1 else None

        if session_ids and (
            len(session_ids) != 1
            or self.settings.mcp_stateless
            or not self.session_bindings.accepts(session_id, authorized.context)
        ):
            await self._json_response(send, 404, {"error": "Unknown MCP session"})
            return

        response_started = False

        async def guarded_send(message: Any) -> None:
            nonlocal response_started
            current = await self.mcp_access_controller.authorize(request)

            if (
                self.registry.get(channel.channel_id) is not channel
                or current.channel is not channel
                or current.context.principal_id != authorized.context.principal_id
            ):
                raise McpAccessError("channel_forbidden")

            if message["type"] == "http.response.start":
                for key, value in message.get("headers", []):
                    if key.lower() == b"mcp-session-id" and not self.session_bindings.bind(
                        value.decode(), current.context
                    ):
                        raise McpAccessError("invalid_session")

                if request.method == "DELETE" and message["status"] < 300 and session_id:
                    self.session_bindings.remove(session_id)

            await send(message)
            response_started = True

        try:
            await self.manager.handle_request(scope, receive, guarded_send)
        except McpAccessError as exc:
            if session_id:
                self.session_bindings.remove(session_id)

            if response_started:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            else:
                await self._json_response(
                    send,
                    exc.status_code,
                    {"error": "MCP access denied"},
                    {
                        "www-authenticate": self.oauth_metadata.challenge(exc.reason),
                        "cache-control": "no-store",
                    },
                )

    async def on_channel_removed(self, channel: Channel) -> None:
        self.session_bindings.remove_channel(channel.channel_id)

    async def provider_endpoint(self, websocket: WebSocket) -> None:
        if not self.provider_origins.allows(websocket.headers.get("origin")):
            await websocket.close(code=1008, reason="Origin not allowed")
            return

        channel = await self.authenticator.authenticate_provider(websocket)

        if channel is None:
            await websocket.close(code=1008, reason="Invalid channel token")
            return

        provider_id = websocket.query_params.get("providerId") or self.tokens.generate(12)
        provider_name = websocket.query_params.get("providerName")

        await websocket.accept()
        await channel.attach(websocket, provider_id=provider_id, provider_name=provider_name)

        if not await self.registry.provider_connected(channel):
            await channel.detach(websocket)
            await websocket.close(code=1008, reason="Channel is no longer available")
            return

        try:
            await channel.send_to_provider(protocol.hello_ack(PROTOCOL_VERSION, channel.channel_id))
            await channel.resync_subscriptions()
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
            payload = text if text is not None else message.get("bytes")

            if payload is not None and self._message_too_large(payload):
                await websocket.close(code=1009, reason="Message too large")
                return

            try:
                await self._handle_provider_frame(websocket, channel, text)
            except (ChannelOfflineError, ChannelReplacedError):
                return

    def _message_too_large(self, payload: str | bytes) -> bool:
        maximum = self.settings.maximum_websocket_message_bytes

        if isinstance(payload, str):
            return len(payload) > maximum or len(payload.encode()) > maximum

        return len(payload) > maximum

    async def _handle_provider_frame(
        self, websocket: WebSocket, channel: Channel, text: str | None
    ) -> None:
        try:
            payload = self.codec.decode(text)
            await channel.handle_provider_message(websocket, payload)
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
