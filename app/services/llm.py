from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel

from app.core.config import settings

# Note: This model instance isn't strictly used by the Agents below
# (they currently use the model name string), but kept for your existing logic.
model: GoogleModel | None = None


def get_gemini_model() -> GoogleModel:
  global model
  if model is None:
    model = GoogleModel(
      model_name=settings.GEMINI_MODEL,
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
You are a world-building expert for a Choose Your Own Adventure game. Given a short prompt describing the world or the story, build out a detailed description of the world.

The world info should include:
- The context of the narrative (context and background of the story)
- The main characters
- Potential plot points
- Endings that the user should be guided towards
"""  # noqa: E501


class LLMWorldInfo(BaseModel):
  setting: str = Field(description="The setting of the story and the world it is in.")
  characters: list[str] = Field(description="The characters in the story and their roles.")
  potential_endings: list[str] = Field(description="3-6 potential endings of the story.")
  story_background: str = Field(description="The background and context leading up to the story")
  story_title: str = Field(description="A title for the story, max 5 words.")
  story_description: str = Field(description="A short description of the story.")


world_info_agent: None | Agent[None, LLMWorldInfo] = None


def get_world_info_agent() -> Agent[None, LLMWorldInfo]:
  global world_info_agent
  if world_info_agent is None:
    world_info_agent = Agent(
      settings.GEMINI_MODEL,
      system_prompt=GENERATE_WORLD_INFO_PROMPT,
      output_type=LLMWorldInfo,
    )
  return world_info_agent


def generate_world_info(world_prompt: str) -> LLMWorldInfo:
  # The world_prompt becomes the User message
  result = get_world_info_agent().run_sync(world_prompt)
  return result.output


##################################################################
# G E N E R A T E   S T A R T   N O D E
##################################################################

GENERATE_START_NODE_PROMPT = """
You are a storyteller for a Choose Your Own Adventure game. Given a detailed description of the world, generate the first story node.

All choices should come from the generated text of the story node - don't reference external context that is not in the generated text.

The story node should include:
- The introduction to the story
- The choices available from this node
- A short summary of the story up to this node
- A title for the story node
"""  # noqa: E501


class LLMStoryNode(BaseModel):
  text: str = Field(description="The text of the story node.")
  choices: list[str] = Field(
    description="The choices available from this node. (2-4 choices). If this is an end to the story, provide no choices."  # noqa: E501
  )
  story_summary: str = Field(description="A summary of the story up to this node.")
  title: str = Field(description="A short 1-5 word title for the story node.")


root_node_agent: None | Agent[LLMWorldInfo, LLMStoryNode] = None


def get_root_node_agent() -> Agent[LLMWorldInfo, LLMStoryNode]:
  global root_node_agent
  if root_node_agent is None:
    root_node_agent = Agent(
      settings.GEMINI_MODEL,
      deps_type=LLMWorldInfo,
      output_type=LLMStoryNode,
    )

    @root_node_agent.system_prompt
    def add_world_context(ctx: RunContext[LLMWorldInfo]) -> str:  # type: ignore
      # Inject dependencies explicitly into the prompt
      return f"""{GENERATE_START_NODE_PROMPT}

            ---
            WORLD CONTEXT:
            {format_model_with_descriptions(ctx.deps)}
            """

  return root_node_agent


def generate_start_node(world_info: LLMWorldInfo) -> LLMStoryNode:
  result = get_root_node_agent().run_sync(
    "Generate the first story node.",  # Dummy user prompt, actual instructions are in system prompt
    deps=world_info,
  )
  return result.output


##################################################################
# G E N E R A T E   N E X T   N O D E
##################################################################

GENERATE_NEXT_NODE_PROMPT = """
You are a storyteller for a Choose Your Own Adventure game. Given a detailed description of the world, the previous story node, and the user's choice, generate the next story node.

All choices should come from the generated text of the story node - don't reference external context that is not in the generated text.

Do not attempt to accommodate the user's choice. If the user's choice is likely to have a negative impact or end the story, end the story or play out a negative outcome. In general, around half of the choices should be negative or end the story.

Given the following context:
- The detailed description of the world
- The previous story node text
- The user's choice
- Facts about the world that may or may not be relevant to the next story node
- Facts about the story up to this point that may or may not be relevant to the next story node

Generate the next story node.
"""  # noqa: E501


class LLMNextNodeDeps(BaseModel):
  world_info: LLMWorldInfo
  previous_node: str = Field(description="The previous story node text.")
  user_choice: str = Field(description="The user's choice.")
  world_facts: list[str] = Field(
    description="Facts about the world that may or may not be relevant to the next story node."
  )
  branch_facts: list[str] = Field(
    description="Facts about the story up to this point that may or may not be relevant to the next story node."  # noqa: E501
  )


next_node_agent: None | Agent[LLMNextNodeDeps, LLMStoryNode] = None


def get_next_node_agent() -> Agent[LLMNextNodeDeps, LLMStoryNode]:
  global next_node_agent
  if next_node_agent is None:
    next_node_agent = Agent(
      settings.GEMINI_MODEL,
      deps_type=LLMNextNodeDeps,
      output_type=LLMStoryNode,
    )

    @next_node_agent.system_prompt
    def add_context(ctx: RunContext[LLMNextNodeDeps]) -> str:  # type: ignore
      d = ctx.deps
      return f"""{GENERATE_NEXT_NODE_PROMPT}

            ---
            CONTEXT:
            Previous Node Text: {d.previous_node}
            // The previous story node text.

            User Choice: {d.user_choice}
            // The user's choice.

            World Facts:
            // Facts about the world that may or may not be relevant to the next story node.
            {chr(10).join(f"- {f}" for f in d.world_facts)}

            Branch Facts:
            // Facts about the story up to this point that may or may not be relevant to the next
            // story node.
            {chr(10).join(f"- {f}" for f in d.branch_facts)}

            World Info:
            {format_model_with_descriptions(d.world_info)}
            """

  return next_node_agent


def generate_next_node(deps: LLMNextNodeDeps) -> LLMStoryNode:
  result = get_next_node_agent().run_sync(
    "Generate the next node.",  # Dummy user prompt
    deps=deps,
  )
  return result.output


##################################################################
# F A C T   E X T R A C T I O N
##################################################################

GENERATE_FACT_EXTRACTION_PROMPT = """
You are a fact extraction expert for a Choose Your Own Adventure game. Given text from the story, extract the facts that are relevant to the story.

You should look for two types of facts:
- *World Facts*: Facts about the world that can be extracted from the text. These facts should be static - they should not be subject to change based on the user's choices.
- *Branch Facts*: Facts that are a direct result of the user's choices that may not be true across all branches of the story.

For example, if the text says "You open the chest and find a treasure.", the fact "You found a treasure" is a branch fact because it is not true across all branches of the story.
If the text says "The sky is blue.", the fact "The sky is blue" is a world fact because it is true across all branches of the story.
"""  # noqa: E501


class LLMFactExtractionDeps(BaseModel):
  text: str = Field(description="The text from the story to extract facts from.")
  user_choice: str = Field(description="The user's choice.")


class LLMFactExtraction(BaseModel):
  world_facts: list[str] = Field(description="The world facts extracted from the text.")
  branch_facts: list[str] = Field(description="The branch facts extracted from the text.")


fact_extraction_agent: None | Agent[LLMFactExtractionDeps, LLMFactExtraction] = None


def get_fact_extraction_agent() -> Agent[LLMFactExtractionDeps, LLMFactExtraction]:
  global fact_extraction_agent
  if fact_extraction_agent is None:
    fact_extraction_agent = Agent(
      settings.GEMINI_MODEL,
      output_type=LLMFactExtraction,
      deps_type=LLMFactExtractionDeps,
    )

    @fact_extraction_agent.system_prompt
    def add_text_context(ctx: RunContext[LLMFactExtractionDeps]) -> str:  # type: ignore
      return f"""{GENERATE_FACT_EXTRACTION_PROMPT}
            
            ---
            CONTENT TO ANALYZE:
            Story Text: {ctx.deps.text}
            User Choice: {ctx.deps.user_choice}
            """

  return fact_extraction_agent


def generate_facts(deps: LLMFactExtractionDeps) -> LLMFactExtraction:
  result = get_fact_extraction_agent().run_sync(
    "Extract facts.",  # Dummy user prompt
    deps=deps,
  )
  return result.output
