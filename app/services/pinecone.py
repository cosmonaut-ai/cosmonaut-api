from typing import Any

from pinecone import Pinecone
from pinecone.db_data import Index, SearchQuery, SearchResponse, UpsertResponse

from app.core.config import settings

pc: Pinecone | None = None
index: Index | None = None


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


def upsert_records(namespace: str, records: list[Any]) -> UpsertResponse:
    index = get_index()
    return index.upsert_records(namespace=namespace, records=records)


def search_vectors(
    namespace: str,
    query: str,
    top_k: int = 10,
    filter: dict[str, Any] | None = None,
) -> SearchResponse:
    index = get_index()
    return index.search(
        namespace=namespace,
        query=SearchQuery(inputs={"text": query}, top_k=top_k, filter=filter),
    )
