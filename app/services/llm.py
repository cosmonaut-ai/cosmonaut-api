from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import ModelSettings

from app.core.config import settings
from app.services.secret_manager import get_secret_value
from app.utils import extract_xml_json

if TYPE_CHECKING:
  from pydantic_ai import Agent, RunContext
  from pydantic_ai.models.google import GoogleModel
  from pydantic_ai.providers.google import GoogleProvider

# Note: This model instance isn't strictly used by the Agents below
# (they currently use the model name string), but kept for your existing logic.
model: Any | None = None
provider: Any | None = None


def get_gemini_provider() -> "GoogleProvider":
  global provider
  if provider is None:
    from pydantic_ai.providers.google import GoogleProvider

    provider = GoogleProvider(api_key=get_secret_value(settings.GEMINI_API_KEY_PARAM))
    # provider = GoogleProvider(
    #   vertexai=True,  # type: ignore
    #   project="cosmonaut-481723",  # type: ignore
    #   location="us-central1",  # type: ignore
    # )
  return provider


def get_gemini_model(model_name: str = settings.GEMINI_MODEL_SMALL) -> "GoogleModel":
  global model
  if model is None:
    from pydantic_ai.models.google import GoogleModel

    model = GoogleModel(model_name=model_name, provider=get_gemini_provider(), settings=ModelSettings(temperature=0.95))
  return model


def format_model_with_descriptions(model: BaseModel) -> str:
  """Format a Pydantic model with field descriptions inline as comments."""
  schema = model.model_json_schema()
  data = model.model_dump()
  properties = schema.get("properties", {})

  lines = ["{"]
  items = list(data.items())
  for i, (key, value) in enumerate(items):
    desc = properties.get(key, {}).get("description", "")

    # Format the value as JSON
    import json

    value_json = json.dumps(value, ensure_ascii=False)

    comma = "," if i < len(items) - 1 else ""

    # Add the key-value pair
    lines.append(f'  "{key}": {value_json}{comma}')

    # Add description as a comment on the next line if it exists
    if desc:
      lines.append(f"  // {desc}")

  lines.append("}")
  return "\n".join(lines)


##################################################################
# G E N E R A T E   W O R L D   I N F O
##################################################################

GENERATE_WORLD_INFO_PROMPT = """
You are a world-building expert for a Choose Your Own Adventure game.

Given a short prompt describing the world or story concept, create a detailed, immersive world foundation.

For items marked (User viewable), do not reveal too much about the story.

## Your Task
Build out these elements:
- **Setting**: The setting of the story and the world it is in. What is the time period, location, and overall atmosphere of the story? Base this on the user's prompt - it should be a normal, everyday world unless the user specifies otherwise.
- **Narrative Context**: The background, history, and circumstances that set up this story. This is the most important element and should be the longest. If there are any novel concepts, mysteries, or mechanics that the user will interact with, explain them in detail here.
- **Main Characters**: The main characters of the story and their relationships to each other. (0 is fine if the only character is the player.)
- **Main Locations**: The main locations of the story and their relationships to each other. (At least one location is required)
- **Potential Endings**: Generate a list of potential endings for the story to guide the narrative towards.
- **Story Title**: A title for the story, max 5 words. (User viewable)
- **Story Description**: A short description of the story. (User viewable)
- **Story Genre**: The genre of the story. (Literary, Fantasy, Sci-fi, Horror, Mystery, etc.) (User viewable)

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
  "narrative_context": "The background, history, and circumstances...",
  "characters": [
    {"name": "Character Name", "description": "A short description", "relationships": ["Relationship to other character", "..."]}
  ],
  "locations": [
    {"name": "Location Name", "description": "A short description", "connections": ["Connection to other location", "..."]}
  ],
  "world_title": "Title (max 5 words)",
  "world_description": "A short description of the story",
  "world_genre": "Fantasy/Sci-fi/Horror/Mystery/Literary/etc.",
  "potential_endings": ["Ending 1", "Ending 2", "..."]
}
</world_info>
"""  # noqa: E501


class LLMCharacter(BaseModel):
  name: str = Field(description="The name of the character.")
  description: str = Field(description="A short description of the character.")
  relationships: list[str] = Field(description="The relationships the character has with other characters.")


class LLMLocation(BaseModel):
  name: str = Field(description="The name of the location.")
  description: str = Field(description="A short description of the location.")
  connections: list[str] = Field(description="The connections the location has with other locations.")


