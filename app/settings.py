from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseModel):
    API_ID: int
    API_HASH: str
    PHONE: str
    BOT_TOKEN: Optional[str] = None
    CHANNELS: str
    TARGET_CHANNEL_ID: int
    TARGET_CHANNEL_ID_UNMONITORED: int
    FORWARD: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        nested_model_default_partial_update=True,
        env_nested_delimiter="__",
        extra="ignore",
    )

    telegram: TelegramSettings = Field()


settings = Settings()
