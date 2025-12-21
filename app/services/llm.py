import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel

from app.core.config import settings

model: GoogleModel = GoogleModel(
    model_name=settings.GEMINI_MODEL,
)

agent: Agent = Agent(model=model)


async def main():
    result = await agent.run("Explain how AI works in a few words")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