class LLMWorldInfo(BaseModel):
  setting: str = Field(description="The setting of the story and the world it is in.")
  narrative_context: str = Field(description="The background, history, and circumstances that set up this story.")
  characters: list[LLMCharacter] = Field(description="The main characters of the story.")
  locations: list[LLMLocation] = Field(description="The main locations of the story.")
  world_title: str = Field(description="A title for the story, max 5 words.")
  world_description: str = Field(description="A short description of the story.")
  world_genre: str = Field(description="The genre of the story.")
  potential_endings: list[str] = Field(
    description="A list of potential endings for the story to guide the narrative towards."
  )


world_info_agent: Any | None = None


def get_world_info_agent() -> "Agent[None, str]":
  global world_info_agent
  if world_info_agent is None:
    from pydantic_ai import Agent

    world_info_agent = Agent(
      model=get_gemini_model(settings.GEMINI_MODEL_LARGE),
      system_prompt=GENERATE_WORLD_INFO_PROMPT,
      output_type=str,
    )
  return world_info_agent


async def generate_world_info(world_prompt: str) -> LLMWorldInfo:
  """Generate world info from a prompt using XML-based planning format.

  The LLM first creates a plan in <plan> tags, then outputs structured
  world info in <world_info> tags as JSON.
  """
  result = await get_world_info_agent().run(world_prompt)
  raw_output = result.output

  # Extract world_info JSON from XML response
  return extract_xml_json(raw_output, "world_info", LLMWorldInfo)


##################################################################
# G E N E R A T E   S T A R T   N O D E
##################################################################

GENERATE_START_NODE_PROMPT = """
You are a storyteller for an interactive story where players choose their own path. Generate the opening scene that hooks the player.

## Your Task
Create an engaging first scene that:
- Establishes the immediate situation with sensory detail
- Introduces a compelling hook or initial tension
- Presents the player with their first meaningful choices
- Sets up the context for the world and story.

## Story Text
- Follow the narrator's profile exactly
- NEVER introduce unexplained elements. If it's not in previous nodes, branch facts, or world facts, you must explain it. The World Info section is background context the player hasn't seen so be sure to explain any novel concepts or details.
- Avoid using excessive jargon. Unfamiliar terms or excess cliches are distracting and detract from the story.

## Choices
- 2-4 choices that emerge naturally from the scene (no arbitrary "door A vs door B")
- All choices should feel viable—don't telegraph the "correct" answer
- Don't shy away from providing "bad" or "dumb" choices. Let the user fail.
- Each choice should be formatted as a single action or response ("Go left" or "Ask the guard about the treasure" or "Investigate the library")
"""  # noqa: E501


class LLMRootNodeDeps(BaseModel):
  world_info: LLMWorldInfo
  narrator_profile: str = Field(description="The narrator's profile.")


class LLMStoryNode(BaseModel):
  text: str = Field(description="The text of the story node.")
  choices: list[str] = Field(
    description="The choices available from this node. (2-4 choices). If this is an end to the story, provide no choices."  # noqa: E501
  )
  story_summary: str = Field(description="A summary of the story up to this node.")
  title: str = Field(description="A short 1-5 word title for the story node.")


root_node_agent: Any | None = None


def get_root_node_agent() -> "Agent[LLMRootNodeDeps, LLMStoryNode]":
  global root_node_agent
  if root_node_agent is None:
    from pydantic_ai import Agent

    root_node_agent = Agent(
      model=get_gemini_model(),
      deps_type=LLMRootNodeDeps,
      output_type=LLMStoryNode,
    )

    @root_node_agent.system_prompt
    def add_world_context(ctx: "RunContext[LLMRootNodeDeps]") -> str:  # type: ignore
      # Inject dependencies explicitly into the prompt
      return f"""{GENERATE_START_NODE_PROMPT}

            ---
            WORLD CONTEXT:
            {format_model_with_descriptions(ctx.deps.world_info)}

            ---
            NARRATOR PROFILE:
            {ctx.deps.narrator_profile}
            """

  return root_node_agent


async def generate_start_node(deps: LLMRootNodeDeps) -> LLMStoryNode:
  result = await get_root_node_agent().run(
    "Generate the first story node.",  # Dummy user prompt, actual instructions are in system prompt
    deps=deps,
  )
  return result.output


class LLMNodeMetadata(BaseModel):
  choices: list[str] = Field(description="The choices available from this node. (2-4 choices).")
  story_summary: str = Field(description="A summary of the story up to this node.")
  title: str = Field(description="A short 1-5 word title for the story node.")


