"""LLM services package.

This package provides LLM agents for story generation.
All public symbols are re-exported here for backward compatibility.
"""

from app.services.llm.agents.fact_extraction import (
  FactExtractionDeps,
  LLMFactExtractionDeps,
  generate_facts_async,
)
from app.services.llm.agents.narrator import generate_narrator_profile
from app.services.llm.agents.next_node import (
  LLMNextNodeDeps,
  NextNodeDeps,
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
from app.services.llm.provider import get_gemini_model, get_gemini_provider
from app.services.llm.utils import format_model_with_descriptions

__all__ = [
  # Models
  "LLMCharacter",
  "LLMFactExtraction",
  "LLMLocation",
  "LLMNarratorProfile",
  "LLMNodeMetadata",
  "LLMStoryNode",
  "LLMWorldInfo",
  # Provider
  "get_gemini_model",
  "get_gemini_provider",
  # Utils
  "format_model_with_descriptions",
  # World Info
  "generate_world_info",
  # Root Node
  "LLMRootNodeDeps",
  "RootNodeDeps",
  "get_root_node_agent",
  # Next Node
  "LLMNextNodeDeps",
  "NextNodeDeps",
  "get_next_node_agent",
  # Fact Extraction
  "FactExtractionDeps",
  "LLMFactExtractionDeps",
  "generate_facts_async",
  # Narrator
  "generate_narrator_profile",
]
