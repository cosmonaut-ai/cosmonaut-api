from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.services.secret_manager import get_secret_value

if TYPE_CHECKING:
  from pinecone import Pinecone
  from pinecone.db_data import Index, UpsertResponse
  from pinecone.db_data.models import SearchRecordsResponse
  from pinecone.db_data.types import FilterTypedDict

pc: Any | None = None
index: Any | None = None
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


class PineconeStoryFact(PineconeRecord):
  world_id: str


class PineconeWorldFact(PineconeStoryFact):
  pass


class PineconeBranchFact(PineconeStoryFact):
  origin_node_id: str


def get_client() -> "Pinecone":
  global pc
  if pc is None:
    from pinecone import Pinecone

    pc = Pinecone(api_key=get_secret_value(settings.PINECONE_API_KEY_PARAM))
  return pc


def get_index() -> "Index":
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


def upsert_records(records: list[PineconeRecord]) -> "UpsertResponse":
  index = get_index()
  return index.upsert_records(
    namespace=PINECONE_NAMESPACE, records=[record.model_dump(mode="json") for record in records]
  )


def search_records(
  query: str,
  top_k: int = 10,
  filter: dict[str, Any] | None = None,
) -> "SearchRecordsResponse":
  from pinecone.db_data import SearchQuery

  index = get_index()
  return index.search(
    namespace=PINECONE_NAMESPACE,
    query=SearchQuery(inputs={"text": query}, top_k=top_k, filter=filter),
  )


def delete_records(ids: list[str] | None = None, filter: "FilterTypedDict | None" = None) -> dict[str, Any]:
  index = get_index()
  return index.delete(
    ids=ids,
    namespace=PINECONE_NAMESPACE,
    filter=filter,
  )
