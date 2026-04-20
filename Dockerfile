FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.2 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH"

COPY app ./app
COPY run.py .
COPY print_db.py .
COPY benchmark.py .

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "run:app"]
