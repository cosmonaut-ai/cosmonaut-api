"""Application settings powered by pydantic-settings."""

from typing import ClassVar, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix="", extra="ignore")

    ENV: Literal["dev", "prod"] = Field(default="dev", description="Runtime environment label.")
    POWERTOOLS_SERVICE_NAME: str = Field(
        default="cosmonaut-api", description="Service name for AWS Powertools telemetry."
    )
    DYNAMODB_TABLE_NAME: str = Field(
        default="cosmonaut-dev", description="Primary DynamoDB table for application state."
    )
    GEMINI_API_KEY: str | None = Field(default=None, description="API key for Gemini access.")
    GEMINI_MODEL: str = Field(default="gemini-3.0-flash", description="Gemini model name.")
    GEMINI_TIMEOUT_S: int = Field(default=30, description="Client timeout in seconds.")
    PINECONE_API_KEY: str | None = Field(default=None, description="API key for Pinecone.")
    PINECONE_ENV: str | None = Field(
        default=None, description="Pinecone environment/region (e.g., us-east-1)."
    )
    PINECONE_PROJECT: str | None = Field(
        default=None, description="Pinecone project name/ID for index access."
    )
    PINECONE_INDEX: str | None = Field(
        default=None, description="Target Pinecone index for vector operations."
    )
    PINECONE_NAMESPACE: str = Field(
        default="default", description="Namespace for isolating vectors."
    )
    PINECONE_TOP_K: int = Field(default=10, description="Default top-k for similarity search.")
    PINECONE_EMBED_DIM: int = Field(default=1536, description="Embedding dimension for index.")


settings: Settings = Settings()
