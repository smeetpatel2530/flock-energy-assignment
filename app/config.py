from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    

    model_config = SettingsConfigDict(env_prefix="URJA_", env_file=".env", extra="ignore")

    portal_url: str = "https://urja-ops.flockenergy.tech"
    username: str
    password: SecretStr

    timeout_seconds: float = 15.0
    # Throttle requests to reduce portal rate limiting.
    min_request_interval_seconds: float = 0.5
    max_rate_limit_retries: int = 3
    # How long the bulk meter snapshot is reused before re-exporting from the portal.
    cache_ttl_seconds: float = 600.0
