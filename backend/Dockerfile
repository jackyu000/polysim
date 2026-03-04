FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN ./.venv/bin/python -c "import httpx"

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

CMD ["./.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
