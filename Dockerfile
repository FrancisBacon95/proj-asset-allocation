FROM python:3.10
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 비밀파일·로컬 캐시는 .dockerignore로 제외하고, 런타임에 필요한 파일만 명시적으로 복사한다.
# 비밀(.env, gcp_service_account.json, kis_api_auth.json)은 Cloud Run secret/환경변수로 주입하고
# 토큰 캐시(kis_token_*)는 컨테이너 시작 시 새로 발급된다.
COPY pyproject.toml uv.lock ./
COPY main.py ./
COPY src/ ./src/

# Python 런타임 품질 옵션
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="${PYTHONPATH}:/app" \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies.
RUN uv sync --frozen --no-cache
