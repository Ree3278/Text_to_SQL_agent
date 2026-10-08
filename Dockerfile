# Recipe for building the image: a frozen box with Python, our code, our dependencies and the data.
# Read top to bottom; each instruction adds one "layer" and Docker caches layers that haven't changed.

# 1. Start from a small official image that already has Python installed.
FROM python:3.12-slim

# 2. Don't write .pyc files, and print logs immediately instead of buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 3. Install dependencies BEFORE copying our code. Dependencies change rarely, code changes often,
#    so Docker can reuse this slow layer on most rebuilds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy only what the running app needs (.dockerignore keeps secrets and junk out).
COPY app ./app
COPY ui ./ui
COPY data/__init__.py data/generate.py ./data/

# 5. Build the synthetic database inside the image. The generator is seeded, so every build produces
#    identical data, and the 160k-row file never has to live in git. The parquet copies are only
#    an intermediate step, so delete them to keep the image small.
RUN python -m data.generate && rm -rf data/parquet

# 6. Where the request log and budget database go. In production this folder is a Fly volume,
#    so it survives restarts and deploys (the rest of the container's disk does not).
ENV STATE_DIR=/data
RUN mkdir -p /data

# 7. Document which port the app listens on, and start it.
#    One worker on purpose: the rate-limit counters live in memory, so a second worker would give
#    every client a second allowance.
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
