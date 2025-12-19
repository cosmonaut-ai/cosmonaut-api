# Base Lambda image for Python 3.13
FROM public.ecr.aws/lambda/python:3.13

ENV UV_SYSTEM_PYTHON=1
WORKDIR /var/task

# Copy project metadata and lock (if present) first to leverage layer caching
COPY pyproject.toml uv.lock ./

# Install uv and project dependencies into the Lambda runtime
RUN pip install uv \
    && uv pip install --system -r pyproject.toml

# Copy application source
COPY app ./app

# Lambda entrypoint
CMD ["app.main.handler"]

