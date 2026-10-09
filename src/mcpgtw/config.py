from __future__ import annotations

from importlib.metadata import version
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROTOCOL_VERSION = "mcp-gtw-provider/1"


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    app_name: str = "MCP Gateway"
    app_version: str = version("mcp-gtw")
    expose_version: bool = False

    host: str = "127.0.0.1"
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("GATEWAY_PORT", "PORT"),
    )
    maximum_concurrent_connections: int | None = Field(default=None, ge=1)

    allowed_provider_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    allowed_mcp_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    tool_call_timeout_seconds: float | None = Field(default=60.0, gt=0)
    maximum_tools: int | None = Field(default=128, ge=1)
    maximum_tool_definition_bytes: int | None = Field(default=64 * 1024, ge=1)
    maximum_websocket_message_bytes: int = Field(default=512 * 1024, ge=1)
    maximum_json_depth: int = Field(default=100, ge=1)
    maximum_pending_calls_per_channel: int | None = Field(default=64, ge=1)
    maximum_mcp_sessions_per_channel: int | None = Field(default=16, ge=1)
    maximum_subscriptions_per_channel: int | None = Field(default=1024, ge=1)
    maximum_channels: int | None = Field(default=10_000, ge=1)
    offline_ttl_seconds: float = Field(default=300.0, ge=0)
    reaper_interval_seconds: float = Field(default=30.0, gt=0)

    mcp_json_response: bool = False
    mcp_stateless: bool = False
    mcp_session_idle_timeout_seconds: float = Field(default=900.0, gt=0)

    admin_enabled: bool = False
    admin_key: str | None = None
    admin_path: str = "/admin"

    oauth_mode: Literal["off", "resource_server", "embedded"] = "off"
    oauth_resource_url: str = ""
    oauth_authorization_servers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    oauth_required_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["mcp:access"]
    )
    oauth_allow_static_mcp_tokens: bool = False
    oauth_allow_localhost_http: bool = False
    oauth_token_verifier: Literal["jwt", "introspection", "custom"] = "jwt"
    oauth_jwks_url: str = ""
    oauth_jwt_allowed_algorithms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["RS256", "ES256"]
    )
    oauth_introspection_url: str = ""
    oauth_introspection_client_id: str = ""
    oauth_introspection_client_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    oauth_http_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    oauth_max_token_bytes: int = Field(default=8192, ge=1)
    oauth_max_metadata_bytes: int = Field(default=65536, ge=1)
    oauth_jwks_cache_ttl_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    oauth_clock_skew_seconds: int = Field(default=30, ge=0, le=60)
    oauth_rate_limit_requests: int = Field(default=120, ge=1)
    oauth_rate_limit_window_seconds: float = Field(default=1, gt=0, allow_inf_nan=False)
    oauth_rate_limit_maximum_keys: int = Field(default=10000, ge=1)
    oauth_maximum_sessions: int = Field(default=10000, ge=1)

    @model_validator(mode="after")
    def validate_oauth(self) -> GatewaySettings:
        if self.oauth_mode == "off":
            return self

        if not self.oauth_resource_url or not self.oauth_authorization_servers:
            raise ValueError("OAuth requires resource URL and authorization servers")

        for url in [
            self.oauth_resource_url,
            *self.oauth_authorization_servers,
            self.oauth_jwks_url,
            self.oauth_introspection_url,
        ]:
            if not url:
                continue

            validate_oauth_url(url, self.oauth_allow_localhost_http)

        resource = urlsplit(self.oauth_resource_url)

        if not resource.path.endswith("/mcp"):
            raise ValueError("OAuth resource URL must end with /mcp")

        if not self.oauth_required_scopes or any(
            not scope or any(ord(c) < 33 or ord(c) > 126 or c in '"\\' for c in scope)
            for scope in self.oauth_required_scopes
        ):
            raise ValueError("OAuth scopes must be non-empty RFC 6749 scope tokens")

        if self.oauth_token_verifier == "jwt":
            if not self.oauth_jwks_url or len(self.oauth_authorization_servers) != 1:
                raise ValueError("JWT requires one issuer and a trusted JWKS URL")

            if not self.oauth_jwt_allowed_algorithms or not set(
                self.oauth_jwt_allowed_algorithms
            ) <= {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}:
                raise ValueError("OAuth JWT algorithms must be asymmetric")

        if self.oauth_token_verifier == "introspection" and (
            not self.oauth_introspection_url
            or not self.oauth_introspection_client_id
            or not self.oauth_introspection_client_secret.get_secret_value()
            or len(self.oauth_authorization_servers) != 1
        ):
            raise ValueError("Introspection requires one issuer, endpoint and client credentials")

        return self

    @field_validator(
        "oauth_authorization_servers",
        "oauth_required_scopes",
        "oauth_jwt_allowed_algorithms",
        "allowed_provider_origins",
        "allowed_mcp_origins",
        "cors_allow_origins",
        mode="before",
    )
    @classmethod
    def parse_csv_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]

        return value

    @field_validator("admin_path")
    @classmethod
    def validate_admin_path(cls, value: str) -> str:
        if not value.startswith("/") or value == "/" or value.endswith("/"):
            raise ValueError("admin_path must start with '/', not be '/', and not end with '/'")

        return value

    @field_validator(
        "tool_call_timeout_seconds",
        "maximum_tools",
        "maximum_tool_definition_bytes",
        "maximum_pending_calls_per_channel",
        "maximum_mcp_sessions_per_channel",
        "maximum_subscriptions_per_channel",
        "maximum_channels",
        "maximum_concurrent_connections",
        mode="before",
    )
    @classmethod
    def empty_means_unlimited(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None

        return value


def validate_oauth_url(value: str, allow_localhost_http: bool = False) -> None:
    parsed = urlsplit(value)
    _ = parsed.port
    local_http = (
        allow_localhost_http
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )

    if (
        not parsed.hostname
        or (parsed.scheme != "https" and not local_http)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or "*" in value
        or any(ord(c) <= 32 or ord(c) >= 127 or c in '"\\' for c in value)
    ):
        raise ValueError("OAuth URLs require HTTPS, no credentials, query or fragment")
