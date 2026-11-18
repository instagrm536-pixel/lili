from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./zyx_media.db"
    bot_api_base_url: str = ""
    rate_limit_seconds: int = 5
    job_ttl_seconds: int = 3600
    max_direct_url_size_mb: int = 50
    worker_concurrency: int = 4
    public_base_url: str = ""
    qr_storage_dir: str = "./storage/qr"
    qr_max_item_size_mb: int = 20
    qr_max_total_size_mb: int = 100
    qr_session_ttl_seconds: int = 3600
    log_level: str = "INFO"
    environment: str = "production"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

settings = Settings()
