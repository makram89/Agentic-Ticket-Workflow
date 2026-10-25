FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY ticketflow ./ticketflow
RUN pip install --no-cache-dir .

# Run as an unprivileged user (port 8000 is unprivileged; nothing writes under /app).
RUN useradd --system --no-create-home --uid 10001 app
USER app

# The command (api or worker) is chosen in docker-compose.yml.
CMD ["uvicorn", "ticketflow.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
