# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Set the working directory
WORKDIR /app

# Install system dependencies
# Node.js/npm for stream service & UI, curl/unzip/git for tools
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    nodejs \
    npm \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Create directories for mounts to ensure permissions (if needed)
RUN mkdir -p logs data web_ui

# Copy dependency files first for caching
COPY pyproject.toml uv.lock package.json package-lock.json ./
COPY web_ui/requirements.txt web_ui/pyproject.toml ./web_ui/

# Install Node.js dependencies (for stream service)
RUN npm install

# Install Python dependencies using uv
# --frozen ensures we stick to the lockfile
RUN uv sync --frozen --no-install-project

# Copy the rest of the application code
COPY . .

# Install the project itself
RUN uv sync --frozen

# Receive Git Commit SHA from build args
# Placed here to prevent cache invalidation of previous steps
ARG GIT_COMMIT_SHA
ENV GIT_COMMIT_SHA=$GIT_COMMIT_SHA

# Expose ports for Reflex UI
EXPOSE 3000 8000

# Default command (can be overridden)
CMD ["uv", "run", "python", "main.py"]
