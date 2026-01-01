# Base Lambda image for Python 3.13
FROM public.ecr.aws/lambda/python:3.13

# --- ADD THIS: Install SSM Parameter & Secrets Extension ---
# For us-east-2, the extension images are:
# x86_64: 590474943231.dkr.ecr.us-east-2.amazonaws.com/aws-parameters-and-secrets-lambda-extension:25
# arm64:  590474943231.dkr.ecr.us-east-2.amazonaws.com/aws-parameters-and-secrets-lambda-extension-arm64:25
# Since we are using arm64:
COPY --from=590474943231.dkr.ecr.us-east-2.amazonaws.com/aws-parameters-and-secrets-lambda-extension-arm64:25 /opt /opt
# -----------------------------------------------------------

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

