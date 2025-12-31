from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from app.core.config import settings
from app.services.secret_manager import get_secret_value

# Note: This model instance isn't strictly used by the Agents below
# (they currently use the model name string), but kept for your existing logic.
model: GoogleModel | None = None
provider: GoogleProvider | None = None


def get_gemini_provider() -> GoogleProvider:
  global provider
  if provider is None:
    provider = GoogleProvider(api_key=get_secret_value(settings.GEMINI_API_KEY_PARAM))
  return provider


def get_gemini_model() -> GoogleModel:
  global model
  if model is None:
    model = GoogleModel(
      model_name=settings.GEMINI_MODEL,
      provider=get_gemini_provider(),
    )
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

## Your Task
Build out these elements:
- **Narrative Context**: The background, history, and circumstances that set up this story
- **World State**: The current situation when the story begins—tensions, opportunities, or conflicts in play
- **Tone & Genre**: Infer the appropriate atmosphere (e.g., dark fantasy, lighthearted sci-fi, gritty noir) from the prompt
- **Potential Endings**: Generate a list of potential endings for the story to guide the narrative towards.

## Guidelines
- Always address the player as "you" to create immediacy
- Be specific—names, places, and details make worlds memorable
- Leave room for player agency; don't predetermine the protagonist's personality or key decisions
- Aim for 2-4 paragraphs of setting detail—enough to ground the story without overwhelming
"""  # noqa: E501


class LLMWorldInfo(BaseModel):
  setting: str = Field(description="The setting of the story and the world it is in.")
  story_title: str = Field(description="A title for the story, max 5 words.")
  story_description: str = Field(description="A short description of the story.")
  potential_endings: list[str] = Field(
    description="A list of potential endings for the story to guide the narrative towards."
  )


world_info_agent: None | Agent[None, LLMWorldInfo] = None


def get_world_info_agent() -> Agent[None, LLMWorldInfo]:
  global world_info_agent
  if world_info_agent is None:
    world_info_agent = Agent(
      model=get_gemini_model(),
      system_prompt=GENERATE_WORLD_INFO_PROMPT,
      output_type=LLMWorldInfo,
    )
  return world_info_agent


async def generate_world_info(world_prompt: str) -> LLMWorldInfo:
  # The world_prompt becomes the User message
  result = await get_world_info_agent().run(world_prompt)
  return result.output


##################################################################
# G E N E R A T E   S T A R T   N O D E
##################################################################

GENERATE_START_NODE_PROMPT = """
You are a storyteller for a Choose Your Own Adventure game. Generate the opening story node that hooks the player.

## Your Task
Create an engaging first node that:
- Establishes the immediate situation with sensory detail
- Introduces a compelling hook or initial tension
- Presents the player with their first meaningful choices

## Choice Design Guidelines
- Provide 2-4 distinct choices that feel meaningfully different
- Each choice should emerge naturally from the narrative (no "left door vs. right door" without context)
- Mix choice types: cautious vs. bold, investigative vs. action-oriented
- Avoid giving away which choices are "correct"—all should feel viable

## Writing Guidelines
- Address the player as "you" throughout
- Open in media res when possible—action or intrigue, not lengthy exposition
- Keep text to 2-4 paragraphs
- The title should be evocative and specific to this moment
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


root_node_agent: None | Agent[LLMRootNodeDeps, LLMStoryNode] = None


def get_root_node_agent() -> Agent[LLMRootNodeDeps, LLMStoryNode]:
  global root_node_agent
  if root_node_agent is None:
    root_node_agent = Agent(
      model=get_gemini_model(),
      deps_type=LLMRootNodeDeps,
      output_type=LLMStoryNode,
    )

    @root_node_agent.system_prompt
    def add_world_context(ctx: RunContext[LLMRootNodeDeps]) -> str:  # type: ignore
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
You are a Choose Your Own Adventure storyteller. Continue the narrative based on the player's choice.

## Consequences
- Honor the player's choice with meaningful consequences—risky choices carry real risk; clever ones are rewarded
- If the choice leading to this node is a "bad" choice, punish the player by ending the story or negative consequences. This should be a very difficult story to complete.

