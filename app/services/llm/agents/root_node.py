"""Root node generation agent.

Generates the first story node for a world with streaming support.
Uses XML output format for incremental content extraction.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.models import LLMWorldInfo
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
- Each choice must be an object with:
  - `label`: A single action or response ("Go left" or "Ask the guard about the treasure" or "Investigate the library")
  - `outcome`: A brief description of what happens if this choice is selected (1-2 sentences)

## Output Format
Respond using these XML tags in order:

<plan>
[Brief reasoning: what is the hook? What tension is introduced? What world elements are being established?]
</plan>
<story>
[The narrative text, 1-3 paragraphs, addressing player as "you"]
</story>
<metadata>
{"choices": [{"label": "...", "outcome": "..."}, ...], "story_summary": "...", "title": "..."}
</metadata>
"""  # noqa: E501


class RootNodeDeps(BaseModel):
  """Dependencies for root node generation."""

  world_info: LLMWorldInfo
  narrator_profile: str = Field(description="The narrator's profile.")


# Module-level agent instantiation (streaming with XML output)
_agent: Agent[RootNodeDeps, str] = Agent(
  model=get_gemini_model(),
  deps_type=RootNodeDeps,
  output_type=str,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[RootNodeDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
  """Build system prompt for streaming root node generation."""
  return f"""{SYSTEM_PROMPT}

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