##################################################################
# G E N E R A T E   N E X T   N O D E
##################################################################

GENERATE_NEXT_NODE_PROMPT = """
You are an interactive storyteller continuing a branching narrative.

## Core Principles
- **Consequences are real**: Risky choices carry real risk; clever ones are rewarded. Deaths, failures, and bad endings are not just possible—they're EXTREMELY common. Most choices lead to immediate endings.
- **Honor the choice**: The player's decision must matter. Don't soften or redirect it.

## Pacing (by story progress %)
- 0-20%: Hook — establish normalcy, then disrupt it
- 20-70%: Escalation — raise stakes, reveal conflict
- 70-90%: Climax — force confrontation, narrow options
- 90+%: Resolution — close threads, deliver endings
This pacing is not a hard requirement, but it is a guideline.

## Story Text
- 1-2 short paragraphs, max
- Start the text by playing out the user's choice.
- Follow the narrator's profile exactly
- NEVER introduce unexplained elements. If it's not in previous nodes, branch facts, or world facts, you must explain it. The World Info section is background context the player hasn't seen so be sure to explain any novel concepts or details.
- Avoid using excessive jargon. Unfamiliar terms or excess cliches are distracting and detract from the story.

## Choices
- 2-4 choices that emerge naturally from the scene (no arbitrary "door A vs door B")
- All choices should feel viable—don't telegraph the "correct" answer
- Don't shy away from providing "bad" or "dumb" choices. Let the user fail.
- If this is an ending, provide NO choices. Should be a common occurrence.
- Each choice should be formatted as a single action or response ("Go left" or "Ask the guard about the treasure" or "Investigate the library")

## Output Format
Respond using these XML tags in order:

<plan>
[Brief reasoning: what are the consequences of the choice? What conflict arises? Which potential ending does this move toward?]
</plan>
<story>
[The narrative text, 1-2 paragraphs, addressing player as "you"]
</story>
<metadata>
{"choices": [...], "story_summary": "...", "title": "..."}
</metadata>
"""  # noqa: E501


class LLMNextNodeDeps(BaseModel):
  world_info: LLMWorldInfo
  story_summary: str = Field(description="A summary of the story up to this point.")
  previous_text: str = Field(description="The previous story node text.")
  user_choice: str = Field(description="The user's choice.")
  world_facts: list[str] = Field(
    description="Facts about the world that may or may not be relevant to the next story node."
  )
  branch_facts: list[str] = Field(
    description="Facts about the story up to this point that may or may not be relevant to the next story node."  # noqa: E501
  )
  narrator_profile: str = Field(description="The narrator's profile.")
  story_length: int = Field(description="The length of the story so far in nodes.")
  story_max_nodes: int = Field(description="The maximum length of the story in nodes.")
  is_custom_choice: bool = Field(default=False, description="Whether this is a user-created custom choice.")


next_node_agent: Any | None = None


def get_next_node_agent() -> "Agent[LLMNextNodeDeps, str]":
  global next_node_agent
  if next_node_agent is None:
    from pydantic_ai import Agent

    next_node_agent = Agent(
      model=get_gemini_model(),
      deps_type=LLMNextNodeDeps,
      output_type=str,
    )

    @next_node_agent.system_prompt
    def add_context(ctx: "RunContext[LLMNextNodeDeps]") -> str:  # type: ignore
      return GENERATE_NEXT_NODE_PROMPT

  return next_node_agent


def build_next_node_prompt(deps: LLMNextNodeDeps) -> str:
  progress_pct = (deps.story_length / deps.story_max_nodes) * 100
  world_facts = "\n".join(f"- {f}" for f in deps.world_facts) if deps.world_facts else "None"
  branch_facts = "\n".join(f"- {f}" for f in deps.branch_facts) if deps.branch_facts else "None"

  # Add custom choice instructions if applicable
  custom_choice_note = ""
  if deps.is_custom_choice:
    custom_choice_note = """
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

  return f"""# CONTEXT
## Recent Text (last 5 nodes):
{deps.previous_text}

## Story Progress: 
{progress_pct:.0f}%

## Story Summary:
{deps.story_summary}

## Player's Choice:
{deps.user_choice}
{custom_choice_note}

## World Facts:
{world_facts}

## Branch Facts (newest first):
{branch_facts}

