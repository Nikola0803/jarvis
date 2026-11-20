from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, List
import yaml
from pydantic import BaseModel, Field, model_validator


class TelegramConfig(BaseModel):
    bot_token: str
    owner_user_id: int


class WhatsAppConfig(BaseModel):
    access_token: str
    phone_number_id: str
    verify_token: str
    app_secret: str = ""


class DiscordConfig(BaseModel):
    bot_token: str
    owner_id: Optional[int] = None


class SlackConfig(BaseModel):
    bot_token: str
    signing_secret: str


class GoogleAccountConfig(BaseModel):
    name: str = "primary"
    token_file: str = "/home/hermes/.hermes/google_token_primary.json"


class CalendarConfig(BaseModel):
    credentials_file: str = "/etc/hermes/google_credentials.json"
    accounts: List[GoogleAccountConfig] = Field(default_factory=lambda: [GoogleAccountConfig()])
    timezone: str = "Europe/Belgrade"


class GmailAccountConfig(BaseModel):
    name: str = "primary"
    email: str = ""
    token_file: str = "/home/hermes/.hermes/gmail_token_primary.json"
    priority: str = "medium"  # high / medium / low


class GmailConfig(BaseModel):
    credentials_file: str = "/etc/hermes/google_credentials.json"
    accounts: List[GmailAccountConfig] = Field(default_factory=list)
    max_unread_shown: int = 10


class AnthropicConfig(BaseModel):
    api_key: str
    model: str = "claude-sonnet-4-5"
    spend_cap_usd: float = 20.0


class DeepSeekConfig(BaseModel):
    api_key: str
    model: str = "deepseek-chat"


class GeminiConfig(BaseModel):
    api_key: str
    model: str = "gemini-2.0-flash"


class RouterConfig(BaseModel):
    default_provider: str = "deepseek"
    complex_provider: str = "claude"
    free_fallback: str = "gemini"
    free_tier_max_words: int = 8        # messages at or under this word count go to Gemini first
    short_message_max_tokens: int = 150
    claude_trigger_prefixes: list[str] = Field(
        default=["/code", "/analyze", "/plan", "/think", "/research"]
    )


class ElevenLabsConfig(BaseModel):
    api_key: str
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel -- swap to any ElevenLabs voice ID


class DatabaseConfig(BaseModel):
    path: str = "/home/hermes/.hermes/hermes.db"


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    secret_key: str = "change-me"


class Config(BaseModel):
    telegram: TelegramConfig
    whatsapp: Optional[WhatsAppConfig] = None
    discord: Optional[DiscordConfig] = None
    slack: Optional[SlackConfig] = None
    calendar: Optional[CalendarConfig] = None
    gmail: Optional[GmailConfig] = None
    anthropic: AnthropicConfig
    deepseek: DeepSeekConfig
    gemini: Optional[GeminiConfig] = None
    elevenlabs: Optional[ElevenLabsConfig] = None
    router: RouterConfig = RouterConfig()
    database: DatabaseConfig = DatabaseConfig()
    dashboard: DashboardConfig = DashboardConfig()
    server: dict = {}


def load_config(path: Optional[str] = None) -> Config:
    cfg_path = path or os.getenv("HERMES_CONFIG", "/etc/hermes/config.yaml")
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    return Config(**raw)
