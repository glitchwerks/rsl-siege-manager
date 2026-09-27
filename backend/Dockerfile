FROM python:3.12-slim

# Inject git SHA at build time so the version endpoint can surface it.
# Default to "unknown" when building locally without passing --build-arg.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA
ARG BUILD_NUMBER=unknown
ENV BUILD_NUMBER=$BUILD_NUMBER
ARG FRONTEND_VERSION=unknown
ENV FRONTEND_VERSION=$FRONTEND_VERSION

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries.
# pip install only brings in the Python package; the actual Chromium binary and
# its system-level dependencies (fonts, shared libs) must be fetched separately.
# --with-deps handles apt packages required on Debian slim images.
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

EXPOSE 8000

# Copy and set up entrypoint — runs alembic upgrade head before uvicorn starts.
# This replaces the separate migration CI job and eliminates the need for
# `az containerapp exec` (which requires a TTY unavailable in GitHub Actions).
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
