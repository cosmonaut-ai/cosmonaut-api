from google import genai
from google.genai.client import Client
from google.genai.types import GenerateContentResponse

from app.core.config import settings

client: Client = genai.Client(api_key=settings.GEMINI_API_KEY)

response: GenerateContentResponse = client.models.generate_content(
    model=settings.GEMINI_MODEL, contents="Explain how AI works in a few words"
)
print(response.text)
