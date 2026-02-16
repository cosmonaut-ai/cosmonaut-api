# CLAUDE.md

## Project

Cosmonaut AI backend — a FastAPI Lambda-based API for interactive branching story generation backed by DynamoDB and Pinecone.

## Setup

Create and install the venv (requires Python 3.13+):

```sh
uv venv --python 3.13 .venv
uv pip install -e .
uv pip install boto3-stubs[all] httpx pytest
```

## Linting

Run BasedPyright for type checking (requires venv to resolve imports):

```sh
pyright
```

Configuration is in `pyrightconfig.json` (strict mode).

Run Ruff for formatting and lint:

```sh
ruff check .
ruff format .
```

Configuration is in `pyproject.toml` (line-length=120, indent-width=2, target py313).
