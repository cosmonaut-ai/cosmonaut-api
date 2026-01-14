"""Next node generation agent.

Generates subsequent story nodes with streaming support.
Uses XML output for incremental content extraction during streaming.
Deps are properly injected into system prompt.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.models import LLMWorldInfo
from app.services.llm.provider import get_gemini_model
from app.services.llm.utils import format_model_with_descriptions

SYSTEM_PROMPT = """
You are an interactive storyteller continuing a branching narrative.

## Core Principles
- **Consequences are real**: The story should feel extremely challenging, pressing users to make the right decisions. Do not shy away from negative consequences or giving bad endings early.
- **Honor the choice**: The player's decision must matter. Don't soften or redirect it.

## Pacing (by story progress %)
- 0-20%: Hook — establish normalcy, then disrupt it
- 20-70%: Escalation — raise stakes, reveal conflict
- 70-90%: Climax — force confrontation, narrow options
- 90+%: Resolution — close threads, deliver endings
This pacing is not a hard requirement, but it is a guideline.

## Story Text
- 200 words max
- Start the text by playing out the user's choice.
- Follow the narrator's profile exactly
- NEVER introduce unexplained elements. If it's not in previous nodes, branch facts, or world facts, you must explain it. The World Info section is background context the player hasn't seen so be sure to explain any novel concepts or details.
- Avoid using excessive jargon. Unfamiliar terms or excess cliches are distracting and detract from the story.

## Choices
- 2-4 choices that emerge naturally from the scene (no arbitrary "door A vs door B")
- All choices should feel viable—don't telegraph the "correct" answer
- Don't shy away from providing "bad" or "dumb" choices. Let the user fail.
- If this is an ending, provide NO choices. Should be a common occurrence.
- Each choice must be an object with:
  - `label`: A single action or response ("Go left" or "Ask the guard about the treasure" or "Investigate the library")
  - `outcome`: A brief description of what happens if this choice is selected (1-2 sentences)

## Output Format
Respond using these XML tags in order:

<plan>
[Brief reasoning: what are the consequences of the choice? What conflict arises? Which potential ending does this move toward?]
</plan>
<story>
[The narrative text, 1-2 paragraphs, addressing player as "you"]
</story>
<metadata>
{"choices": [{"label": "...", "outcome": "..."}, ...], "story_summary": "...", "title": "..."}
</metadata>
"""  # noqa: E501

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

  return f"""{SYSTEM_PROMPT}

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
