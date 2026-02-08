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
  GEMINI_MODEL_SMALL: str = Field(default="gemini-3-flash-preview", description="Gemini model name for small tasks.")
  GEMINI_MODEL_LARGE: str = Field(default="gemini-3-pro-preview", description="Gemini model name for large tasks.")
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

  IMAGES_S3_BUCKET: str = Field(default="", description="S3 bucket for generated images.")
  IMAGES_CDN_DOMAIN: str = Field(default="", description="CloudFront domain for serving images.")

  GOOGLE_CLIENT_SECRET_PARAM: str = Field(default="", description="Parameter store path for the Google client secret.")
  GEMINI_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Gemini API key.")
  PINECONE_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Pinecone API key.")

  # Stripe
  STRIPE_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Stripe API key.")
  STRIPE_WEBHOOK_SECRET_PARAM: str = Field(
    default="", description="Parameter store path for the Stripe webhook secret."
  )
  STRIPE_PRICE_EXPLORER: str = Field(default="", description="Stripe Price ID for the Explorer tier.")
  STRIPE_PRICE_COSMONAUT: str = Field(default="", description="Stripe Price ID for the Cosmonaut tier.")
  STRIPE_PORTAL_CONFIG_ID: str = Field(default="", description="Stripe Customer Portal configuration ID.")

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
  "FREE": {"worlds": 3, "nodes": 30, "reset_days": 7, "saved_worlds": 5},
  "EXPLORER": {"worlds": 20, "nodes": 500, "reset_days": 30, "saved_worlds": 50},
  "COSMONAUT": {"worlds": 100, "nodes": 2000, "reset_days": 30, "saved_worlds": 100},
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
