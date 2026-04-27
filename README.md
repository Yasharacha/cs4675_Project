# CS4675 Distributed URL Shortener

A small URL shortener built for CS4675. It supports creating short links,
redirecting through those links, storing mappings in SQLite, and running the
same app behind nginx with two backend containers.

## What Is Included

- Flask URL shortener API
- SQLite persistence
- Docker Compose setup with two app containers and nginx
- Health and backend-node endpoints for checking the deployment
- Benchmark script and saved benchmark artifacts

## Run Locally

Install dependencies:

```bash
uv sync --dev
```

Start the single-node app:

```bash
uv run python run.py
```

The local app runs at:

```text
http://127.0.0.1:5000
```

## Run With Docker

Start the distributed version:

```bash
docker compose up --build -d
```

The nginx load balancer runs at:

```text
http://127.0.0.1:8080
```

Stop the containers:

```bash
docker compose down
```

## API

Create a short URL:

```bash
curl -X POST http://127.0.0.1:5000/api/v1/urls \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

Useful endpoints:

- `GET /<code>` redirects to the original URL
- `GET /api/v1/urls/<code>` returns metadata for a short code
- `GET /api/v1/urls` lists saved URLs
- `GET /api/v1/node` shows which backend handled the request
- `GET /health` checks service health

## Tests

```bash
uv run pytest
```

## Benchmarks

The benchmark runner is in [benchmark.py](benchmark.py).

Example:

```bash
uv run python benchmark.py run-concurrency-study \
  --single-url http://127.0.0.1:5000 \
  --multi-url http://127.0.0.1:8080
```

Saved benchmark outputs are under [benchmarks/](benchmarks/).