## Choice Design Guidelines
- Provide 2-4 distinct choices that feel meaningfully different
- Each choice should emerge naturally from the narrative (no "left door vs. right door" without context)
- Avoid giving away which choices are "correct"—all should feel viable

## Writing Guidelines
- Address the player as "you" throughout
- Obey the narrator's profile as closely as possible.
- Keep text to 2-4 paragraphs
- The title should be evocative and specific to this moment

## IMPORTANT: OUTPUT FORMAT
You must output the response in two distinct parts using XML-style tags.
1. First, write the story text inside <story> tags.
2. Second, write the metadata (choices, story_summary, title) as a JSON object inside <metadata> tags.

Example Format:
<story>
The door creaks open and you step into the darkness...
</story>
<metadata>
{
  "choices": ["Enter the room", "Run away"],
  "story_summary": "The player opened the mysterious door.",
  "title": "The Dark Room"
}
</metadata>
"""  # noqa: E501


class LLMNextNodeDeps(BaseModel):
  world_info: LLMWorldInfo
  previous_text: str = Field(description="The previous story node text.")
  user_choice: str = Field(description="The user's choice.")
  world_facts: list[str] = Field(
    description="Facts about the world that may or may not be relevant to the next story node."
  )
  branch_facts: list[str] = Field(
    description="Facts about the story up to this point that may or may not be relevant to the next story node."  # noqa: E501
  )
  similar_nodes: list[str] = Field(description="Similar story nodes that are relevant to the next story node.")
  narrator_profile: str = Field(description="The narrator's profile.")


next_node_agent: None | Agent[LLMNextNodeDeps, str] = None


def get_next_node_agent() -> Agent[LLMNextNodeDeps, str]:
  global next_node_agent
  if next_node_agent is None:
    next_node_agent = Agent(
      model=get_gemini_model(),
      deps_type=LLMNextNodeDeps,
      output_type=str,
    )

    @next_node_agent.system_prompt
    def add_context(ctx: RunContext[LLMNextNodeDeps]) -> str:  # type: ignore
      return GENERATE_NEXT_NODE_PROMPT

  return next_node_agent


def build_next_node_prompt(deps: LLMNextNodeDeps) -> str:
  return f"""
Given the following context, generate the next story node.
# CONTEXT:

## Previous Node Text:
{deps.previous_text}

## Similar Nodes:
These are story nodes from other choice branches that you should use to ensure consistency within
the world and story.

{"\n\n".join(deps.similar_nodes)}

## User Choice:
{deps.user_choice}

## World Facts:
- {chr(10).join(f"- {f}" for f in deps.world_facts)}

## Branch Facts:
(ordered from newest to oldest, with the newest being first)
- {chr(10).join(f"- {f}" for f in deps.branch_facts)}

## Narrator Profile:
{deps.narrator_profile}

## World Info:
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


fact_extraction_agent: None | Agent[LLMFactExtractionDeps, LLMFactExtraction] = None


def get_fact_extraction_agent() -> Agent[LLMFactExtractionDeps, LLMFactExtraction]:
  global fact_extraction_agent
  if fact_extraction_agent is None:
    fact_extraction_agent = Agent(
      model=get_gemini_model(),
      output_type=LLMFactExtraction,
      deps_type=LLMFactExtractionDeps,
    )

    @fact_extraction_agent.system_prompt
    def add_text_context(ctx: RunContext[LLMFactExtractionDeps]) -> str:  # type: ignore
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


narrator_profile_agent: None | Agent[LLMWorldInfo, LLMNarratorProfile] = None


def get_narrator_profile_agent() -> Agent[LLMWorldInfo, LLMNarratorProfile]:
  global narrator_profile_agent
  if narrator_profile_agent is None:
    narrator_profile_agent = Agent(
      model=get_gemini_model(),
      deps_type=LLMWorldInfo,
      output_type=LLMNarratorProfile,
    )

    @narrator_profile_agent.system_prompt
    def add_world_context(ctx: RunContext[LLMWorldInfo]) -> str:  # type: ignore
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
