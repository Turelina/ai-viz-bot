"""Application configuration loaded from the .env file."""
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

    # Google Gemini (NanaBananaPro) — optional
    gemini_api_key: str = Field(default="", description="Google Gemini API key for image generation")
    gemini_proxy: str = Field(default="", description="HTTP proxy for the Gemini API when geo-blocked")

    # Payment details
    payment_card: str = Field(default="Bank card: 1234 5678 9012 3456", description="Card details for payment")
    payment_recipient: str = Field(default="Payment Recipient", description="Payment recipient full name")
    payment_phone: str = Field(default="", description="Recipient phone number (optional)")

    # Prices by order category
    base_price_image: int = Field(default=500, description="Base price for all other requests, RUB")
    price_exterior: int = Field(default=1500, description="Exterior, facade, or rendering price, RUB")
    price_interior: int = Field(default=1000, description="Interior or room price, RUB")

    # Environment
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")


settings = Settings()
