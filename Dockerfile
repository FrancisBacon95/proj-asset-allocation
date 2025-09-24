FROM python:3.10
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy the necessary files to the working directory
COPY . .

# Python 런타임 품질 옵션
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="${PYTHONPATH}:/app" \
    PATH="/app/.venv/bin:$PATH"

# Debug: List files in /app and print PYTHONPATH

RUN ls -R /app

RUN echo $PYTHONPATH

# Install dependencies.
RUN uv sync --frozen --no-cache