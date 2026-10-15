FROM python:3.11-slim

WORKDIR /app

# Prevent prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies: Xvfb, X11 utilities, and Chromium runtime requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11-utils \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Patchright's Chromium binary
RUN python -m patchright install chromium

COPY . .

# Render exposes the PORT env var (defaults to 10000 or 8000)
ENV PORT=10000
EXPOSE 10000

# Start Xvfb virtual display in background, then run uvicorn
CMD sh -c "Xvfb :99 -screen 0 1280x800x24 -ac > /dev/null 2>&1 & DISPLAY=:99 uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"
