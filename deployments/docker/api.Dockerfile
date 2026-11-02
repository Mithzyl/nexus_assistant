FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY services/assistant-api/pyproject.toml ./
COPY services/assistant-api/src ./src
COPY services/assistant-api/scripts ./scripts
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home assistant && mkdir /data && chown assistant:assistant /data
USER assistant
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "assistant_api.main:app", "--host", "0.0.0.0", "--port", "8010"]
