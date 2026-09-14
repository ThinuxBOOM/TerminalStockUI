# Backend image (build context: ./backend).
# Layer order is deps-before-source so code edits reuse the cached pip layer.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Deps first (cached unless requirements.txt changes). Fail fast with a clear
# message if the manifest is missing — silent fallbacks hid broken builds.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Source last (invalidates only the app layer on code edits).
COPY . .

EXPOSE 8000

# Bounded liveness probe (matches docker-compose.yml): 3s socket timeout so a
# refused/slow /health never hangs the interval. Non-200 or connection error
# exits non-zero (unhealthy); no multiline python needed in Dockerfile shell.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=40s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)" || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
