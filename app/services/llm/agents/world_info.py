"""World info generation agent.

Generates world setting, characters, locations from a user prompt.
Uses structured output (no XML parsing needed for non-streaming).
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.agents.prompts import FAMILY_FRIENDLY_INSTRUCTIONS, NARRATIVE_CONSTRAINTS
from app.services.llm.models import LLMWorldInfo
from app.services.llm.provider import get_world_building_model
from app.utils import extract_xml_json

SYSTEM_PROMPT = """
You are a world-building expert for a Choose Your Own Adventure game.

Given a short prompt describing the world or story concept, create a detailed, immersive world foundation.

For items marked (User viewable), do not reveal too much about the story.

## Your Task
Build out these elements:
- **Setting**: The setting of the story and the world it is in. What is the time period, location, and overall atmosphere of the story? Base this on the user's prompt - it should be a normal, everyday world unless the user specifies otherwise.
- **Backstory**: The background, history, and circumstances that set up this story. This is the most important element and should be the longest. If there are any novel concepts, mysteries, or mechanics that the user will interact with, explain them in detail here.
- **Main Characters**: The main characters of the story and their relationships to each other. (0 is fine if the only character is the player.)
- **Main Locations**: The main locations of the story and their relationships to each other. (At least one location is required)
- **Story Title**: A title for the story, max 5 words. (User viewable)
- **Story Description**: A short description of the story. (User viewable)
- **Story Genre**: The genre of the story. (Literary, Fantasy, Sci-fi, Horror, Mystery, etc.) (User viewable)
- **Potential Endings**: Generate a list of potential endings for the story to guide the narrative towards. Include a mix of positive, negative, and bittersweet outcomes. Not every ending should be a victory — meaningful failure is equally important.

## Guidelines
- Leave room for player agency; don't predetermine the protagonist's personality or key decisions
- If you are introducing novel concepts or mechanics, explain them in detail. Be sure to explain how they work and how they interact with the world.
- Mysteries and secrets (if any) should be described in detail here and left for the player to discover.
- The story title and description should be concise and descriptive.

## Output Format
Respond using these XML tags in order:

<plan>
[Think through: What makes this concept interesting? What are the central conflicts or tensions? What tone/genre fits best? What mysteries or mechanics need explanation? What endings feel satisfying for this type of story?]
</plan>
<world_info>
{
  "setting": "The setting of the story and the world it is in.",
  "backstory": "The background, history, and circumstances...",
  "characters": [
    {"name": "Character Name", "description": "A short description", "relationships": ["Relationship to other character", "..."]}
  ],
  "locations": [
    {"name": "Location Name", "description": "A short description", "connections": ["Connection to other location", "..."]}
  ],
  "title": "Title (max 5 words)",
  "description": "A short description of the story",
  "genre": "Fantasy, Sci-fi, Horror, Mystery, Literary, etc.",
  "endings": ["Ending 1", "Ending 2", "..."]
}
</world_info>
"""  # noqa: E501


class WorldInfoDeps(BaseModel):
  """Dependencies for world info generation."""

  family_friendly: bool = Field(default=False, description="Whether to enforce family-friendly content guidelines.")


# Module-level agent instantiation
_agent: Agent[WorldInfoDeps, str] = Agent(
  model=get_world_building_model(),
  deps_type=WorldInfoDeps,
  output_type=str,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[WorldInfoDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
  """Build system prompt with optional family-friendly instructions."""
  family_friendly_note = FAMILY_FRIENDLY_INSTRUCTIONS if ctx.deps.family_friendly else ""
  return SYSTEM_PROMPT + NARRATIVE_CONSTRAINTS + family_friendly_note


async def generate_world_info(world_prompt: str, *, family_friendly: bool = False) -> LLMWorldInfo:
  """Generate world info from a prompt using XML-based planning format.

  The LLM first creates a plan in <plan> tags, then outputs structured
  world info in <world_info> tags as JSON.
  """
  deps = WorldInfoDeps(family_friendly=family_friendly)
  result = await _agent.run(world_prompt, deps=deps)
  raw_output = result.output

  # Extract world_info JSON from XML response
  return extract_xml_json(raw_output, "world_info", LLMWorldInfo)
