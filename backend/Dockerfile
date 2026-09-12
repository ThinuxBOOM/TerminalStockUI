# Minimal backend Dockerfile (placeholder-friendly).
# Backend agent owns backend/ source; this file is only created because it was missing.
# It must not be overwritten by infra once backend takes ownership — backend agent may replace it.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps if requirements.txt exists (tolerates backend agent not having landed it yet).
COPY requirements.txt ./requirements.txt
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; else echo "no requirements.txt yet — skipping pip install"; fi \
    && pip install --no-cache-dir fastapi uvicorn sqlalchemy psycopg[binary] redis pydantic 2>/dev/null || pip install --no-cache-dir fastapi uvicorn

# Backend source lands here (owned by backend agent).
COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
