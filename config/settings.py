"""
Конфигурация приложения.
Читает переменные из .env файла.
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Supabase
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_key: str = Field(..., description="Supabase anon key")

    # Telegram
    telegram_bot_token: str = Field(..., description="Telegram bot token")
    telegram_admin_ids: str = Field(..., description="Comma-separated admin Telegram IDs")

    @property
    def admin_ids_list(self) -> List[int]:
        return [int(i.strip()) for i in self.telegram_admin_ids.split(",")]

    # Anthropic
    anthropic_api_key: str = Field(..., description="Anthropic Claude API key")

    # Цены
    base_price_image: int = Field(default=500, description="Базовая цена за изображение, руб")

    # Окружение
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")


settings = Settings()
