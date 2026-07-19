from __future__ import annotations

from importlib.metadata import version
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROTOCOL_VERSION = "mcp-gtw-provider/1"


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MCP Gateway"
    app_version: str = version("mcp-gtw")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    maximum_concurrent_connections: int | None = Field(default=None, ge=1)

    allowed_provider_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    allowed_mcp_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    tool_call_timeout_seconds: float = Field(default=60.0, gt=0)
    maximum_tools: int = Field(default=128, ge=1)
    maximum_tool_definition_bytes: int = Field(default=64 * 1024, ge=1)
    maximum_websocket_message_bytes: int = Field(default=512 * 1024, ge=1)
    maximum_json_depth: int = Field(default=100, ge=1)
    maximum_pending_calls_per_channel: int = Field(default=64, ge=1)
    maximum_mcp_sessions_per_channel: int = Field(default=16, ge=1)
    maximum_channels: int = Field(default=10_000, ge=1)
    offline_ttl_seconds: float = Field(default=300.0, ge=0)
    reaper_interval_seconds: float = Field(default=30.0, gt=0)

    mcp_json_response: bool = False
    mcp_stateless: bool = False
    mcp_session_idle_timeout_seconds: float = Field(default=900.0, gt=0)

    admin_enabled: bool = False
    admin_key: str | None = None

    @field_validator(
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
