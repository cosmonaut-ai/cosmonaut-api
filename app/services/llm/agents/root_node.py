"""Root node generation agent.

Generates the first story node for a world with streaming support.
Uses XML output format for incremental content extraction.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.agents.prompts import (
  CHOICE_GUIDELINES,
  FAMILY_FRIENDLY_INSTRUCTIONS,
  METADATA_GUIDELINES,
  NARRATIVE_CONSTRAINTS,
  OUTPUT_FORMAT,
  PROSE_QUALITY,
  STORY_TEXT_RULES,
)
from app.services.llm.models import LLMWorldInfo
from app.services.llm.provider import get_gemini_model
from app.services.llm.utils import format_model_with_descriptions

ROOT_NODE_PREAMBLE = """
You are a storyteller for an interactive story where players choose their own path. Generate the opening scene that hooks the player.
"""  # noqa: E501

ROOT_NODE_TASK = """
## Your Task
Create an engaging first scene that:
- Establishes the immediate situation with sensory detail
- Introduces a compelling hook — one of:
  - A **question** (mystery: something is wrong or unexplained)
  - A **disruption** (action: normalcy is shattered)
  - A **dilemma** (moral: a choice with no clear right answer)
- Presents the player with their first meaningful choices
- Sets up the context for the world and story

## Story Text
- 300 words max
- 1-3 paragraphs, addressing the player as "you"
- Do NOT provide endings for the root node — this is the beginning.
"""

SYSTEM_PROMPT = (
  ROOT_NODE_PREAMBLE
  + ROOT_NODE_TASK
  + STORY_TEXT_RULES
  + PROSE_QUALITY
  + NARRATIVE_CONSTRAINTS
  + CHOICE_GUIDELINES
  + METADATA_GUIDELINES
  + OUTPUT_FORMAT
)


class RootNodeDeps(BaseModel):
  """Dependencies for root node generation."""

  world_info: LLMWorldInfo
  narrator_profile: str = Field(description="The narrator's profile.")
  family_friendly: bool = Field(default=False, description="Whether to enforce family-friendly content guidelines.")


# Module-level agent instantiation (streaming with XML output)
_agent: Agent[RootNodeDeps, str] = Agent(
  model=get_gemini_model(),
  deps_type=RootNodeDeps,
  output_type=str,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[RootNodeDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
  """Build system prompt for streaming root node generation."""
  family_friendly_note = FAMILY_FRIENDLY_INSTRUCTIONS if ctx.deps.family_friendly else ""
  return f"""{SYSTEM_PROMPT}
{family_friendly_note}

---
WORLD CONTEXT (background—player hasn't seen this):
{format_model_with_descriptions(ctx.deps.world_info)}

---
NARRATOR PROFILE:
{ctx.deps.narrator_profile}
"""


def get_root_node_agent() -> Agent[RootNodeDeps, str]:
  """Get the root node agent for streaming."""
  return _agent


# Re-export for backward compatibility
LLMRootNodeDeps = RootNodeDeps
