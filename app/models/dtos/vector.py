"""DTOs for Pinecone vector operations."""

from __future__ import annotations

from pydantic import Field

from app.models.dtos.base import DTOModel


class VectorMetadata(DTOModel):
  """Optional metadata stored alongside a vector."""

  # Allow arbitrary key/value metadata; validated as a mapping.
  model_config = {"arbitrary_types_allowed": True}


class VectorUpsertItem(DTOModel):
  """Single vector upsert payload."""

  id: str
  values: list[float] = Field(default_factory=list, description="Embedding values.")
  metadata: dict[str, object] | None = Field(default=None, description="Optional metadata to persist with the vector.")
  namespace: str | None = Field(default=None, description="Overrides default namespace.")


class VectorUpsertRequest(DTOModel):
  """Bulk upsert request."""

  items: list[VectorUpsertItem] = Field(default_factory=list)


class VectorUpsertResponse(DTOModel):
  """Result of an upsert operation."""

  upserted_count: int


class VectorQueryRequest(DTOModel):
  """Vector similarity query request."""

  embedding: list[float] = Field(default_factory=list, description="Query embedding.")
  top_k: int | None = Field(default=None, description="Override default top-k.")
  namespace: str | None = Field(default=None, description="Namespace to search.")


class VectorMatch(DTOModel):
  """Single match from a similarity search."""

  id: str
  score: float | None = None
  metadata: dict[str, object] | None = None


class VectorQueryResponse(DTOModel):
  """Query response with ordered matches."""

  matches: list[VectorMatch] = Field(default_factory=list)


class VectorDeleteRequest(DTOModel):
  """Delete vectors by id or namespace."""

  ids: list[str] | None = Field(default=None, description="IDs to delete.")
  delete_all: bool = Field(default=False, description="Delete all vectors in the namespace when true.")
  namespace: str | None = Field(default=None, description="Namespace to target.")


class VectorDeleteResponse(DTOModel):
  """Delete response metadata."""

  deleted_count: int | None = Field(default=None, description="Number of vectors removed when available.")
