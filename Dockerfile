# Base Lambda image for Python 3.13
FROM public.ecr.aws/lambda/python:3.13

# Install SSM Parameter & Secrets Extension
# This folder is populated by the GitHub Action. For local builds, see README or download the layer zip manually.
COPY extension_layer /opt

# Install uv using the installer script (supports multi-arch)
ADD --chmod=755 https://astral.sh/uv/install.sh /install.sh
RUN /install.sh && rm /install.sh

# Add uv to PATH and set uv environment variables
ENV PATH="/root/.cargo/bin:$PATH"
# UV_SYSTEM_PYTHON=1 tells uv to use the system python (Lambda's python)
# UV_COMPILE_BYTECODE=1 speeds up startup times
# UV_CACHE_DIR ensures uv has a writable cache space during build
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_CACHE_DIR=/tmp/.uv_cache

WORKDIR /var/task

# Copy project metadata and lock first to leverage layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies using the lockfile
# Use uv sync with --no-dev to install only production dependencies
# --frozen ensures we use the exact lockfile versions
# --no-install-project skips installing the project itself (we only want deps)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY app ./app

# Lambda entrypoint (default to API handler)
# Note: For background workers, override the CMD or handler in the Lambda config to 'app.worker.handler'
CMD ["app.main.handler"]

