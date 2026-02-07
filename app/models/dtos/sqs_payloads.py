from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class TaskType(str, Enum):
  ANALYZE_NODE = "analyze_node"
  GENERATE_WORLD = "generate_world"
  GENERATE_WORLD_IMAGE = "generate_world_image"


class AnalyzeNodePayload(BaseModel):
  task_type: Literal[TaskType.ANALYZE_NODE] = TaskType.ANALYZE_NODE
  world_id: str
  node_id: str


class GenerateWorldPayload(BaseModel):
  task_type: Literal[TaskType.GENERATE_WORLD] = TaskType.GENERATE_WORLD
  world_id: str


class GenerateWorldImagePayload(BaseModel):
  task_type: Literal[TaskType.GENERATE_WORLD_IMAGE] = TaskType.GENERATE_WORLD_IMAGE
  world_id: str


SQSPayload = Annotated[
  Union[AnalyzeNodePayload, GenerateWorldPayload, GenerateWorldImagePayload],
  Field(discriminator="task_type"),
]
