"""Image prompt generation agent.

Crafts an optimized image-generation prompt from world metadata.
The output is a concise, visually descriptive string suitable for Imagen 3.
"""

from pydantic_ai import Agent, RunContext

from app.services.llm.models import LLMWorldInfo
from app.services.llm.provider import get_utility_model
from app.services.llm.utils import format_model_with_descriptions

SYSTEM_PROMPT = """
You craft image generation prompts for a Choose Your Own Adventure game.

## Your Task
Given the world setting and story concept, write a single image-generation prompt that will produce a compelling cover image for this story world.

## Prompt Guidelines
- Write ONE concise paragraph (2-4 sentences, under 100 words total)
- Focus on a single, striking visual scene that captures the world's essence
- Describe atmosphere, lighting, color palette, and composition
- Include the dominant genre aesthetic (fantasy, sci-fi, horror, etc.)
- Reference key locations or environmental details from the world
- Prefer cinematic, painterly, or concept-art styles
- Do NOT include any text, titles, logos, letters, words, or UI elements in the prompt
- Do NOT reference specific characters by name — describe archetypes or silhouettes instead
- Do NOT use meta-instructions like "generate an image of" — just describe the scene directly

## Style Keywords to Consider
Use style descriptors appropriate to the genre:
- Fantasy: epic, painterly, dramatic lighting, rich colors
- Sci-fi: cinematic, volumetric lighting, neon, atmospheric
- Horror: dark, moody, desaturated, fog, chiaroscuro
- Mystery: noir, shadows, muted tones, rain-slicked
- Literary: impressionistic, soft light, watercolor, intimate

## Output
Respond with ONLY the image prompt — no preamble, no explanation, no quotes.
"""  # noqa: E501


# Module-level agent instantiation
_agent: Agent[LLMWorldInfo, str] = Agent(
  model=get_utility_model(),
  deps_type=LLMWorldInfo,
  output_type=str,
  name="image_prompt",
)


@_agent.system_prompt
def _build_system_prompt(ctx: RunContext[LLMWorldInfo]) -> str:
  """Inject world info into system prompt."""
  return f"""{SYSTEM_PROMPT}

# WORLD INFO:
{format_model_with_descriptions(ctx.deps)}
"""


async def generate_image_prompt(deps: LLMWorldInfo) -> str:
  """Generate an optimized image prompt from world metadata."""
  result = await _agent.run(
    "Generate an image prompt that captures this world's visual identity.",
    deps=deps,
  )
  return result.output
