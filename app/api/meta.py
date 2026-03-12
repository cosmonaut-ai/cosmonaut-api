"""Public meta endpoints for social media bot Open Graph scraping."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Path
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.observability import logger
from app.services.worlds import WorldNotFoundError, get_world_entity

router = APIRouter(prefix="/meta", tags=["meta"])

_DEFAULT_TITLE = "Cosmonaut AI"
_DEFAULT_DESCRIPTION = "An AI-powered interactive story world."


def _cdn_url(path: str) -> str:
  """Build a full CDN URL for a static asset."""
  return f"https://{settings.STATIC_CONTENT_CDN_DOMAIN}/{path}"


def _build_og_html(
  *,
  canonical_url: str,
  title: str,
  description: str,
  image_url: str,
) -> str:
  """Build a minimal HTML page containing Open Graph and Twitter Card meta tags."""

  safe_title = escape("Cosmonaut World: " + title)
  safe_desc = escape(description)
  safe_image = escape(image_url)
  safe_url = escape(canonical_url)
  favicon_url = escape(_cdn_url("meta/favicon.png"))

  return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{safe_title}</title>
  <link rel="icon" type="image/png" href="{favicon_url}">

  <meta property="og:type" content="website" />
  <meta property="og:url" content="{safe_url}" />
  <meta property="og:title" content="{safe_title}" />
  <meta property="og:description" content="{safe_desc}" />
  <meta property="og:site_name" content="Cosmonaut AI" />
  <meta property="og:image" content="{safe_image}" />

  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{safe_title}" />
  <meta name="twitter:description" content="{safe_desc}" />
  <meta name="twitter:image" content="{safe_image}" />

  <meta http-equiv="refresh" content="0; url={safe_url}">
</head>
<body>
  <p>Redirecting to <a href="{safe_url}">{safe_title}</a>...</p>
</body>
</html>"""


@router.get("/worlds/{world_id}", response_class=HTMLResponse, summary="OG metadata for a world")
async def get_world_meta(world_id: str = Path(..., description="Identifier for the world")) -> HTMLResponse:
  """Return a lightweight HTML page with Open Graph tags for social media link previews.

  This endpoint is public (no auth) because social media bots cannot authenticate.
  It always returns 200 -- bots handle non-200 responses poorly, so a fallback page
  with generic branding is returned when the world is not found.
  """

  canonical_url = f"https://{settings.FRONTEND_DOMAIN}/worlds/{world_id}"

  default_image = _cdn_url("meta/og-default.png")

  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError:
    logger.info(f"OG meta requested for unknown world {world_id}, returning fallback")
    html = _build_og_html(
      canonical_url=canonical_url,
      title=_DEFAULT_TITLE,
      description=_DEFAULT_DESCRIPTION,
      image_url=default_image,
    )
    return HTMLResponse(content=html)

  title = world.title or _DEFAULT_TITLE
  description = world.description or _DEFAULT_DESCRIPTION
  image_url = world.world_image_url or default_image

  html = _build_og_html(
    canonical_url=canonical_url,
    title=title,
    description=description,
    image_url=image_url,
  )
  return HTMLResponse(content=html)
