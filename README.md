# Cosmonaut API

FastAPI backend for [Cosmonaut AI](https://cosmonaut-ai.com), an AI-powered interactive storytelling platform. This service owns story generation, authenticated API routes, streaming generation, billing, media generation, and background workers.

The API is designed to run locally for development and in AWS Lambda behind API Gateway for deployed environments.

## Repository Role

Cosmonaut is split across several public repositories:

- [`cosmonaut-web`](https://github.com/cosmonaut-ai/cosmonaut-web): SvelteKit frontend.
- [`cosmonaut-api`](https://github.com/cosmonaut-ai/cosmonaut-api): Backend API and workers.
- [`cosmonaut-infra`](https://github.com/cosmonaut-ai/cosmonaut-infra): Terraform infrastructure.
- [`cosmonaut-android`](https://github.com/cosmonaut-ai/cosmonaut-android): Native Android client.

## Stack

- Python 3.13, FastAPI, Mangum, AWS Lambda
- DynamoDB via PynamoDB
- SQS-backed background workers
- Pinecone for vector memory
- Vertex AI / Gemini for story generation
- ElevenLabs for text-to-speech narration
- Stripe for subscription billing
- AWS Cognito JWT validation
- PostHog and Sentry for observability

## Local Setup

Prerequisites:

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- AWS credentials if you need to touch AWS-backed resources
- GCP credentials if you are exercising Vertex-backed generation locally

```bash
uv venv --python 3.13 .venv
uv sync
cp .env.example .envrc
```

Fill in `.envrc`, then load it with `direnv allow` or `source .envrc`.

For local development without Cognito, keep `MOCK_AUTH=true`. Some routes still depend on configured local AWS, Pinecone, Stripe, ElevenLabs, or Vertex resources when those integrations are exercised.

Run the API locally:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

## Verification

Run these checks before opening a pull request:

```bash
ruff format .
ruff check --fix .
ty check
```

The type checker is [`ty`](https://github.com/astral-sh/ty), configured in `pyproject.toml`. Run it from the repository root so it picks up the project configuration and virtual environment.

## Project Structure

```text
app/
├── api/               # FastAPI route handlers
├── core/              # Config, auth, observability, PostHog/Sentry helpers
├── models/            # DynamoDB models and API DTOs
├── services/          # Business logic and external integrations
│   ├── llm/           # Story-generation agents and provider code
│   ├── audio.py       # ElevenLabs narration
│   ├── stripe_client.py
│   └── usage.py       # Subscription quota enforcement
├── main.py            # FastAPI app entry point
└── worker.py          # SQS Lambda worker entry point
```

## Documentation

Start with [`docs/README.md`](docs/README.md). The most useful references are:

- [`docs/api-spec.md`](docs/api-spec.md): public API behavior and DTOs.
- [`docs/audio-implementation.md`](docs/audio-implementation.md): narration API and frontend integration notes.
- [`docs/frontend-world-options.md`](docs/frontend-world-options.md): world creation option contract.
- [`docs/tech-spec.md`](docs/tech-spec.md): system design and generation loop.

## Deployment

GitHub Actions deploys pushes to `main` as production and `develop` as development. Deployment builds a Docker image, pushes it to ECR, then updates the API, streaming API, fast worker, and slow worker Lambda functions.

Documentation-only commits should use `[skip ci]` when they do not need a deployment.

## Security

Do not put raw credentials in source. Local configuration should stay in ignored files, runtime secrets should live in AWS SSM Parameter Store, and GitHub Actions secrets should be used only for CI/CD credentials.

See [`SECURITY.md`](SECURITY.md) for disclosure and secret-handling guidance.

## Contributing

Issues and pull requests are welcome. Please keep changes focused, include tests or validation output for behavior changes, and avoid committing generated artifacts.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
