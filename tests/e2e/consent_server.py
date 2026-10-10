import os
from pathlib import Path

from fastapi import FastAPI

from mcpgtw.config import GatewaySettings
from mcpgtw.oauth.authorization_server import EmbeddedAuthorizationServer
from mcpgtw.oauth.client_registry import OAuthClientRegistry
from mcpgtw.oauth.consent_policy import ConsentPolicy
from mcpgtw.oauth.identity import SqlitePasswordIdentity
from mcpgtw.oauth.signing_key import OAuthSigningKey
from mcpgtw.oauth.state_store import SqliteOAuthStateStore


class AccountConsent(ConsentPolicy):
    async def approve(self, subject, client_id, resource, scopes):
        return True

    async def validate(self, subject, client_id, resource, scopes):
        return True


issuer = os.environ["TEST_ISSUER"]
directory = Path(os.environ["TEST_DIRECTORY"])
settings = GatewaySettings(
    oauth_mode="embedded",
    oauth_resource_url=issuer + "/mcp",
    oauth_authorization_servers=[issuer],
    oauth_jwks_url=issuer + "/oauth/jwks",
    oauth_embedded_dcr_enabled=True,
)
store = SqliteOAuthStateStore(str(directory / "oauth.sqlite"))
server = EmbeddedAuthorizationServer(
    settings,
    SqlitePasswordIdentity(str(directory / "oauth.sqlite"), registration_enabled=True),
    AccountConsent(),
    store,
    OAuthSigningKey(str(directory / "signing.pem")),
    OAuthClientRegistry(store, issuer, issuer + "/mcp", dcr_enabled=True),
)
app = FastAPI()
server.register_routes(app)
