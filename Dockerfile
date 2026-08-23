FROM python:3.12-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY arena ./arena
COPY benchmark_sets ./benchmark_sets
# [run] brings the test runner the copied packs invoke. Installing bare `.` left
# the image able to serve the API but unable to execute a single case locally.
RUN pip install --no-cache-dir ".[run]"

EXPOSE 8000
CMD ["arena", "serve", "--host", "0.0.0.0", "--port", "8000"]
