import contextlib
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI

from mcpgtw.config import GatewaySettings
from mcpgtw.gateway import Gateway
from mcpgtw.oauth.channel_access import DenyUnlessGranted
from mcpgtw.oauth.channel_grants import MemoryChannelGrantStore
from mcpgtw.oauth.token_verifier import AccessTokenVerifier
from mcpgtw.oauth.verified_principal import VerifiedPrincipal

config = json.loads(Path(os.environ["TEST_CONFIG"]).read_text())
principal = VerifiedPrincipal(
    "https://local-test.invalid", "alice", "host", frozenset({"mcp:access"}), int(time.time()) + 300
)


class TestVerifier(AccessTokenVerifier):
    async def verify(self, raw_token, expected_resource):
        return principal if raw_token == config["access"] else None


class TestGateway(Gateway):
    @contextlib.asynccontextmanager
    async def serve(self) -> AsyncIterator[None]:
        for channel_id in config["channels"]:
            await self.registry.create_channel(
                channel_id=channel_id,
                mcp_token=config[channel_id]["mcp"],
                provider_token=config[channel_id]["provider"],
            )
        await grants.grant(principal, "oauth")
        yield

    def register_routes(self, app: FastAPI):
        super().register_routes(app)

        @app.get("/ready")
        async def ready():
            return {
                "ready": all(self.registry.get(c).provider_connected for c in config["channels"])
            }


grants = MemoryChannelGrantStore()
settings = GatewaySettings(
    oauth_mode="resource_server",
    oauth_resource_url="http://127.0.0.1:19480/mcp",
    oauth_authorization_servers=["https://local-test.invalid"],
    oauth_allow_localhost_http=True,
    oauth_token_verifier="custom",
    oauth_allow_static_mcp_tokens=True,
    mcp_json_response=True,
)
gateway = TestGateway(
    settings,
    access_token_verifier=TestVerifier(),
    channel_access=DenyUnlessGranted(grants),
    static_channel_eligible=lambda c: c.channel_id == "static",
)
app = gateway.create_app()
