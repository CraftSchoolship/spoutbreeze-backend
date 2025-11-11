from pydantic_settings import BaseSettings
from functools import lru_cache
from keycloak import KeycloakOpenID, KeycloakAdmin
import os
import urllib3
from typing import Union
from pydantic import Field


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

    # Database settings
    db_url: str

    # Environment settings
    env: str = "development"

    # SSL settings
    ssl_cert_file: str = "certs/keycloak.pem"
    ssl_verify: bool = True

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
    chat_gateway_url: str = "http://localhost:8800"
    chat_gateway_shared_secret: str

    # Stripe settings
    stripe_secret_key: str
    stripe_publishable_key: str
    stripe_webhook_secret: str
    stripe_free_price_id: str = ""  # Optional, for Free plan (if you create one in Stripe)
    stripe_pro_price_id: str = ""  # Will be configured from Stripe dashboard
    stripe_enterprise_price_id: str = ""  # Will be configured from Stripe dashboard

    model_config = {"env_file": ".env"}


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()

# Determine SSL verification method
cert_path = "/app/certs/keycloak.pem"
verify_ssl: Union[str, bool]
if os.path.exists(cert_path):
    # Use the certificate file if it exists
    verify_ssl = cert_path
    print(f"Using SSL certificate: {cert_path}")
else:
    # Disable SSL verification for development
    verify_ssl = False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Keycloak configuration
keycloak_openid = KeycloakOpenID(
    server_url=settings.keycloak_server_url,
    client_id=settings.keycloak_client_id,
    realm_name=settings.keycloak_realm,
    client_secret_key=settings.keycloak_client_secret,
    verify=verify_ssl,
)

keycloak_admin = KeycloakAdmin(
    server_url=settings.keycloak_server_url,
    realm_name=settings.keycloak_realm,
    client_id=settings.keycloak_client_id,
    client_secret_key=settings.keycloak_client_secret,
    verify=verify_ssl,
)

# Get OIDC config
# oidc_config = keycloak_openid.well_know()
# jwks_uri = oidc_config["jwks_uri"]
