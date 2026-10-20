from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MED_", env_file=".env", extra="ignore")
    database_url: SecretStr
    control_database_url: SecretStr
    public_origin: str = "http://127.0.0.1:5173"
    inventory_commands_enabled: bool = False
    procurement_commands_enabled: bool = False
    sales_commands_enabled: bool = False
    settlement_commands_enabled: bool = False
    returns_commands_enabled: bool = False
    retail_commands_enabled: bool = False
    count_commands_enabled: bool = False
    quality_commands_enabled: bool = False
    transfer_commands_enabled: bool = False
    warehouse_commands_enabled: bool = False
    opening_commands_enabled: bool = False
    reservation_expiry_enabled: bool = False
    reservation_expiry_interval_seconds: int = Field(default=15, ge=1, le=300)
    reservation_expiry_batch_size: int = Field(default=25, ge=1, le=100)
    pool_size: int = Field(default=5, ge=1, le=50)
    pool_overflow: int = Field(default=5, ge=0, le=50)
    lock_timeout_ms: int = Field(default=1500, ge=100, le=10000)
    statement_timeout_ms: int = Field(default=5000, ge=1000, le=30000)
    reservation_ttl_seconds: int = Field(default=900, ge=60, le=3600)
