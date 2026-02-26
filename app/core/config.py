"""Application settings powered by pydantic-settings."""

from typing import ClassVar, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  """Runtime configuration sourced from environment variables."""

  model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix="", extra="ignore")

  ENV: Literal["dev", "prod", "local"] = Field(default="dev", description="Runtime environment label.")
  POWERTOOLS_SERVICE_NAME: str = Field(
    default="cosmonaut-api", description="Service name for AWS Powertools telemetry."
  )
  DYNAMODB_TABLE_NAME: str = Field(default="cosmonaut-dev", description="Primary DynamoDB table for application state.")
  GEMINI_MODEL_SMALL: str = Field(default="gemini-flash-latest", description="Gemini model name for small tasks.")
  GEMINI_MODEL_LARGE: str = Field(default="gemini-pro-latest", description="Gemini model name for large tasks.")
  GEMINI_TIMEOUT_S: int = Field(default=30, description="Client timeout in seconds.")
  PINECONE_INDEX: str | None = Field(default=None, description="Target Pinecone index for vector operations.")
  MOCK_AUTH: bool = Field(default=False, description="If True, bypasses JWT validation (DEV ONLY).")
  COGNITO_USER_POOL_ID: str = Field(default="", description="AWS Cognito User Pool ID.")
  COGNITO_CLIENT_ID: str = Field(default="", description="AWS Cognito Client ID (Audience).")
  AWS_REGION: str = Field(default="us-east-2", description="AWS Region.")
  CORS_ORIGINS: list[str] = Field(
    default=[
      "http://localhost:5173",
      "https://cosmonaut-ai.com",
      "https://dev.cosmonaut-ai.com",
    ],
    description="Allowed CORS origins for cross-origin requests.",
  )

  SLOW_WORKER_QUEUE_URL: str = Field(default="", description="URL of the slow worker queue.")
  FAST_WORKER_QUEUE_URL: str = Field(default="", description="URL of the fast worker queue.")

  CLOUDFRONT_PRIVATE_KEY_PARAM: str = Field(
    default="", description="Parameter store path for the CloudFront private key."
  )
  CLOUDFRONT_KEY_PAIR_ID: str = Field(default="", description="CloudFront key pair ID.")
  COOKIE_DOMAIN: str = Field(default=".cosmonaut-ai.com", description="Cookie domain.")

  STATIC_CONTENT_S3_BUCKET: str = Field(default="", description="S3 bucket for static content (images, audio).")
  STATIC_CONTENT_CDN_DOMAIN: str = Field(default="", description="CloudFront domain for serving static content.")

  GCP_PROJECT_ID: str = Field(default="", description="GCP project ID for Vertex AI.")
  GCP_LOCATION: str = Field(default="us-central1", description="GCP region for Vertex AI.")
  GOOGLE_CLIENT_SECRET_PARAM: str = Field(default="", description="Parameter store path for the Google client secret.")
  PINECONE_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Pinecone API key.")
  ELEVENLABS_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the ElevenLabs API key.")

  # Stripe
  STRIPE_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Stripe API key.")
  STRIPE_WEBHOOK_SECRET_PARAM: str = Field(
    default="", description="Parameter store path for the Stripe webhook secret."
  )
  STRIPE_PRICE_EXPLORER: str = Field(default="", description="Stripe Price ID for the Explorer tier.")
  STRIPE_PRICE_COSMONAUT: str = Field(default="", description="Stripe Price ID for the Cosmonaut tier.")
  STRIPE_PORTAL_CONFIG_ID: str = Field(default="", description="Stripe Customer Portal configuration ID.")

  # Buttondown newsletter
  BUTTONDOWN_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Buttondown API key.")

  # SES Email
  SES_FROM_EMAIL: str = Field(
    default="", description="Verified SES sender address (e.g. Cosmonaut <noreply@cosmonaut-ai.com>)."
  )
  SES_ENABLED: bool = Field(default=False, description="Enable SES email sending. Disabled in local/dev by default.")

  # Frontend
  FRONTEND_DOMAIN: str = Field(default="dev.cosmonaut-ai.com", description="Public frontend domain for canonical URLs.")

  # Dev access control
  DEV_ALLOWED_EMAILS: list[str] = Field(
    default=["imatson9119@gmail.com", "ian@cosmonaut-ai.com"],
    description="Email allowlist for the dev environment. Only these emails may access the API when ENV=dev.",
  )


settings: Settings = Settings()

# ---------------------------------------------------------------------------
# Tier limits (not environment-dependent; kept outside Settings)
# ---------------------------------------------------------------------------
TIER_LIMITS: dict[str, dict[str, int]] = {
  "FREE": {"worlds": 3, "nodes": 30, "reset_days": 7, "saved_worlds": 5, "audio_limit": 20},
  "EXPLORER": {"worlds": 20, "nodes": 500, "reset_days": 30, "saved_worlds": 50, "audio_limit": 60},
  "COSMONAUT": {"worlds": 100, "nodes": 2000, "reset_days": 30, "saved_worlds": 100, "audio_limit": 200},
}


def get_tier_limits(tier: str) -> dict[str, int]:
  """Return the limits dict for the given tier, falling back to FREE if unknown."""
  return TIER_LIMITS.get(tier, TIER_LIMITS["FREE"])


# ---------------------------------------------------------------------------
# World length presets (max story depth per branch)
# ---------------------------------------------------------------------------
WORLD_LENGTH_MAX_NODES: dict[str, int] = {
  "short": 5,
  "medium": 10,
  "long": 15,
}

# Reverse lookup: Stripe Price ID -> tier name (populated from settings at import time)
PRICE_TO_TIER: dict[str, str] = {}


def _build_price_to_tier() -> None:
  """Populate PRICE_TO_TIER from settings once values are available."""
  if settings.STRIPE_PRICE_EXPLORER:
    PRICE_TO_TIER[settings.STRIPE_PRICE_EXPLORER] = "EXPLORER"
  if settings.STRIPE_PRICE_COSMONAUT:
    PRICE_TO_TIER[settings.STRIPE_PRICE_COSMONAUT] = "COSMONAUT"


_build_price_to_tier()
