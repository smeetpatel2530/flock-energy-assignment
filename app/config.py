from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_prefix="URJA_", env_file=".env", extra="ignore")

    portal_url: str = "https://urja-ops.flockenergy.tech"
    username: str
    password: SecretStr

    timeout_seconds: float = 15.0
    # The portal rate-limits bursts (HTTP 429, no Retry-After); ~1 req/1.5s was safe in testing.
    min_request_interval_seconds: float = 0.5
    max_rate_limit_retries: int = 3
    # How long the bulk meter snapshot is reused before re-exporting from the portal.
    cache_ttl_seconds: float = 600.0
