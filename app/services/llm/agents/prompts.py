"""Shared prompt constants for story node generation agents.

Consolidates prompt sections used by both root_node and next_node agents,
plus narrative constraints shared across all generation agents.
"""

NARRATIVE_CONSTRAINTS = """
## Narrative Constraints (STRICT)
- NEVER use these character names: Elara, Kaelen, Elias, Kethric, Thorne
- NEVER use "Oakhaven" as a place name
- Avoid contrastive emphasis patterns. Do NOT overuse constructions like:
  - "X, yet Y" / "X, but Y" / "Despite X, Y" / "X — and yet, Y"
  - "Her voice was soft, yet carried an edge of steel"
  - "The village seemed peaceful, but darkness lurked beneath"
  If you find yourself reaching for "yet", "but", "despite", or "however" to
  juxtapose two qualities, rewrite the sentence using a different structure.
  One or two per node is acceptable; more is not.
"""

PROSE_QUALITY = """
## Prose Quality
- **Show, don't tell** — especially for emotion in second person. Write
  "Your hands tremble" not "You feel afraid." Let actions, dialogue, and
  sensory detail carry the emotional weight.
- **Avoid purple prose** — no stacking adjectives ("the dark, foreboding,
  ancient corridor"). One strong descriptor beats three weak ones.
- **Ban these cliched LLM phrases**:
  - "a wave of...", "a surge of...", "a pang of..."
  - "couldn't help but...", "something ancient and powerful"
  - "the weight of...", "hung heavy in the air"
  - "sent a shiver down...", "a flicker of..."
  If you catch yourself generating any of these, rewrite the sentence.
- **Vary paragraph openings** — do not start every paragraph with atmosphere
  or sensory description. Mix action, dialogue, interiority, and environment.
"""

STORY_TEXT_RULES = """
## Story Text Quality
- Follow the narrator's voice, tone, and style consistently
- NEVER introduce unexplained elements. If it's not in previous nodes, branch
  facts, or world facts, you must explain it. The World Info section is
  background context the player hasn't seen — explain any novel concepts.
- Avoid excessive jargon. Unfamiliar terms or cliches are distracting.
"""

CHOICE_GUIDELINES = """
## Choices
- 2-4 choices that emerge naturally from the scene (no arbitrary "door A vs door B")
- All choices should feel viable — don't telegraph the "correct" answer
- Don't shy away from providing "bad" or "dumb" choices. Let the user fail.
- **Vary choice types**: mix actions, dialogue, observation, and retreat.
  Do not offer choices that are rephrased versions of each other.
- Each choice must be an object with:
  - `label`: A single action or response ("Go left" or "Ask the guard about
    the treasure" or "Investigate the library")
  - `outcome`: A brief description of what happens if this choice is selected
    (1-2 sentences)
"""

METADATA_GUIDELINES = """
## Metadata Fields
- `story_summary`: 2-3 sentences capturing the key events, decisions, and
  current situation. Write for a future node generator — prioritize what it
  needs to maintain continuity, not prose quality.
- `title`: A short evocative title for this node (1-5 words). Prefer concrete
  nouns or actions over abstract concepts ("The Iron Key", "Into the Mine",
  not "A New Beginning").
"""

FAMILY_FRIENDLY_INSTRUCTIONS = """
## Family-Friendly Mode (STRICT)
This story MUST be suitable for children (ages 8+). Follow these rules absolutely:
- NO violence that is graphic, gory, or disturbing. Conflict can exist but
  should be handled with restraint (e.g., characters can be "captured" or
  "knocked down" but not killed gruesomely).
- NO death described in graphic or frightening detail. If a character dies
  it should be handled gently and off-screen where possible.
- NO profanity, crude language, slurs, or innuendo of any kind.
- NO sexual content, romantic tension beyond age-appropriate
  friendship/crushes, or suggestive themes.
- NO horror elements, jump scares, psychological terror, or deeply
  disturbing imagery.
- NO drug or alcohol use, gambling, or other adult vices.
- Use clear, approachable language. Prefer shorter sentences and vocabulary
  accessible to a young reader. Avoid overly complex or literary prose.
- Tone should be adventurous, wonder-filled, and encouraging. Consequences
  for bad choices should be educational rather than traumatic.
- Humor is encouraged. Lighthearted moments help keep the story engaging
  for younger audiences.
"""

OUTPUT_FORMAT = """
## Output Format
Respond using these XML tags in order:

<plan>
[Brief reasoning about the scene]
</plan>
<story>
[The narrative text, addressing player as "you"]
</story>
<metadata>
{"choices": [{"label": "...", "outcome": "..."}, ...], "story_summary": "...", "title": "..."}
</metadata>
"""
