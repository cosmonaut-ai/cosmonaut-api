# Base Lambda image for Python 3.13
FROM public.ecr.aws/lambda/python:3.13

# Install SSM Parameter & Secrets Extension
# This folder is populated by the GitHub Action. For local builds, see README or download the layer zip manually.
COPY extension_layer /opt

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set uv environment variables
# UV_SYSTEM_PYTHON=1 tells uv to use the system python (Lambda's python)
# UV_COMPILE_BYTECODE=1 speeds up startup times
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

WORKDIR /var/task

# Copy project metadata and lock first to leverage layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies using the lockfile to ensure parity between dev/prod
# We export to requirements.txt to strictly follow the lockfile versions
RUN uv export --format requirements-txt --no-dev --output-file requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt

# Copy application source
COPY app ./app

# Lambda entrypoint (default to API handler)
# Note: For background workers, override the CMD or handler in the Lambda config to 'app.worker.handler'
CMD ["app.main.handler"]

