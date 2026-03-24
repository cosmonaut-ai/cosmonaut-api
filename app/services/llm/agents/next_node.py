"""Next node generation agent.

Generates subsequent story nodes with streaming support.
Uses XML output for incremental content extraction during streaming.

The system prompt contains only static content (instructions + world context
+ narrator profile) that is constant for all nodes within a world.  Dynamic
per-node context (story progress, facts, user choice, recent text) is passed
as the user message.  This layout enables automatic prompt caching at the
model level -- the system prompt prefix gets a cache hit on every node after
the first.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.agents.prompts import (
  CHOICE_OUTCOME_INSTRUCTIONS,
  CUSTOM_CHOICE_INSTRUCTIONS,
  FAMILY_FRIENDLY_INSTRUCTIONS,
  build_next_node_system_prompt,
)
from app.services.llm.models import LLMWorldInfo
from app.services.llm.provider import get_storytelling_model
from app.services.llm.sanitize import sanitize_user_input
from app.services.llm.utils import format_model_with_descriptions

SYSTEM_PROMPT = build_next_node_system_prompt()


class NextNodeDeps(BaseModel):
  """Dependencies for next node generation."""

  world_info: LLMWorldInfo
  story_summary: str = Field(description="A summary of the story up to this point.")
  previous_text: str = Field(description="The previous story node text.")
  user_choice: str = Field(description="The user's choice.")
  choice_outcome: str | None = Field(default=None, description="The outcome of the user's choice.")
  world_facts: list[str] = Field(
    description="Facts about the world that may or may not be relevant to the next story node."
  )
  branch_facts: list[str] = Field(description="Facts about the story up to this point that may or may not be relevant.")
  narrator_profile: str = Field(description="The narrator's profile.")
  story_length: int = Field(description="The length of the story so far in nodes.")
  story_max_nodes: int = Field(description="The maximum length of the story in nodes.")
  is_custom_choice: bool = Field(default=False, description="Whether this is a user-created custom choice.")
  family_friendly: bool = Field(default=False, description="Whether to enforce family-friendly content guidelines.")


_agent: Agent[NextNodeDeps, str] = Agent(
  model=get_storytelling_model(),
  deps_type=NextNodeDeps,
  output_type=str,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[NextNodeDeps]) -> str:
  """Build the static system prompt (cacheable).

  Contains only content that is constant across all node generations for a
  given world: instructions, world info, and narrator profile.  Dynamic
  per-node context is passed as the user message via ``build_user_message``.
  """
  deps = ctx.deps
  family_friendly_note = FAMILY_FRIENDLY_INSTRUCTIONS if deps.family_friendly else ""

  return f"""{SYSTEM_PROMPT}
{family_friendly_note}

## Narrator Profile:
<narrator_data>
{deps.narrator_profile}
</narrator_data>

## World Info (background—player hasn't seen this):
<world_data>
{format_model_with_descriptions(deps.world_info)}
</world_data>
"""


def build_user_message(deps: NextNodeDeps) -> str:
  """Build the per-node dynamic context sent as the user message.

  Contains everything that changes between node generations: story progress,
  story summary, the player's choice, world/branch facts, and recent text.
  """
  progress_pct = (deps.story_length / deps.story_max_nodes) * 100
  world_facts = "\n".join(f"- {f}" for f in deps.world_facts) if deps.world_facts else "None"
  branch_facts = "\n".join(f"- {f}" for f in deps.branch_facts) if deps.branch_facts else "None"

  sanitized_choice = sanitize_user_input(deps.user_choice)

  custom_choice_note = CUSTOM_CHOICE_INSTRUCTIONS if deps.is_custom_choice else ""
  choice_outcome_note = (
    CHOICE_OUTCOME_INSTRUCTIONS.format(choice_outcome=deps.choice_outcome) if deps.choice_outcome else ""
  )

  return f"""Continue the story.

# CONTEXT
## Story Progress:
{progress_pct:.0f}%

## Story Summary:
{deps.story_summary}

## Player's Choice:
<user_choice>
{sanitized_choice}
</user_choice>
{custom_choice_note}
{choice_outcome_note}

## World Facts:
{world_facts}

## Branch Facts (newest first):
{branch_facts}

## Recent Text (last 5 nodes):
{deps.previous_text}
"""


def get_next_node_agent() -> Agent[NextNodeDeps, str]:
  """Get the next node agent for streaming."""
  return _agent


# Re-export for backward compatibility
LLMNextNodeDeps = NextNodeDeps
