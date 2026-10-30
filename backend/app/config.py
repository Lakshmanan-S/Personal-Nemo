"""
Centralized application configuration.

All runtime configuration (API keys, model IDs, cost caps, OAuth credentials)
is loaded from environment variables / a local .env file via pydantic-settings.
Never hardcode secrets here.
"""
from functools import lru_cache
from enum import StrEnum
from urllib.parse import urlsplit
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Nebius Token Factory
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nemotron_nano_model: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    nemotron_super_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nemotron_ultra_model: str = "nvidia/Nemotron-3-Ultra-550b-a55b"
    daily_spend_cap_usd: float = 5.0
    gmail_nano_debug_output: bool = False
    tavily_api_key: str = Field(
        default="", repr=False,
        validation_alias=AliasChoices("TAVIL_API_KEY", "TAVILY_API_KEY", "tavily_api_key"),
    )

    # Security
    token_encryption_key: str = ""

    # Database
    database_url: str = "sqlite:///./persona_vault.db"

    # OAuth - Google
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/oauth/callback/gmail"
    google_calendar_redirect_uri: str = "http://localhost:8000/api/oauth/callback/gcal"

    # OAuth - Spotify
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8000/api/oauth/callback/spotify"

    # OAuth - Strava
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = "http://localhost:8000/api/oauth/callback/strava"

    # OAuth - Trakt
    trakt_client_id: str = ""
    trakt_client_secret: str = ""
    trakt_redirect_uri: str = "http://localhost:8000/api/oauth/callback/trakt"

    # MCP server
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8800

    # App
    app_env: str = "development"
    frontend_url: str = "http://localhost:5173"
    demo_user_email: str = "demo@personavault.local"
    public_demo: bool = False

    @field_validator("frontend_url")
    @classmethod
    def validate_frontend_origin(cls, value: str) -> str:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("FRONTEND_URL must be an HTTP(S) origin without a path, query or credentials.")
        return value.rstrip("/")

    @property
    def cors_origins(self) -> list[str]:
        origins = [self.frontend_url]
        if self.app_env == "development":
            origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])
        return list(dict.fromkeys(origins))

    @property
    def effective_database_url(self) -> str:
        return "sqlite:///./personal_nemo_public_demo.db" if self.public_demo else self.database_url


@lru_cache
def get_settings() -> Settings:
    """Settings are cached so the .env file is only parsed once per process."""
    return Settings()


class Category(StrEnum):
    FOOD = "food"
    MUSIC = "music"
    ENTERTAINMENT = "entertainment"
    FITNESS = "fitness"
    ROUTINE = "routine"
    PLACES_TRAVEL = "places_travel"
    STYLE_SHOPPING = "style_shopping"
    PERSONALITY = "personality"
    APPEARANCE = "appearance"  # user-entered only, never inferred


class Provider(StrEnum):
    GMAIL = "gmail"
    GCAL = "gcal"
    SPOTIFY = "spotify"
    STRAVA = "strava"
    TRAKT = "trakt"


class ItemType(StrEnum):
    EMAIL_RECEIPT = "email_receipt"
    EVENT = "event"
    TRACK = "track"
    ACTIVITY = "activity"
    WATCH = "watch"


class PrivacyTier(StrEnum):
    SHARE = "share"
    ASK = "ask"
    PRIVATE = "private"


class ModelSize(StrEnum):
    NANO = "nano"
    SUPER = "super"
    ULTRA = "ultra"


class ChatMode(StrEnum):
    PERSONALIZED = "personalized"
    BASELINE = "baseline"


class ConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    DEMO = "demo"
    ERROR = "error"


# User overrides always win over these category defaults.
DEFAULT_CATEGORY_TIER: dict[str, str] = {
    Category.FOOD: PrivacyTier.SHARE,
    Category.MUSIC: PrivacyTier.SHARE,
    Category.ENTERTAINMENT: PrivacyTier.SHARE,
    Category.FITNESS: PrivacyTier.ASK,
    Category.ROUTINE: PrivacyTier.PRIVATE,
    Category.PLACES_TRAVEL: PrivacyTier.ASK,
    Category.STYLE_SHOPPING: PrivacyTier.SHARE,
    Category.PERSONALITY: PrivacyTier.ASK,
    Category.APPEARANCE: PrivacyTier.ASK,
}

PROVIDER_ITEM_TYPE: dict[str, str] = {
    Provider.GMAIL: ItemType.EMAIL_RECEIPT,
    Provider.GCAL: ItemType.EVENT,
    Provider.SPOTIFY: ItemType.TRACK,
    Provider.STRAVA: ItemType.ACTIVITY,
    Provider.TRAKT: ItemType.WATCH,
}

