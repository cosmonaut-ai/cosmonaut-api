from enum import Enum
from typing import Any

from pinecone import Pinecone
from pinecone.db_data import Index, SearchQuery, SearchResponse, UpsertResponse
from pydantic import BaseModel, ConfigDict

from app.core.config import settings

pc: Pinecone | None = None
index: Index | None = None
PINECONE_NAMESPACE = "default"


class EntityType(str, Enum):
  NODE_TEXT = "node_text"
  WORLD_FACT = "world_fact"
  BRANCH_FACT = "branch_fact"


class PineconeRecord(BaseModel):
  model_config = ConfigDict(extra="allow")

  id: str
  text: str
  entity_type: EntityType


def get_client() -> Pinecone:
  global pc
  if pc is None:
    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
  return pc


def get_index() -> Index:
  global index
  if index is None:
    if settings.PINECONE_INDEX is None:
      raise ValueError("PINECONE_INDEX is not set")
    index = get_client().Index(
      name=settings.PINECONE_INDEX,
      pool_threads=50,
      connection_pool_maxsize=50,
    )
  return index


def upsert_records(records: list[PineconeRecord]) -> UpsertResponse:
  index = get_index()
  return index.upsert_records(
    namespace=PINECONE_NAMESPACE, records=[record.model_dump(mode="json") for record in records]
  )


def search_vectors(
  query: str,
  top_k: int = 10,
  filter: dict[str, Any] | None = None,
) -> SearchResponse:
  index = get_index()
  return index.search(
    namespace=PINECONE_NAMESPACE,
    query=SearchQuery(inputs={"text": query}, top_k=top_k, filter=filter),
  )