## Narrator Profile:
{deps.narrator_profile}

## World Info (background—player hasn't seen this):
{format_model_with_descriptions(deps.world_info)}
"""


##################################################################
# F A C T   E X T R A C T I O N
##################################################################

GENERATE_FACT_EXTRACTION_PROMPT = """
You are a fact extraction system for a Choose Your Own Adventure game. Extract facts that ensure narrative consistency.

## Context
You are given the following context:
- The previous story node text
- The user's choice
- The world facts
- The branch facts

Do not extract facts that are already in the context. If a fact in the existing context should be modified or added to, insert a new fact and mark the old fact for deletion.

## Fact Types

### World Facts (static truths)
Facts about the world, characters, items, locations, etc. that are true regardless of player choices:
- Geography, rules of magic/technology, cultural norms
- Named characters' baseline traits (not their current state)
- Historical events or established lore

Examples:
- "The kingdom of Valdris is ruled by Queen Seraphina"
- "Magic requires spoken incantations"
- "The old mine collapsed 20 years ago"

### Branch Facts (choice-dependent truths)
Facts that resulted from player choices, actions, or events and may differ in other story branches:
- Items obtained, allies made, injuries sustained
- Secrets learned, doors opened (or closed)
- Reputation changes, relationship states

Examples:
- "You possess the iron key from the guard captain"
- "Marcus now distrusts you after your lie"
- "Your left arm is wounded"

## Extraction Guidelines
- Be concise: information density over prose quality
- Be self-contained: facts must make sense without the source text
- Be selective: only extract facts likely to matter 2+ nodes later
- Prioritize facts about characters, items, and locations.
- Avoid obvious/trivial facts that won't affect future narrative
- Prefer specific over vague: "You have 3 gold coins" > "You have some money"
"""  # noqa: E501


class LLMFactExtractionDeps(BaseModel):
  text: str = Field(description="The text from the story to extract facts from.")
  user_choice: str | None = Field(description="The user's choice.")


class LLMFactExtraction(BaseModel):
  world_facts: list[str] = Field(description="The world facts extracted from the text.")
  branch_facts: list[str] = Field(description="The branch facts extracted from the text.")


fact_extraction_agent: Any | None = None


def get_fact_extraction_agent() -> "Agent[LLMFactExtractionDeps, LLMFactExtraction]":
  global fact_extraction_agent
  if fact_extraction_agent is None:
    from pydantic_ai import Agent

    fact_extraction_agent = Agent(
      model=get_gemini_model(),
      output_type=LLMFactExtraction,
      deps_type=LLMFactExtractionDeps,
    )

    @fact_extraction_agent.system_prompt
    def add_text_context(ctx: "RunContext[LLMFactExtractionDeps]") -> str:  # type: ignore
      return GENERATE_FACT_EXTRACTION_PROMPT

  return fact_extraction_agent


async def generate_facts_async(deps: LLMFactExtractionDeps) -> LLMFactExtraction:
  """Async version of generate_facts for use in async contexts."""
  prompt = f"""
# STORY TEXT: 
{deps.text}
# USER CHOICE:
{deps.user_choice}
"""
  result = await get_fact_extraction_agent().run(
    prompt,
    deps=deps,
  )
  return result.output


##################################################################
# N A R R A T O R   P R O F I L E
##################################################################

GENERATE_NARRATOR_PROFILE_PROMPT = """
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


class LLMNarratorProfile(BaseModel):
  narrator_profile: str = Field(description="The narrator's profile.")


narrator_profile_agent: Any | None = None


def get_narrator_profile_agent() -> "Agent[LLMWorldInfo, LLMNarratorProfile]":
  global narrator_profile_agent
  if narrator_profile_agent is None:
    from pydantic_ai import Agent

    narrator_profile_agent = Agent(
      model=get_gemini_model(),
      deps_type=LLMWorldInfo,
      output_type=LLMNarratorProfile,
    )

    @narrator_profile_agent.system_prompt
    def add_world_context(ctx: "RunContext[LLMWorldInfo]") -> str:  # type: ignore
      return GENERATE_NARRATOR_PROFILE_PROMPT

  return narrator_profile_agent


async def generate_narrator_profile(deps: LLMWorldInfo) -> LLMNarratorProfile:
  prompt = f"""
# WORLD INFO:
{format_model_with_descriptions(deps)}
"""
  result = await get_narrator_profile_agent().run(
    prompt,
    deps=deps,
  )
  return result.output
