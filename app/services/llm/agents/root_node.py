"""Root node generation agent.

Generates the first story node for a world.
Uses structured output with deps properly injected into system prompt.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.models import LLMStoryNode, LLMWorldInfo
from app.services.llm.provider import get_gemini_model
from app.services.llm.utils import format_model_with_descriptions

SYSTEM_PROMPT = """
You are a storyteller for an interactive story where players choose their own path. Generate the opening scene that hooks the player.

## Your Task
Create an engaging first scene that:
- Establishes the immediate situation with sensory detail
- Introduces a compelling hook or initial tension
- Presents the player with their first meaningful choices
- Sets up the context for the world and story.

## Story Text
- 300 words max
- Follow the narrator's profile exactly
- NEVER introduce unexplained elements. If it's not in previous nodes, branch facts, or world facts, you must explain it. The World Info section is background context the player hasn't seen so be sure to explain any novel concepts or details.
- Avoid using excessive jargon. Unfamiliar terms or excess cliches are distracting and detract from the story.

## Choices
- 2-4 choices that emerge naturally from the scene (no arbitrary "door A vs door B")
- All choices should feel viable—don't telegraph the "correct" answer
- Don't shy away from providing "bad" or "dumb" choices. Let the user fail.
- Each choice must have:
  - `label`: A single action or response ("Go left" or "Ask the guard about the treasure" or "Investigate the library")
  - `outcome`: A brief description of what happens if this choice is selected (1-2 sentences)
"""  # noqa: E501


class RootNodeDeps(BaseModel):
  """Dependencies for root node generation."""

  world_info: LLMWorldInfo
  narrator_profile: str = Field(description="The narrator's profile.")


# Module-level agent instantiation
_agent: Agent[RootNodeDeps, LLMStoryNode] = Agent(
  model=get_gemini_model(),
  deps_type=RootNodeDeps,
  output_type=LLMStoryNode,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[RootNodeDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
  """Inject world context and narrator profile into system prompt."""
  return f"""{SYSTEM_PROMPT}

---
WORLD CONTEXT:
{format_model_with_descriptions(ctx.deps.world_info)}

---
NARRATOR PROFILE:
{ctx.deps.narrator_profile}
"""


async def generate_start_node(deps: RootNodeDeps) -> LLMStoryNode:
  """Generate the first story node for a world."""
  result = await _agent.run(
    "Generate the first story node.",
    deps=deps,
  )
  return result.output


# Re-export for backward compatibility
LLMRootNodeDeps = RootNodeDeps
