"""LLM pipeline skeleton with idempotency enabled."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import cast

import aws_lambda_powertools.utilities.idempotency as idempotency
from aws_lambda_powertools.utilities.idempotency import DynamoDBPersistenceLayer, IdempotencyConfig

from app.core.config import settings

IdempotentDecorator = Callable[
    [Callable[[str, Mapping[str, object] | None], dict[str, object]]],
    Callable[[str, Mapping[str, object] | None], dict[str, object]],
]
IdempotentFactory = Callable[..., IdempotentDecorator]

idempotent_factory: IdempotentFactory = cast(IdempotentFactory, idempotency.idempotent)
persistence_layer = DynamoDBPersistenceLayer(table_name=settings.DYNAMODB_TABLE_NAME)
idempotency_config = IdempotencyConfig(event_key_jmespath="request_id")
idempotent_wrapper: IdempotentDecorator = idempotent_factory(
    persistence_store=persistence_layer, config=idempotency_config
)


@idempotent_wrapper
def generate_node(
    request_id: str, payload: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Simulate a long-running LLM node generation step."""

    time.sleep(1)
    return {
        "request_id": request_id,
        "status": "generated",
        "payload": dict(payload) if payload else {},
    }
