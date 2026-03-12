"""LLM services package.

This package provides LLM agents for story generation.
All public symbols are re-exported here for backward compatibility.
"""

from app.services.llm.agents.fact_extraction import (
  FactExtractionDeps,
  LLMFactExtractionDeps,
  generate_facts_async,
)
from app.services.llm.agents.image_prompt import generate_image_prompt
from app.services.llm.agents.narrator import generate_narrator_profile
from app.services.llm.agents.next_node import (
  LLMNextNodeDeps,
  NextNodeDeps,
  build_user_message,
  get_next_node_agent,
)
from app.services.llm.agents.root_node import (
  LLMRootNodeDeps,
  RootNodeDeps,
  get_root_node_agent,
)
from app.services.llm.agents.world_info import generate_world_info
from app.services.llm.models import (
  LLMCharacter,
  LLMFactExtraction,
  LLMLocation,
  LLMNarratorProfile,
  LLMNodeMetadata,
  LLMStoryNode,
  LLMWorldInfo,
)
from app.services.llm.provider import (
  get_storytelling_model,
  get_utility_model,
  get_vertex_google_model,
  get_vertex_google_provider,
  get_world_building_model,
)
from app.services.llm.utils import format_model_with_descriptions

__all__ = [
  # Fact Extraction
  "FactExtractionDeps",
  # Models
  "LLMCharacter",
  "LLMFactExtraction",
  "LLMFactExtractionDeps",
  "LLMLocation",
  "LLMNarratorProfile",
  # Next Node
  "LLMNextNodeDeps",
  "LLMNodeMetadata",
  # Root Node
  "LLMRootNodeDeps",
  "LLMStoryNode",
  "LLMWorldInfo",
  "NextNodeDeps",
  "RootNodeDeps",
  "build_user_message",
  # Utils
  "format_model_with_descriptions",
  "generate_facts_async",
  # Image Prompt
  "generate_image_prompt",
  # Narrator
  "generate_narrator_profile",
  # World Info
  "generate_world_info",
  "get_next_node_agent",
  "get_root_node_agent",
  # Provider
  "get_storytelling_model",
  "get_utility_model",
  "get_vertex_google_model",
  "get_vertex_google_provider",
  "get_world_building_model",
]
