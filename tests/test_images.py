"""Offline regression coverage for Gemini world-cover generation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from google.auth.credentials import AnonymousCredentials
from google.genai import types

from app.core.config import Settings

# Existing LLM agents initialize providers at import time; use no real credentials.
with patch("google.auth.default", return_value=(AnonymousCredentials(), "offline-test")):
  from app.services import images

PNG = b"\x89PNG\r\n\x1a\nfixture"


@pytest.fixture
def image_job(monkeypatch):
  world = SimpleNamespace(id="world-test", save=Mock())
  generate = Mock()
  upload = Mock(return_value="https://cdn.example/cover.png")
  capture = Mock()
  monkeypatch.setattr(images, "world_meta_to_llm_world_info", Mock(return_value="world info"))
  monkeypatch.setattr(images.llm, "generate_image_prompt", AsyncMock(return_value="a distant planet"))
  monkeypatch.setattr(
    images, "_get_genai_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate))
  )
  monkeypatch.setattr(images, "upload_image", upload)
  monkeypatch.setattr(images, "_capture_image_generation", capture)
  return world, generate, upload, capture


def response_with(parts):
  return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=parts))])


def test_generates_uploads_and_persists_png(image_job):
  world, generate, upload, capture = image_job
  generate.return_value = response_with(
    [types.Part(text="cover"), types.Part(inline_data=types.Blob(mime_type="image/png", data=PNG))]
  )
  assert asyncio.run(images.generate_world_image(world)) is world
  kwargs = generate.call_args.kwargs
  assert kwargs["model"] == "gemini-3.1-flash-lite-image"
  assert kwargs["contents"] == ["a distant planet"]
  assert kwargs["config"].response_modalities == ["IMAGE"]
  assert kwargs["config"].image_config.aspect_ratio == "1:1"
  upload.assert_called_once_with(key="worlds/world-test/cover.png", image_bytes=PNG, content_type="image/png")
  assert world.world_image_url == "https://cdn.example/cover.png"
  assert world.world_image_alt_text == "a distant planet"
  assert (world.world_image_width, world.world_image_height) == ("1024", "1024")
  assert world.world_image_size == str(len(PNG))
  world.save.assert_called_once()
  capture.assert_called_once()
  assert "error" not in capture.call_args.kwargs


@pytest.mark.parametrize(
  "response",
  [
    types.GenerateContentResponse(),
    response_with([types.Part(text="blocked")]),
    response_with([types.Part(inline_data=types.Blob(mime_type="image/png"))]),
    response_with([types.Part(inline_data=types.Blob(mime_type="image/png", data=b""))]),
    response_with([types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=b"jpeg"))]),
  ],
)
def test_missing_or_wrong_format_image_does_not_upload_or_save(image_job, response):
  world, generate, upload, capture = image_job
  generate.return_value = response
  with pytest.raises(RuntimeError, match="no PNG image data"):
    asyncio.run(images.generate_world_image(world))
  upload.assert_not_called()
  world.save.assert_not_called()
  assert "no PNG image data" in capture.call_args.kwargs["error"]


def test_api_failure_is_propagated_and_tracked(image_job):
  world, generate, upload, capture = image_job
  generate.side_effect = RuntimeError("model unavailable")
  with pytest.raises(RuntimeError, match="model unavailable"):
    asyncio.run(images.generate_world_image(world))
  upload.assert_not_called()
  world.save.assert_not_called()
  assert capture.call_args.kwargs["error"] == "model unavailable"


def test_cost_estimate_keeps_environment_override(monkeypatch):
  monkeypatch.delenv("IMAGEN_USD_PER_IMAGE", raising=False)
  assert Settings().IMAGEN_USD_PER_IMAGE == 0.017
  monkeypatch.setenv("IMAGEN_USD_PER_IMAGE", "0.034")
  assert Settings().IMAGEN_USD_PER_IMAGE == 0.034
