# ─────────────────────────────────────────────────────────────────────────────
# KLAUD-NINJA — Production Dockerfile
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Set safe working directory
WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies required by asyncpg and cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy full bot source
COPY . .

# Create data directory for SQLite fallback
RUN mkdir -p /app/data

# Create non-root user for security
RUN useradd -m -u 1000 klaud \
    && chown -R klaud:klaud /app

USER klaud

# Health check — ensures the process is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD pgrep -f "python main.py" || exit 1

# Run the bot
CMD ["python", "main.py"]
