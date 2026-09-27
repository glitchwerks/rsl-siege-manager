FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

# Inject build metadata at image build time so the /version endpoint can
# surface the full version string (e.g. 1.0.1+42.abc1234).
# Default to "unknown" when building locally without passing --build-arg.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA
ARG BUILD_NUMBER=unknown
ENV BUILD_NUMBER=$BUILD_NUMBER
ENV PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install the exact production dependency graph from the committed lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

EXPOSE 8001

CMD ["python", "-m", "app.main"]
