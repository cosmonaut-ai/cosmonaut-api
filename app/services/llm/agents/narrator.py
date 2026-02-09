"""Narrator profile generation agent.

Generates narrator voice profile for story generation.
Uses structured output with deps properly injected into system prompt.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.agents.prompts import FAMILY_FRIENDLY_INSTRUCTIONS, NARRATIVE_CONSTRAINTS
from app.services.llm.models import LLMNarratorProfile, LLMWorldInfo
from app.services.llm.provider import get_gemini_model
from app.services.llm.utils import format_model_with_descriptions

SYSTEM_PROMPT = """
You create narrator profiles for a Choose Your Own Adventure game.

## Your Task
Given the world setting and story concept, generate a narrator profile that defines how the story will be told. This profile will guide all future narrative text generation.

## Profile Must Define
- **Voice & Persona**: Is the narrator omniscient, mysterious, sardonic, warm, clinical, theatrical?
- **Prose Style**: Sentence rhythm (terse vs. flowing), vocabulary level (pulpy vs. literary), use of figurative language
- **Sensory Focus**: Which senses are prioritized? Does the narrator favor visceral action or introspective moments?
- **Tone & Atmosphere**: How does the narrator build mood? Through humor, dread, wonder, tension?
- **Player Address**: The narrator always addresses the player as "you" — how does this feel? (intimate, commanding, conspiratorial?)

## Guidelines
- Write a single, dense paragraph (3-5 sentences)
- Use present tense to describe how the narrator writes
- Be specific enough that two different story nodes would sound stylistically consistent
- Match the narrator's personality to the genre and tone of the world

## Examples of Specificity
❌ Vague: "The narrator has a dark tone"
✅ Specific: "The narrator whispers secrets in clipped, unsettling sentences, favoring tactile descriptions of decay and cold."

❌ Vague: "The narrator is humorous"
✅ Specific: "The narrator is a wry observer who punctuates danger with deadpan asides, treating mortal peril like mild inconvenience."
"""  # noqa: E501


class NarratorDeps(BaseModel):
  """Dependencies for narrator profile generation."""

  world_info: LLMWorldInfo
  family_friendly: bool = Field(default=False, description="Whether to enforce family-friendly content guidelines.")


# Module-level agent instantiation
_agent: Agent[NarratorDeps, LLMNarratorProfile] = Agent(
  model=get_gemini_model(),
  deps_type=NarratorDeps,
  output_type=LLMNarratorProfile,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[NarratorDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
  """Inject world info into system prompt."""
  family_friendly_note = FAMILY_FRIENDLY_INSTRUCTIONS if ctx.deps.family_friendly else ""
  return f"""{SYSTEM_PROMPT}
{NARRATIVE_CONSTRAINTS}
{family_friendly_note}

# WORLD INFO:
{format_model_with_descriptions(ctx.deps.world_info)}
"""


async def generate_narrator_profile(world_info: LLMWorldInfo, *, family_friendly: bool = False) -> LLMNarratorProfile:
  """Generate a narrator profile for a world."""
  deps = NarratorDeps(world_info=world_info, family_friendly=family_friendly)
  result = await _agent.run(
    "Generate a narrator profile for this world.",
    deps=deps,
  )
  return result.output
