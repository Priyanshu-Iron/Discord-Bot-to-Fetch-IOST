"""
Configuration module for IOST Wallet Monitor.
Loads environment variables using pydantic-settings.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    # Discord Configuration
    discord_bot_token: str
    discord_channel_id: int
    
    # IOST Configuration
    iost_wallet_address: str = "inwaliost"
    
    # Polling Configuration (25-30 seconds as specified)
    poll_interval_seconds: int = 25
    
    # Storage Configuration
    storage_file_path: str = "data/last_tx.json"
    
    # IOSTScan API Configuration
    iostscan_base_url: str = "https://www.iostscan.com"
    iostscan_api_timeout: int = 30
    
    # Retry Configuration
    max_retries: int = 3
    retry_delay_seconds: int = 5


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Using lru_cache ensures settings are only loaded once.
    """
    return Settings()
