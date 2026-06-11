from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""

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

    # Rate-limit defaults for auth endpoints. Strings use SlowAPI syntax
    # ("<count>/<window>"), e.g. "10/minute", "100/hour", "5/second".
    # Per-IP keying for unauthenticated endpoints, per-user for
    # authenticated ones.
    rate_limit_enabled: bool = True
    rate_limit_token: str = "20/minute"
    rate_limit_refresh: str = "30/minute"
    rate_limit_dev_token: str = "5/minute"
    rate_limit_payments: str = "60/minute"

    # Environment settings
    env: str = "development"

    # Token encryption
    token_encryption_key: str  # Fernet key for encrypting OAuth tokens at rest

    # Api base url - Let Pydantic handle this
    api_base_url: str = "http://localhost:8000"  # Default value

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

    # ``extra="ignore"`` so leftover environment variables (e.g. the retired
    # KEYCLOAK_* / SSL_* keys still present in deployment configs during the
    # Firebase migration) don't crash startup. Remove them from the Helm
    # values once the rollout is complete.
    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()
