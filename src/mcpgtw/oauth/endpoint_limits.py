from mcpgtw.config import GatewaySettings
from mcpgtw.oauth.rate_limit import OAuthRateLimitPolicy, WindowOAuthRateLimitPolicy


class OAuthEndpointLimits:
    policy_class: type[OAuthRateLimitPolicy] = WindowOAuthRateLimitPolicy

    def __init__(self, settings: GatewaySettings) -> None:
        def budget(requests: int) -> OAuthRateLimitPolicy:
            return self.policy_class(
                requests,
                settings.oauth_embedded_rate_limit_window_seconds,
                settings.oauth_rate_limit_maximum_keys,
                settings.oauth_rate_limit_backoff_seconds,
                settings.oauth_rate_limit_maximum_backoff_seconds,
            )

        self.browser = budget(settings.oauth_embedded_browser_requests)
        self.token = budget(settings.oauth_embedded_token_requests)
        self.registration = budget(settings.oauth_embedded_registration_requests)
        self.metadata = budget(settings.oauth_embedded_metadata_requests)
        self.cimd = budget(settings.oauth_embedded_cimd_requests)
        self.client = budget(settings.oauth_embedded_client_requests)
        self.principal = budget(settings.oauth_embedded_principal_requests)
        self.login = budget(settings.oauth_embedded_login_attempts)
