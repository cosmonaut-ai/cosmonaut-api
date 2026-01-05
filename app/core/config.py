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

  GOOGLE_CLIENT_SECRET_PARAM: str = Field(default="", description="Parameter store path for the Google client secret.")
  GEMINI_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Gemini API key.")
  PINECONE_API_KEY_PARAM: str = Field(default="", description="Parameter store path for the Pinecone API key.")


settings: Settings = Settings()
