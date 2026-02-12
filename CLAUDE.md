# CLAUDE.md

## Project

Cosmonaut AI backend — a FastAPI Lambda-based API for interactive branching story generation backed by DynamoDB and Pinecone.

## Linting

Run BasedPyright for type checking:

```sh
pyright
```

Configuration is in `pyrightconfig.json` (strict mode). Many errors from unresolved third-party imports are expected when the venv is not activated.

Run Ruff for formatting and lint:

```sh
ruff check .
ruff format .
```

Configuration is in `pyproject.toml` (line-length=120, indent-width=2, target py313).
