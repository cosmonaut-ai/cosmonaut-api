"""Shared LLM Pydantic models.

These models are separate from DTOs intentionally since they have
LLM-specific field descriptions that guide model generation.
"""

from pydantic import BaseModel, Field


class LLMCharacter(BaseModel):
  """A character in the story world."""

  name: str = Field(description="The name of the character.")
  description: str = Field(description="A short description of the character.")
  relationships: list[str] = Field(description="The relationships the character has with other characters.")


class LLMLocation(BaseModel):
  """A location in the story world."""

  name: str = Field(description="The name of the location.")
  description: str = Field(description="A short description of the location.")
  connections: list[str] = Field(description="The connections the location has with other locations.")


class LLMWorldInfo(BaseModel):
  """Complete world information for story generation."""

  setting: str = Field(description="The setting of the story and the world it is in.")
  backstory: str = Field(description="The background, history, and circumstances that set up this story.")
  characters: list[LLMCharacter] = Field(description="The main characters of the story.")
  locations: list[LLMLocation] = Field(description="The main locations of the story.")
  title: str = Field(description="A title for the story, max 5 words.")
  description: str = Field(description="A short description of the story.")
  genre: str = Field(description="The genre of the story.")
  endings: list[str] = Field(description="A list of potential endings for the story to guide the narrative towards.")


class LLMChoice(BaseModel):
  """A choice available from a story node."""

  label: str = Field(description="The label of the choice.")
  outcome: str = Field(description="The outcome of the choice.")


class LLMStoryNode(BaseModel):
  """A story node with text, choices, and metadata."""

  text: str = Field(description="The text of the story node.")
  choices: list[LLMChoice] = Field(
    description="The choices available from this node. Prefer 2-3 choices; more only when distinct options exist. If ending, provide no choices."
  )
  story_summary: str = Field(description="A summary of the story up to this node.")
  title: str = Field(description="A short 1-5 word title for the story node.")


class LLMNodeMetadata(BaseModel):
  """Metadata extracted from a story node (used for streaming)."""

  choices: list[LLMChoice] = Field(
    description="The choices available from this node. Prefer 2-3 choices; more only when distinct options exist."
  )
  story_summary: str = Field(description="A summary of the story up to this node.")
  title: str = Field(description="A short 1-5 word title for the story node.")


class LLMFactExtraction(BaseModel):
  """Facts extracted from story text."""

  world_facts: list[str] = Field(description="The world facts extracted from the text.")
  branch_facts: list[str] = Field(description="The branch facts extracted from the text.")


class LLMNarratorProfile(BaseModel):
  """Narrator profile for story generation."""

  narrator_profile: str = Field(description="The narrator's profile.")
