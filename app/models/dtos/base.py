"""DTO base class and helpers for converting from Pynamo models."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ConfigDict

DTO = TypeVar("DTO", bound=BaseModel)


class DTOModel(BaseModel):
  """Base DTO with sensible defaults for API respon ses."""

  model_config = ConfigDict(populate_by_name=True)
