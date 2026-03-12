# Cosmonaut API

FastAPI backend for Cosmonaut AI — an interactive choose-your-own-adventure storytelling platform. Runs as AWS Lambda functions behind API Gateway.

## Stack

- **Python 3.13**, **FastAPI**, **Mangum** (Lambda adapter)
- **DynamoDB** (single-table design via PynamoDB)
- **Pinecone** (vector search for story facts)
- **Vertex AI** (Gemini + Claude via Google Cloud) for story generation
- **ElevenLabs** for text-to-speech audio narration
- **Stripe** for subscription billing
- **AWS Cognito** for authentication (JWT validation)

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- AWS CLI configured with credentials
- GCP credentials for Vertex AI (service account JSON or ADC)

## Setup

```bash
# Create venv and install dependencies
uv venv --python 3.13 .venv
uv sync

# Copy and fill in environment variables
cp .env.example .envrc
# Edit .envrc with your values, then:
direnv allow
# Or: source .envrc

# Run locally
uv run uvicorn app.main:app --reload --port 8000
```

## Linting & Type Checking

```bash
# Ruff (lint + format)
uv run ruff check .
uv run ruff format .

# BasedPyright (strict type checking)
uv run --with basedpyright basedpyright
```

Configuration: `pyproject.toml` (Ruff), `pyrightconfig.json` (Pyright).

## Project Structure

```
app/
├── api/              # FastAPI route handlers
│   ├── auth.py       # Session, checkout, billing, account deletion
│   ├── worlds.py     # World CRUD, sharing
│   ├── story_nodes.py# Node generation, choices, audio
│   ├── webhooks.py   # Stripe webhook handler
│   ├── meta.py       # Public OG metadata for social bots
│   └── voices.py     # TTS voice listing
├── core/             # Configuration, security, observability
├── models/           # DynamoDB entities and DTOs
├── services/         # Business logic and external integrations
│   ├── llm/          # Multi-agent LLM pipeline
│   │   ├── agents/   # Story agents (root_node, next_node, world_info, etc.)
│   │   ├── provider.py  # Vertex AI client with retry
│   │   └── sanitize.py  # Prompt injection defense
│   ├── audio.py      # ElevenLabs TTS
│   ├── stripe_client.py
│   ├── usage.py      # Tier quota enforcement
│   └── ...
├── main.py           # FastAPI app entrypoint
└── worker.py         # SQS Lambda worker entrypoint
```

## Deployment

Push to `main` (prod) or `develop` (dev) triggers GitHub Actions:
Docker build → ECR push → Lambda function code update (4 functions: api, worker-fast, worker-slow, api-streaming).

See `ARCHITECTURE.md` at the workspace root for the full system overview.
