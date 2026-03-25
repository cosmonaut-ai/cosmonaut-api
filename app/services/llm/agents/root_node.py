"""Root node generation agent.

Generates the first story node for a world with streaming support.
Uses XML output format for incremental content extraction.

Like next_node, the system prompt contains only static content so that
prompt caching (configured at the model level) can activate automatically.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.agents.prompts import (
  build_content_directives,
  build_root_node_system_prompt,
)
from app.services.llm.models import LLMWorldInfo
from app.services.llm.provider import get_storytelling_model
from app.services.llm.utils import format_model_with_descriptions

SYSTEM_PROMPT = build_root_node_system_prompt()


class RootNodeDeps(BaseModel):
  """Dependencies for root node generation."""

  world_info: LLMWorldInfo
  narrator_profile: str = Field(description="The narrator's profile.")
  vocab_level: str = Field(default="adult", description="Vocabulary complexity level.")
  content_filter: str = Field(default="none", description="Content filter strictness.")


_agent: Agent[RootNodeDeps, str] = Agent(
  model=get_storytelling_model(),
  deps_type=RootNodeDeps,
  output_type=str,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[RootNodeDeps]) -> str:
  """Build system prompt for streaming root node generation."""
  directives = build_content_directives(ctx.deps.vocab_level, ctx.deps.content_filter)
  return f"""{SYSTEM_PROMPT}
{directives}

## Narrator Profile:
<narrator_data>
{ctx.deps.narrator_profile}
</narrator_data>

## World Info (background—player hasn't seen this):
<world_data>
{format_model_with_descriptions(ctx.deps.world_info)}
</world_data>
"""


def get_root_node_agent() -> Agent[RootNodeDeps, str]:
  """Get the root node agent for streaming."""
  return _agent


# Re-export for backward compatibility
LLMRootNodeDeps = RootNodeDeps
