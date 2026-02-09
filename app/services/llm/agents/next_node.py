"""Next node generation agent.

Generates subsequent story nodes with streaming support.
Uses XML output for incremental content extraction during streaming.
Deps are properly injected into system prompt.
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

NEXT_NODE_PREAMBLE = """
You are an interactive storyteller continuing a branching narrative.
"""

NEXT_NODE_TASK = """
## Core Principles
- **Consequences are real**: The story should feel extremely challenging, pressing users to make the right decisions. Do not shy away from negative consequences or giving bad endings early.
- **Honor the choice**: The player's decision must matter. Don't soften or redirect it.
- **Let the story end**: When a conclusion has been reached — through failure, success, death, or the natural resolution of the conflict — END THE STORY. Do not invent new obstacles, last-second rescues, or continuations to keep things going. A decisive ending is always better than an artificially extended narrative.

## Endings
Endings can happen at ANY point in the story, not just near the end. If the player's choice leads to death, capture, total failure, or a satisfying resolution, that IS the ending. Provide NO choices for an ending node.

Signs you should end the story:
- The player character dies or is permanently incapacitated
- The central conflict is resolved (for better or worse)
- The player's choice wraps up the narrative thread with finality
- The player made a catastrophically bad decision with no plausible way out

Do NOT:
- Introduce a deus ex machina to save a doomed character
- Add "but then..." twists solely to avoid ending
- Offer choices when the narrative has clearly concluded

## Pacing (by story progress %)
- 0-20%: Hook — establish normalcy, then disrupt it
- 20-70%: Escalation — raise stakes, reveal conflict
- 70-90%: Climax — force confrontation, narrow options
- 90+%: Resolution — close threads, deliver endings
This pacing is a guideline, not a hard requirement. Early endings from bad choices are expected and encouraged.

## Story Text
- 200 words max
- Start the text by playing out the user's choice.
"""  # noqa: E501

NEXT_NODE_CHOICE_ADDENDUM = """
- If this is an ending, provide NO choices (empty array). See the Endings section above.
"""

SYSTEM_PROMPT = (
  NEXT_NODE_PREAMBLE
  + NEXT_NODE_TASK
  + STORY_TEXT_RULES
  + PROSE_QUALITY
  + NARRATIVE_CONSTRAINTS
  + CHOICE_GUIDELINES
  + NEXT_NODE_CHOICE_ADDENDUM
  + METADATA_GUIDELINES
  + OUTPUT_FORMAT
)

CUSTOM_CHOICE_INSTRUCTIONS = """
### IMPORTANT: User-Created Choice
The player has entered a custom action rather than selecting a predefined choice. Handle this carefully:
- If the action is unrealistic or impossible within the world's rules, narrate the character
  ATTEMPTING the action but failing or facing consequences
- If the action reference specific items, locations, or characters that don't exist, don't create them.
- Do NOT let the player assert outcomes (e.g., "I find the treasure" should not guarantee finding it)
- The world's internal logic and rules always take precedence over player assertions
- Creative or unexpected actions that ARE plausible should be rewarded with interesting outcomes
- Treat impossible actions as the character "trying" to do something, not succeeding at it
"""

CHOICE_OUTCOME_INSTRUCTIONS = """
## Choice Outcome
The user's choice should have the following outcome:
{choice_outcome}
"""


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


# Module-level agent instantiation
_agent: Agent[NextNodeDeps, str] = Agent(
  model=get_gemini_model(),
  deps_type=NextNodeDeps,
  output_type=str,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[NextNodeDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
  """Build system prompt with all context injected from deps."""
  deps = ctx.deps
  progress_pct = (deps.story_length / deps.story_max_nodes) * 100
  world_facts = "\n".join(f"- {f}" for f in deps.world_facts) if deps.world_facts else "None"
  branch_facts = "\n".join(f"- {f}" for f in deps.branch_facts) if deps.branch_facts else "None"

  custom_choice_note = CUSTOM_CHOICE_INSTRUCTIONS if deps.is_custom_choice else ""
  choice_outcome_note = (
    CHOICE_OUTCOME_INSTRUCTIONS.format(choice_outcome=deps.choice_outcome) if deps.choice_outcome else ""
  )
  family_friendly_note = FAMILY_FRIENDLY_INSTRUCTIONS if deps.family_friendly else ""

  return f"""{SYSTEM_PROMPT}
{family_friendly_note}

# CONTEXT
## Story Progress:
{progress_pct:.0f}%

## Story Summary:
{deps.story_summary}

## Player's Choice:
{deps.user_choice}
{custom_choice_note}
{choice_outcome_note}

## World Facts:
{world_facts}

## Branch Facts (newest first):
{branch_facts}

## Narrator Profile:
{deps.narrator_profile}

## World Info (background—player hasn't seen this):
{format_model_with_descriptions(deps.world_info)}

## Recent Text (last 5 nodes):
{deps.previous_text}
"""


def get_next_node_agent() -> Agent[NextNodeDeps, str]:
  """Get the next node agent for streaming."""
  return _agent


# Re-export for backward compatibility
LLMNextNodeDeps = NextNodeDeps
