from typing import Literal

from pydantic import BaseModel


class SQSPayload(BaseModel):
  task_type: Literal["analyze_node", "generate_world", "generate_world_image"]


class AnalyzeNodePayload(SQSPayload):
  world_id: str
  node_id: str


class GenerateWorldPayload(SQSPayload):
  world_id: str


class GenerateWorldImagePayload(SQSPayload):
  world_id: str
