from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import time
from abc import ABC, abstractmethod
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlsplit, urlunsplit

import jwt
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from mcpgtw.config import GatewaySettings
from mcpgtw.errors import GatewayConfigurationError, OAuthRateLimitError
from mcpgtw.oauth.access_error import McpAccessError
from mcpgtw.oauth.claims import principal_from_claims
from mcpgtw.oauth.client_metadata import HttpsClientMetadataResolver
from mcpgtw.oauth.client_registry import OAuthClientRegistry
from mcpgtw.oauth.consent_policy import ConsentPolicy
from mcpgtw.oauth.endpoint_limits import OAuthEndpointLimits
from mcpgtw.oauth.error_response import rate_limit_response, unavailable_response
from mcpgtw.oauth.identity import IdentityAuthenticator
from mcpgtw.oauth.page import AuthorizationPage
from mcpgtw.oauth.signing_key import OAuthSigningKey
from mcpgtw.oauth.state_store import OAuthStateStore
from mcpgtw.oauth.token_verifier import AccessTokenVerifier
from mcpgtw.oauth.verified_principal import VerifiedPrincipal

HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        "; frame-ancestors 'none'; base-uri 'none'"
    ),
}


class AuthorizationServer(AccessTokenVerifier, ABC):
    @abstractmethod
    def register_routes(self, app: FastAPI) -> None: ...


