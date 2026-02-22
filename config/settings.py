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

    # Реквизиты оплаты
    payment_card: str = Field(default="Сбербанк: 1234 5678 9012 3456", description="Реквизиты карты для оплаты")
    payment_recipient: str = Field(default="Иванов И.И.", description="ФИО получателя платежа")
    payment_phone: str = Field(default="", description="Номер телефона получателя (необязательно)")

    # Цены по категориям заказа
    base_price_image: int = Field(default=500, description="Базовая цена (всё остальное), руб")
    price_exterior: int = Field(default=1500, description="Цена за экстерьер/фасад/рендеринг, руб")
    price_interior: int = Field(default=1000, description="Цена за интерьер/комнату, руб")

    # Окружение
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")


settings = Settings()
