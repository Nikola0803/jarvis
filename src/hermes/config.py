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
    bot_token: str                 # xoxb-... bot token
    app_token: str = ""            # xapp-... app-level token (Socket Mode)
    signing_secret: str = ""       # only needed for the old HTTP webhook mode


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
    model: str = "gemini-3.8-flash"


class GroqConfig(BaseModel):
    api_key: str
    # Groq free tier -- fast inference on open-source models.
    # NousResearch Hermes on Groq: nous-hermes-2-pro-llama-3-1-8b (if available)
    # Fastest free option: llama-3.1-8b-instant
    # Best free option:    llama-3.3-70b-versatile
    model: str = "llama-3.1-8b-instant"
    # Override base_url to use Together AI or any other OpenAI-compat endpoint.
    # Together AI (NousResearch Hermes): https://api.together.xyz/v1/chat/completions
    #   model: NousResearch/Hermes-3-Llama-3.1-70B-Turbo
    base_url: str = "https://api.groq.com/openai/v1/chat/completions"


class RouterConfig(BaseModel):
    default_provider: str = "deepseek"
    complex_provider: str = "claude"
    free_fallback: str = "gemini"
    free_tier_max_words: int = 8        # messages at or under this word count go to Gemini first
    short_message_max_tokens: int = 150
    history_limit: int = 10             # past exchanges replayed as conversation memory
    claude_trigger_prefixes: list[str] = Field(
        default=["/code", "/analyze", "/plan", "/think", "/research"]
    )
    # How many past exchanges to replay as conversation memory per message.
    history_limit: int = 10


class MemoryConfig(BaseModel):
    """Long-term 'facts brain' (Supermemory). Disabled until enabled + api_key set."""
    enabled: bool = False
    provider: str = "supermemory"
    api_key: str = ""
    base_url: str = "https://api.supermemory.ai"
    add_path: str = "/v3/documents"
    search_path: str = "/v4/search"
    search_limit: int = 5
    timeout_seconds: float = 6.0
    namespace: str = "hermes"


class ElevenLabsConfig(BaseModel):
    api_key: str
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel -- swap to any ElevenLabs voice ID


class HermesAgentConfig(BaseModel):
    """Nous Research's Hermes Agent as the brain, via its OpenAI-compatible API.
    When enabled, the HUD's voice/chat go to Hermes instead of the local router."""
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8642/v1"
    api_key: str = ""
    model: str = "hermes"
    timeout_seconds: float = 90.0


class CodexConfig(BaseModel):
    """OpenAI Codex CLI as ZERO's coding/design sub-agent. Uses your ChatGPT
    sign-in on the server (not the API), invoked via the code_task MCP tool.
    `allowed_repos` limits where Codex may run; `extra_args` tunes the CLI
    (e.g. a sandbox/approval flag) without code changes."""
    enabled: bool = False
    binary: str = "codex"
    default_repo: str = "/opt/jarvis"
    allowed_repos: List[str] = Field(default_factory=lambda: ["/opt/jarvis"])
    extra_args: List[str] = Field(default_factory=list)
    timeout_seconds: int = 900


class ClaudeCodeAccount(BaseModel):
    """One Claude Code login. `config_dir` is that account's CLAUDE_CONFIG_DIR,
    so two accounts stay isolated on the same machine."""
    name: str
    config_dir: str = ""


class ClaudeCodeConfig(BaseModel):
    """Claude Code CLI as a coding hand, alongside Codex. Runs `claude -p` in a
    repo under `allowed_repos`. Supports multiple accounts (pick with `account`)."""
    enabled: bool = False
    binary: str = "claude"
    default_repo: str = "/opt/jarvis"
    allowed_repos: List[str] = Field(default_factory=lambda: ["/opt/jarvis"])
    accounts: List[ClaudeCodeAccount] = Field(default_factory=list)
    extra_args: List[str] = Field(default_factory=lambda: ["--permission-mode", "acceptEdits"])
    timeout_seconds: int = 900


class GitHubConfig(BaseModel):
    """GitHub access for the PROJECTS panel and ZERO's repo tools. Read-only use;
    `token` is a personal access token (classic, `repo` scope) kept out of git."""
    token: str = ""
    username: str = ""                      # optional; the token's own user by default
    repos: List[str] = Field(default_factory=list)  # optional allowlist "owner/name"; empty = all the token sees
    max_shown: int = 8


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
    groq: Optional[GroqConfig] = None
    elevenlabs: Optional[ElevenLabsConfig] = None
    router: RouterConfig = RouterConfig()
    hermes_agent: Optional[HermesAgentConfig] = None
    memory: Optional[MemoryConfig] = None
    github: Optional[GitHubConfig] = None
    codex: Optional[CodexConfig] = None
    claude_code: Optional[ClaudeCodeConfig] = None
    database: DatabaseConfig = DatabaseConfig()
    dashboard: DashboardConfig = DashboardConfig()
    server: dict = {}


def load_config(path: Optional[str] = None) -> Config:
    cfg_path = path or os.getenv("HERMES_CONFIG", "/etc/hermes/config.yaml")
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)
    return Config(**raw)
