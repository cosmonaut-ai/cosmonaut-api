from google import genai
from google.genai.client import Client
from google.genai.types import GenerateContentResponse

from app.core.config import settings

client: Client | None = None


def get_client() -> Client:
    global client
    if client is None:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return client


def generate_content(prompt: str) -> str | None:
    client = get_client()
    response: GenerateContentResponse = client.models.generate_content(
        model=settings.GEMINI_MODEL, contents=prompt
    )

    return response.text
