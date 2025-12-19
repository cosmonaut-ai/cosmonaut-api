"""DTO base class and helpers for converting from Pynamo models."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from app.models.pynamo.base import BaseCosmonautModel

DTO = TypeVar("DTO", bound=BaseModel)


class DTOModel(BaseModel):
    """Base DTO with sensible defaults for API responses."""

    model_config = ConfigDict(populate_by_name=True)


def to_dto(model: BaseCosmonautModel, dto_class: type[DTO]) -> DTO:
    """Convert a Pynamo model instance to a Pydantic DTO."""

    return dto_class.model_validate(model.attribute_values)
