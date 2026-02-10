"""Pre-defined voice registry for TTS audio narration.

Each voice maps a public-facing ``id`` (used in API requests and S3 storage
keys) to an internal ElevenLabs ``elevenlabs_voiceid``.  The ElevenLabs
identifiers are never exposed through the API.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings

S3_VOICE_SAMPLE_KEY_TEMPLATE = "voices/{voice_id}/sample.mp3"


@dataclass(frozen=True)
class Voice:
  """A single TTS voice option."""

  id: str
  display_name: str
  description: str
  elevenlabs_voiceid: str

  @property
  def sample_url(self) -> str:
    """Public CDN URL for the voice sample MP3."""
    key = S3_VOICE_SAMPLE_KEY_TEMPLATE.format(voice_id=self.id)
    return f"https://{settings.STATIC_CONTENT_CDN_DOMAIN}/{key}"


VOICES: list[Voice] = [
  Voice(
    id="jane",
    display_name="Jane",
    description="A strong, older voice with a warm presence.",
    elevenlabs_voiceid="RILOU7YmBhvwJGDGjNmP",
  ),
  Voice(
    id="katherine",
    display_name="Katherine",
    description="A smooth, British-accented voice with refined elegance.",
    elevenlabs_voiceid="NtS6nEHDYMQC9QczMQuq",
  ),
  Voice(
    id="paige",
    display_name="Paige",
    description="A clear, midrange voice with an engaging cadence.",
    elevenlabs_voiceid="NDTYOmYEjbDIVCKB35i3",
  ),
  Voice(
    id="peter",
    display_name="Peter",
    description="A deep, bassy voice with a gravelly texture.",
    elevenlabs_voiceid="ZthjuvLPty3kTMaNKVKb",
  ),
  Voice(
    id="theo",
    display_name="Theo",
    description="A smooth, composed male voice with quiet confidence.",
    elevenlabs_voiceid="jfIS2w2yJi0grJZPyEsk",
  ),
  Voice(
    id="michael",
    display_name="Michael",
    description="A strong, commanding male voice with rich depth.",
    elevenlabs_voiceid="uju3wxzG5OhpWcoi3SMy",
  ),
  Voice(
    id="jon",
    display_name="Jon",
    description="A balanced, everyday male voice with an approachable feel.",
    elevenlabs_voiceid="MFZUKuGQUsGJPQjTS4wC",
  ),
  Voice(
    id="josh",
    display_name="Josh",
    description="A youthful male voice, perfect for lighthearted adventures.",
    elevenlabs_voiceid="nzFihrBIvB34imQBuxub",
  ),
]

_VOICE_BY_ID: dict[str, Voice] = {v.id: v for v in VOICES}


def get_voice_by_id(voice_id: str) -> Voice | None:
  """Return the :class:`Voice` matching *voice_id*, or ``None``."""
  return _VOICE_BY_ID.get(voice_id)


def get_all_voices() -> list[Voice]:
  """Return the full list of available voices."""
  return list(VOICES)