class EmbeddedAuthorizationServer(AuthorizationServer):
    limits_class: type[OAuthEndpointLimits] = OAuthEndpointLimits
    page_class: type[AuthorizationPage] = AuthorizationPage

    def __init__(
        self,
        settings: GatewaySettings,
        identity: IdentityAuthenticator,
        consent: ConsentPolicy,
        store: OAuthStateStore,
        key: OAuthSigningKey,
        clients: OAuthClientRegistry,
        *,
        limits: OAuthEndpointLimits | None = None,
    ) -> None:
        self.settings = settings
        self.pages = self.page_class()
        self.identity = identity
        self.consent = consent
        self.store = store
        self.key = key
        self.clients = clients
        self.issuer = settings.oauth_embedded_issuer
        issuer = urlsplit(self.issuer)
        self.origin = f"{issuer.scheme}://{issuer.netloc}"
        self.path = issuer.path
        self.cookie_path = self.path + "/oauth"
        self.resource = settings.oauth_resource_url
        clients.scopes = frozenset({"openid", *settings.oauth_supported_scopes})
        self.limits = limits or self.limits_class(settings)
        self.clients.metadata_limit = self.limits.cimd

        if settings.oauth_embedded_cimd_enabled and clients.metadata_resolver is None:
            clients.metadata_resolver = HttpsClientMetadataResolver(
                settings.oauth_http_timeout_seconds,
                settings.oauth_max_metadata_bytes,
                frozenset(settings.oauth_embedded_cimd_allowed_origins),
            )
            clients.metadata_ttl_seconds = settings.oauth_jwks_cache_ttl_seconds

    def register_routes(self, app: FastAPI) -> None:
        app.add_exception_handler(OAuthRateLimitError, rate_limit_response)
        app.add_exception_handler(McpAccessError, unavailable_response)
        routes = [
            ("/.well-known/oauth-authorization-server" + self.path, self.metadata, ["GET"]),
            (self.path + "/.well-known/openid-configuration", self.metadata, ["GET"]),
            (self.path + "/oauth/jwks", self.jwks, ["GET"]),
            (self.path + "/oauth/authorize", self.authorize, ["GET"]),
            (self.path + "/oauth/login", self.login, ["GET", "POST"]),
            (self.path + "/oauth/consent", self.consent_endpoint, ["GET", "POST"]),
            (self.path + "/oauth/token", self.token, ["POST"]),
            (self.path + "/oauth/revoke", self.revoke, ["POST"]),
        ]

        if self.clients.dcr_enabled:
            routes.append((self.path + "/oauth/register", self.register, ["POST"]))

        for path, endpoint, methods in routes:
            if any(getattr(route, "path", "") == path for route in app.routes) or (
                self.settings.admin_enabled
                and (
                    self.settings.admin_path == path
                    or self.settings.admin_path.startswith(path + "/")
                )
            ):
                raise GatewayConfigurationError("OAuth route collision")

            app.add_api_route(path, endpoint, methods=methods, include_in_schema=False)

    async def metadata(self, request: Request) -> Response:
        self.limits.metadata.enforce(self.address(request))

        data = {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/oauth/authorize",
            "token_endpoint": self.issuer + "/oauth/token",
            "jwks_uri": self.issuer + "/oauth/jwks",
            "revocation_endpoint": self.issuer + "/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
            "scopes_supported": ["openid", *self.settings.oauth_supported_scopes],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": self.clients.metadata_resolver is not None,
        }

        if self.clients.dcr_enabled:
            data["registration_endpoint"] = self.issuer + "/oauth/register"

        return JSONResponse(data, headers=HEADERS)

    async def jwks(self, request: Request) -> Response:
        self.limits.metadata.enforce(self.address(request))

        return JSONResponse({"keys": [self.key.jwk]}, headers=HEADERS)

    @staticmethod
    def error(error: str = "invalid_request", status: int = 400) -> Response:
        return JSONResponse({"error": error}, status_code=status, headers=HEADERS)

    @staticmethod
    def cookie(request: Request, name: str) -> str:
        headers = request.headers.getlist("cookie")

        if sum(map(len, headers)) > 8192:
            return ""

        values = [
            part.partition("=")[2].strip()
            for header in headers
            for part in header.split(";")
            if part.partition("=")[0].strip() == name
        ]
        return values[0] if len(values) == 1 else ""

    def set_cookie(self, response: Response, name: str, value: str, ttl: int) -> None:
        response.set_cookie(
            name,
            value,
            max_age=ttl,
            path=self.cookie_path,
            secure=True,
            httponly=True,
            samesite="lax",
        )

    @staticmethod
    def address(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @staticmethod
    async def body(request: Request) -> bytes:
        data = bytearray()

        async for chunk in request.stream():
            if len(data) + len(chunk) > 16384:
                raise ValueError("Request too large")

            data.extend(chunk)

        return bytes(data)

    async def form(self, request: Request) -> dict[str, str]:
        if (
            request.headers.get("content-type", "").split(";")[0]
            != "application/x-www-form-urlencoded"
        ):
            raise ValueError("Expected form")

        pairs = parse_qsl(
            (await self.body(request)).decode(), keep_blank_values=True, max_num_fields=20
        )

        if len(dict(pairs)) != len(pairs):
            raise ValueError("Duplicate field")

        return dict(pairs)

    def page(self, title: str, body: str, status: int = 200) -> Response:
        return HTMLResponse(
            self.pages.render(self.settings.app_name, title, body),
            status_code=status,
            headers={**HEADERS, "Referrer-Policy": "same-origin"},
        )

    def expired_authorization(self, status: int) -> Response:
        return self.page(
            "Sign-in expired",
            "<p>Return to the application or MCP client and start sign-in again.</p>",
            status,
        )

    def login_form(self, message: str = "", status: int = 200) -> Response:
        csrf = secrets.token_urlsafe(32)
        registration = (
            '<button class="secondary" name="action" value="register">Create account</button>'
            if self.identity.registration_enabled
            else '<p class="registration-note">Account creation is disabled. '
            "Contact the server operator for access.</p>"
        )
        error = '<p class="notice" role="alert">' + html.escape(message) + "</p>" if message else ""
        response = self.page(
            "Sign in",
            "<p>Continue with your account.</p>"
            + error
            + '<form method="post"><input type="hidden" name="csrf" value="'
            + csrf
            + (
                '"><div class="field"><label for="username">Username</label>'
                '<input id="username" name="username" autocomplete="username" required '
                'aria-describedby="username-hint" minlength="3" maxlength="64" '
                'pattern="[A-Za-z0-9_.\\-]{3,64}">'
                '<small class="hint" id="username-hint">3-64 characters: letters, numbers, '
                "dots, underscores or hyphens.</small></div>"
                '<div class="field"><label for="password">Password</label>'
                '<input id="password" name="password" type="password" '
                'autocomplete="current-password" required minlength="12" maxlength="256" '
                'aria-describedby="password-hint">'
                '<small class="hint" id="password-hint">12-256 characters.</small></div>'
                '<div class="actions">'
                '<button name="action" value="login">Sign in</button>'
            )
            + registration
            + "</div></form>",
            status,
        )
        self.set_cookie(
            response, "oauth_csrf", csrf, self.settings.oauth_embedded_authorization_ttl_seconds
        )
        return response

    async def authorize(self, request: Request) -> Response:
        self.limits.browser.enforce(self.address(request))

        params = dict(request.query_params)

        if len(request.scope.get("query_string", b"")) > 8192 or any(
            len(request.query_params.getlist(k)) != 1 for k in params
        ):
            return self.error()

        self.limits.client.enforce(params.get("client_id", ""))
        client = await self.clients.get(params.get("client_id", ""))

        if client is None or params.get("redirect_uri") not in client["redirect_uris"]:
            return self.error("unauthorized_client")

        scopes = set(params.get("scope", " ".join(self.settings.oauth_required_scopes)).split())
        browser = client["browser"]
        resource = params.get("resource", "")

        if (
            params.get("response_type") != "code"
            or params.get("code_challenge_method") != "S256"
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", ""))
            or len(params.get("state", "")) > 2048
            or (browser and not params.get("nonce"))
        ):
            return self.error()

        if not scopes or not scopes <= (
            {"openid"} if browser else {"openid", *self.settings.oauth_supported_scopes}
        ):
            return self.error("invalid_scope")

        if resource != ("" if browser else self.resource):
            return self.error("invalid_target")

        try:
            transaction = await self.store.put(
                "transaction",
                {**params, "scopes": sorted(scopes), "browser": browser},
                self.settings.oauth_embedded_authorization_ttl_seconds,
            )
        except ValueError:
            return self.error("temporarily_unavailable", 503)

        logged = await self.store.get("login", self.cookie(request, "oauth_login"))
        response = RedirectResponse(
            self.issuer + ("/oauth/consent" if logged else "/oauth/login"),
            status_code=303,
            headers=HEADERS,
        )
        self.set_cookie(
            response,
            "oauth_transaction",
            transaction,
            self.settings.oauth_embedded_authorization_ttl_seconds,
        )
        return response

    async def login(self, request: Request) -> Response:
        self.limits.browser.enforce(self.address(request))

        transaction = await self.store.get("transaction", self.cookie(request, "oauth_transaction"))

        if transaction is None:
            return self.expired_authorization(400)

        if request.method == "GET":
            return self.login_form()

        try:
            form = await self.form(request)
        except (ValueError, UnicodeError):
            return self.error()

        csrf = self.cookie(request, "oauth_csrf")

        if (
            request.headers.get("origin") != self.origin
            or not csrf
            or not secrets.compare_digest(csrf.encode(), form.get("csrf", "").encode())
            or form.get("action") not in ("login", "register")
        ):
            return self.error("access_denied", 403)

        if form["action"] == "register" and not self.identity.registration_enabled:
            return self.login_form("Account creation is disabled.", 403)

        account = hashlib.sha256(form.get("username", "").casefold().encode()).hexdigest()
        self.limits.login.enforce(account)
        subject = await self.identity.authenticate(
            form.get("username", ""), form.get("password", ""), form["action"] == "register"
        )

        if subject is None:
            return self.login_form(
                "Sign-in or account creation failed. Check your credentials.", 403
            )

        try:
            session = await self.store.put(
                "login", {"subject": subject}, self.settings.oauth_embedded_access_token_ttl_seconds
            )
        except ValueError:
            return self.error("temporarily_unavailable", 503)

        old = self.cookie(request, "oauth_login")
        await self.store.remove("login", old)
        response = RedirectResponse(
            self.issuer + "/oauth/consent", status_code=303, headers=HEADERS
        )
        self.set_cookie(
            response, "oauth_login", session, self.settings.oauth_embedded_access_token_ttl_seconds
        )
        response.delete_cookie(
            "oauth_csrf", path=self.cookie_path, secure=True, httponly=True, samesite="lax"
        )
        return response

    async def consent_endpoint(self, request: Request) -> Response:
        self.limits.browser.enforce(self.address(request))

        transaction_key = self.cookie(request, "oauth_transaction")
        transaction = await self.store.get("transaction", transaction_key)
        logged = await self.store.get("login", self.cookie(request, "oauth_login"))

        if transaction is None or logged is None:
            return self.expired_authorization(403)

        self.limits.principal.enforce(logged["subject"])
        self.limits.client.enforce(transaction["client_id"])
        client = await self.clients.get(transaction["client_id"])

        if client is None:
            return self.error("unauthorized_client")

        if request.method == "GET":
            nonce = secrets.token_urlsafe(32)
            body = (
                "<p><strong>"
                + html.escape(client["name"])
                + "</strong> would like to access "
                + html.escape("your identity" if client["browser"] else "your game channel")
                + '.</p><p class="permissions">Permissions: '
                + html.escape(" ".join(transaction["scopes"]))
                + '</p><form method="post"><input type="hidden" name="csrf" value="'
                + nonce
                + (
                    '"><div class="actions"><button name="action" value="allow">Allow</button>'
                    '<button class="secondary" name="action" value="deny">Deny</button>'
                    "</div></form><details><summary>Connection details</summary><dl>"
                )
                + "<dt>Client ID</dt><dd>"
                + html.escape(client["client_id"])
                + "</dd><dt>Resource</dt><dd>"
                + html.escape(transaction.get("resource") or "your browser identity")
                + "</dd><dt>Redirect</dt><dd>"
                + html.escape(transaction["redirect_uri"])
                + "</dd></dl></details>"
            )
            response = self.page("Authorize access", body)
            self.set_cookie(
                response,
                "oauth_csrf",
                nonce,
                self.settings.oauth_embedded_authorization_ttl_seconds,
            )
            return response

        try:
            form = await self.form(request)
        except (ValueError, UnicodeError):
            return self.error()

        csrf = self.cookie(request, "oauth_csrf")

        if (
            request.headers.get("origin") != self.origin
            or not csrf
            or not secrets.compare_digest(csrf.encode(), form.get("csrf", "").encode())
            or form.get("action") not in ("allow", "deny")
        ):
            return self.error("access_denied", 403)

        transaction = await self.store.get("transaction", transaction_key, consume=True)

        if transaction is None:
            return self.error("invalid_grant")

        approved = form["action"] == "allow" and (
            client["browser"]
            or await self.consent.approve(
                logged["subject"],
                client["client_id"],
                transaction.get("resource", ""),
                frozenset(transaction["scopes"]),
            )
        )
        params = {"iss": self.issuer}

        if "state" in transaction:
            params["state"] = transaction["state"]

        if approved:
            try:
                params["code"] = await self.store.put(
                    "code",
                    {
                        **transaction,
                        "subject": logged["subject"],
                        "binding": ""
                        if client["browser"]
                        else await self.consent.binding(
                            logged["subject"], client["client_id"], transaction.get("resource", "")
                        ),
                    },
                    self.settings.oauth_embedded_auth_code_ttl_seconds,
                )
            except ValueError:
                return self.error("temporarily_unavailable", 503)
        else:
            params["error"] = "access_denied"

        parts = urlsplit(transaction["redirect_uri"])
        query = urlencode([*parse_qsl(parts.query, keep_blank_values=True), *params.items()])
        response = RedirectResponse(
            urlunsplit((parts.scheme, parts.netloc, parts.path, query, "")),
            status_code=303,
            headers=HEADERS,
        )
        response.delete_cookie(
            "oauth_transaction", path=self.cookie_path, secure=True, httponly=True, samesite="lax"
        )
        response.delete_cookie(
            "oauth_csrf", path=self.cookie_path, secure=True, httponly=True, samesite="lax"
        )
        return response

    async def authenticate_client(self, request: Request, form: dict) -> dict | None:
        if "client_assertion" in form or "client_assertion_type" in form:
            return None

        headers = request.headers.getlist("authorization")
        client_id = form.get("client_id", "")
        secret = ""

        if headers:
            if (
                len(headers) != 1
                or headers[0][:6].casefold() != "basic "
                or len(headers[0]) > 2048
                or "client_secret" in form
            ):
                return None

            try:
                client_id, secret = (
                    base64.b64decode(headers[0][6:], validate=True).decode().split(":", 1)
                )
                client_id, secret = unquote_plus(client_id), unquote_plus(secret)
            except (ValueError, UnicodeError):
                return None

            if form.get("client_id", client_id) != client_id:
                return None
        elif "client_secret" in form:
            return None

        client = await self.clients.get(client_id)

        if (
            client is None
            or (
                client["method"] == "client_secret_basic"
                and (
                    not headers
                    or not secrets.compare_digest(secret.encode(), client["secret"].encode())
                )
            )
            or (client["method"] == "none" and bool(headers))
        ):
            return None

        return client

    async def token(self, request: Request) -> Response:
        self.limits.token.enforce(self.address(request))

        try:
            form = await self.form(request)
            client = await self.authenticate_client(request, form)

            if client is None:
                return self.error("invalid_client", 401)

            self.limits.client.enforce(client["client_id"])
            kind = "code" if form.get("grant_type") == "authorization_code" else "refresh"
            record = await self.store.get(
                kind, form.get(kind if kind == "code" else "refresh_token", "")
            )

            if record is not None and record["client_id"] == client["client_id"]:
                self.limits.principal.enforce(record["subject"])

            result = await self.exchange(client, form)
        except (ValueError, UnicodeError):
            return self.error("invalid_grant")

        return JSONResponse(result, headers=HEADERS) if result else self.error("invalid_grant")

    async def exchange(self, client: dict, form: dict) -> dict | None:
        resource = form.get("resource", "")
        scopes = set(form.get("scope", "").split())

        if form.get("grant_type") not in client["grant_types"] or resource != (
            "" if client["browser"] else self.resource
        ):
            return None

        if form["grant_type"] == "authorization_code":
            code = await self.store.get("code", form.get("code", ""), consume=True)
            verifier = form.get("code_verifier", "")

            if (
                code is None
                or code["client_id"] != client["client_id"]
                or code["redirect_uri"] != form.get("redirect_uri")
                or code.get("resource", "") != resource
                or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
            ):
                return None

            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )

            if not secrets.compare_digest(challenge, code["code_challenge"]) or (
                scopes and not scopes <= set(code["scopes"])
            ):
                return None

            scopes = scopes or set(code["scopes"])
            family = await self.store.put(
                "family",
                {"subject": code["subject"], "client_id": client["client_id"]},
                self.settings.oauth_embedded_refresh_token_ttl_seconds,
            )
            record = {
                "family": family,
                "subject": code["subject"],
                "client_id": client["client_id"],
                "resource": resource,
                "scopes": sorted(scopes),
                "used": False,
                "nonce": code.get("nonce", ""),
                "binding": code["binding"],
            }
            refresh = (
                await self.store.put(
                    "refresh", record, self.settings.oauth_embedded_refresh_token_ttl_seconds
                )
                if "refresh_token" in client["grant_types"]
                else ""
            )
        else:
            current = await self.store.get("refresh", form.get("refresh_token", ""))

            if current is None:
                return None

            rotated = await self.store.rotate(
                form["refresh_token"],
                client["client_id"],
                resource,
                scopes or set(current["scopes"]),
            )

            if rotated is None:
                return None

            record, refresh = rotated
            scopes = set(record["scopes"])

        if not client["browser"] and (
            record["binding"]
            != await self.consent.binding(record["subject"], client["client_id"], resource)
            or not await self.consent.validate(
                record["subject"], client["client_id"], resource, frozenset(scopes)
            )
        ):
            await self.store.remove("family", record["family"])
            return None

        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": record["subject"],
            "client_id": client["client_id"],
            "aud": resource or client["client_id"],
            "iat": now,
            "exp": now + self.settings.oauth_embedded_access_token_ttl_seconds,
            "scope": " ".join(sorted(scopes)),
            "family": record["family"],
            "jti": secrets.token_urlsafe(24),
        }
        result = {
            "access_token": self.key.encode(claims),
            "token_type": "Bearer",
            "expires_in": self.settings.oauth_embedded_access_token_ttl_seconds,
            "scope": claims["scope"],
        }

        if refresh:
            result["refresh_token"] = refresh

        if "openid" in scopes:
            identity = {**claims, "aud": client["client_id"]}

            if record["nonce"]:
                identity["nonce"] = record["nonce"]

            result["id_token"] = self.key.encode(identity, "JWT")

        return result

    async def verify(self, raw_token: str, expected_resource: str) -> VerifiedPrincipal | None:
        try:
            claims = self.key.decode(raw_token, self.issuer, expected_resource)
            principal = principal_from_claims(claims, self.issuer, expected_resource)

            if principal is None or not isinstance(claims.get("family"), str):
                return None

            family = await self.store.get("family", claims["family"])
            return (
                principal
                if family is not None
                and family["subject"] == principal.subject
                and family["client_id"] == principal.client_id
                else None
            )
        except (jwt.PyJWTError, ValueError, TypeError, RecursionError):
            return None

    async def revoke(self, request: Request) -> Response:
        self.limits.token.enforce(self.address(request))

        try:
            form = await self.form(request)
            client = await self.authenticate_client(request, form)

            if client is None:
                return self.error("invalid_client", 401)

            self.limits.client.enforce(client["client_id"])
            record = await self.store.get("refresh", form.get("token", ""))

            if record is None:
                try:
                    claims = self.key.decode(form.get("token", ""), self.issuer, self.resource)
                    record = {
                        "client_id": claims["client_id"],
                        "subject": claims["sub"],
                        "family": claims["family"],
                    }
                except (jwt.PyJWTError, ValueError, KeyError):
                    record = {}

            if record.get("client_id") == client["client_id"]:
                self.limits.principal.enforce(record["subject"])
                await self.store.remove("family", record["family"])
        except (ValueError, UnicodeError):
            return self.error()

        return Response(status_code=200, headers=HEADERS)

    async def register(self, request: Request) -> Response:
        self.limits.registration.enforce(self.address(request))

        try:
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                raise ValueError("Expected JSON")

            data = json.loads(await self.body(request))

            if not isinstance(data, dict):
                raise ValueError("Invalid metadata")

            client = await self.clients.register(data)
        except (ValueError, TypeError, RecursionError):
            return self.error("invalid_client_metadata")

        return JSONResponse(client, status_code=201, headers=HEADERS)
