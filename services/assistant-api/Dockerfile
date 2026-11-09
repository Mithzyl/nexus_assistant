FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/assistant-api
COPY pyproject.toml docker-constraints.txt ./
COPY src ./src
RUN python -m pip install --no-cache-dir --constraint docker-constraints.txt .

# The development service source, config, key and SQLite are bind-mounted by
# Compose. Host configuration and business data never enter the image layers.
