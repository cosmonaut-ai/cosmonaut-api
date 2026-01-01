# syntax=docker/dockerfile:1.4
# Base Lambda image for Python 3.13
FROM public.ecr.aws/lambda/python:3.13

# Install SSM Parameter & Secrets Extension
# This folder is populated by the GitHub Action. For local builds, see README or download the layer zip manually.
COPY extension_layer /opt

# Install uv by copying from the official Docker image
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

# Set uv environment variables
# UV_SYSTEM_PYTHON=1 tells uv to use the system python (Lambda's python)
# UV_COMPILE_BYTECODE=1 speeds up startup times
# UV_LINK_MODE=copy required for cache mount to work correctly
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /var/task

# Copy project metadata and lock first to leverage layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies using BuildKit cache mount for uv's cache
# This persists the cache between builds, dramatically speeding up rebuilds
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY app ./app

# Lambda entrypoint (default to API handler)
# Note: For background workers, override the CMD or handler in the Lambda config to 'app.worker.handler'
CMD ["app.main.handler"]
