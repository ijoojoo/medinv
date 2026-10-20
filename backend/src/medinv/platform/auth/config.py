from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MED_", env_file=".env", extra="ignore")
    environment: Literal["development", "test", "production"] = "production"
    control_auth_database_url: SecretStr
    public_origin: str
    oidc_issuer_url: str
    oidc_client_id: str = "medinv-web"
    oidc_client_secret: SecretStr
    session_encryption_key: SecretStr
    session_seconds: int = Field(default=28800, ge=300, le=86400)

    @model_validator(mode="after")
    def secure_urls(self) -> AuthSettings:
        for value in (self.public_origin, self.oidc_issuer_url):
            url = urlsplit(value)
            if url.username or url.password or url.query or url.fragment or value.endswith("/"):
                raise ValueError("Canonical origin and issuer URLs are required")
            if url.scheme != "https":
                if (
                    self.environment == "production"
                    or url.scheme != "http"
                    or url.hostname not in {"127.0.0.1", "localhost"}
                ):
                    raise ValueError("HTTPS required outside loopback development")
        if urlsplit(self.public_origin).path:
            raise ValueError("public_origin cannot contain a path")
        return self

    @property
    def secure_cookie(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def callback_url(self) -> str:
        return self.public_origin + "/auth/callback"
