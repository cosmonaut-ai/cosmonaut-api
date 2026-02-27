"""Fact extraction agent.

Extracts world and branch facts from story text for narrative consistency.
Uses structured output with deps properly injected into system prompt.
"""

import logging

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.services.llm.models import LLMFactExtraction
from app.services.llm.provider import get_utility_model

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are a fact extraction system for a Choose Your Own Adventure game. Extract facts that ensure narrative consistency.

## Context
You are given the following context:
- The previous story node text
- The user's choice
- The world facts
- The branch facts

Do not extract facts that are already in the context.

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


class FactExtractionDeps(BaseModel):
  """Dependencies for fact extraction."""

  text: str = Field(description="The text from the story to extract facts from.")
  user_choice: str | None = Field(description="The user's choice.")


# Module-level agent instantiation
_agent: Agent[FactExtractionDeps, LLMFactExtraction] = Agent(
  model=get_utility_model(),
  deps_type=FactExtractionDeps,
  output_type=LLMFactExtraction,
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[FactExtractionDeps]) -> str:  # pyright: ignore[reportUnusedFunction]
  """Inject story text and user choice into system prompt."""
  return f"""{SYSTEM_PROMPT}

# STORY TEXT:
{ctx.deps.text}

# USER CHOICE:
{ctx.deps.user_choice}
"""


async def generate_facts_async(deps: FactExtractionDeps) -> LLMFactExtraction:
  """Extract facts from story text asynchronously."""
  result = await _agent.run(
    "Extract facts from the story text.",
    deps=deps,
  )

  logger.debug(f"Fact extraction result: {result.output}")
  return result.output


# Re-export for backward compatibility
LLMFactExtractionDeps = FactExtractionDeps
