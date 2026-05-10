import os
from functools import lru_cache

import urllib3
from keycloak import KeycloakOpenID
from pydantic import Field
from pydantic_settings import BaseSettings

from app.config.logger_config import logger


class Settings(BaseSettings):
    """Application settings"""

    # Keycloak settings
    keycloak_server_url: str
    keycloak_client_id: str
    keycloak_client_secret: str
    keycloak_realm: str

    # BBB API settings
    bbb_server_base_url: str
    bbb_secret: str
    plugin_manifests_url: str

    # Broadcaster service settings
    broadcaster_api_url: str = Field(..., alias="BROADCASTER_API_URL")
    broadcaster_api_timeout: int = Field(15, alias="BROADCASTER_API_TIMEOUT")

    # Twitch IRC settings
    twitch_server: str
    twitch_port: int
    twitch_nick: str
    twitch_channel: str

    # Twitch OAuth credentials flow settings
    twitch_redirect_uri: str
    twitch_client_id: str
    twitch_client_secret: str
    twitch_token_url: str

    # YouTube OAuth settings
    youtube_client_id: str
    youtube_client_secret: str
    youtube_redirect_uri: str

    # Facebook OAuth settings
    facebook_app_id: str
    facebook_app_secret: str
    facebook_redirect_uri: str

    # Database settings
    db_url: str
    # Connection pool tuning for the async SQLAlchemy engine. Defaults are
    # sized for a single web worker handling moderate concurrency; raise
    # `db_pool_size` if a worker's connection acquisition becomes the
    # bottleneck (visible as p99 latency spikes under load).
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_pre_ping: bool = True
    # SQL statement echo. Off by default everywhere — turn on with
    # DB_ECHO=true only when you actively want to debug a query.
    db_echo: bool = False

    # Environment settings
    env: str = "development"

    # Token encryption
    token_encryption_key: str  # Fernet key for encrypting OAuth tokens at rest

    # SSL verification for Keycloak HTTPS calls.
    # Production: leave `ssl_verify=True` (default). Either rely on the
    # system CA trust store (works for public CAs like Let's Encrypt) or
    # set `ssl_cert_file` to a custom CA bundle for private / self-signed
    # CAs. NEVER set `ssl_verify=False` in production — every Keycloak
    # call (including admin credential exchange) becomes MITM-vulnerable.
    ssl_verify: bool = True
    ssl_cert_file: str | None = None

    # Api base url - Let Pydantic handle this
    api_base_url: str = "http://localhost:8000"  # Default value

    # Admin credentials for Keycloak
    keycloak_admin_username: str = "admin"
    keycloak_admin_password: str = "admin"

    domain: str = "localhost"

    redis_url: str = "redis://localhost:6379/0"

    cache_ttl_short: int = 300  # 5 minutes
    cache_ttl_medium: int = 1800  # 30 minutes
    cache_ttl_long: int = 3600  # 1 hour
    cache_ttl_user: int = 900  # 15 minutes
    cache_ttl_bbb: int = 180  # 3 minutes (BBB data changes frequently)

    # Chat Gateway settings
    chat_gateway_url: str = "http://localhost:8081"
    chat_gateway_shared_secret: str

    # Stripe settings
    stripe_secret_key: str
    stripe_publishable_key: str
    stripe_webhook_secret: str
    stripe_free_price_id: str = ""  # Optional, for Free plan (if you create one in Stripe)
    stripe_pro_price_id: str = ""  # Will be configured from Stripe dashboard
    stripe_enterprise_price_id: str = ""  # Will be configured from Stripe dashboard

    # CORS settings - comma-separated list of allowed origins
    cors_origins: str
    # CORS regex pattern for dynamic origins (e.g., BBB instances)
    cors_origin_regex: str = ""  # Optional regex pattern

    frontend_url: str = "http://localhost:3000"

    # Firebase (push notifications)
    firebase_service_account_base64: str | None = None

    # SMTP (email notifications)
    smtp_host: str = "smtp-relay.brevo.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "no-reply@localhost"
    smtp_from_name: str = "bluescale"
    smtp_use_starttls: bool = True

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()


def resolve_ssl_verify(s: Settings) -> str | bool:
    """Resolve the `verify=` argument for HTTP clients hitting Keycloak.

    Returns:
        - ``False``: verification disabled — only when ``ssl_verify=False``
          is set explicitly. Suitable for local dev only.
        - ``str``  : path to a CA bundle for private / self-signed CAs.
        - ``True`` : verify against the system CA trust store (the right
          choice when Keycloak uses a public CA like Let's Encrypt).

    Raises:
        RuntimeError: ``ssl_cert_file`` is set but the file is missing.
            That's a deployment misconfiguration, not something to silently
            fall back from — silent fallback was the original CVE.
    """
    if not s.ssl_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        logger.warning(
            "SSL verification is DISABLED for Keycloak HTTPS calls. "
            "Every connection is MITM-vulnerable. Use only in local development."
        )
        return False

    # Treat empty string as unset — the `.env.example` template uses
    # `SSL_CERT_FILE=` (empty) as the "no custom CA" placeholder.
    cert_file = (s.ssl_cert_file or "").strip()
    if cert_file:
        if not os.path.exists(cert_file):
            raise RuntimeError(
                f"ssl_cert_file is set to {cert_file!r} but the file does not "
                "exist. Fix the path / cert volume mount, or unset ssl_cert_file "
                "to use the system CA trust store."
            )
        logger.info(f"Using custom SSL CA bundle: {cert_file}")
        return cert_file

    logger.info("Using system CA trust store for Keycloak HTTPS verification")
    return True


verify_ssl: str | bool = resolve_ssl_verify(settings)


@lru_cache
def get_keycloak_openid() -> KeycloakOpenID:
    """Lazily build the shared KeycloakOpenID client.

    Previously the client was constructed at module import time, which
    meant any test (or one-off script) that imported ``settings`` needed
    a reachable Keycloak server. With ``@lru_cache`` the constructor
    runs only on first call, and tests can swap the factory via
    ``monkeypatch.setattr(auth_module, "get_keycloak_openid", ...)``.
    """
    s = get_settings()
    return KeycloakOpenID(
        server_url=s.keycloak_server_url,
        client_id=s.keycloak_client_id,
        realm_name=s.keycloak_realm,
        client_secret_key=s.keycloak_client_secret,
        verify=resolve_ssl_verify(s),
    )
