# CS4675 Distributed URL Shortener

This repository is being built in stages so the implementation maps cleanly to the final report.

## Stage 1

Stage 1 implements a single-node URL shortener with:

- `POST /api/v1/urls` to create a short URL
- `GET /<code>` to redirect to the original URL
- `GET /api/v1/urls/<code>` to inspect metadata
- `GET /health` for a basic health check
- expiration support
- click-count and last-accessed analytics

## Stage 2

Stage 2 replaces the in-memory store with SQLite persistence so URL mappings survive server restarts and can be shared by multiple app instances in later stages.

You can override the database path by passing `DATABASE_PATH` into `create_app(...)` during tests or future deployment configuration.

## Run locally

```bash
python run.py
```

This creates or reuses a SQLite database at `data/url_shortener.db`.

To verify persistence manually:

1. Start the server with `python run.py`
2. Create a short URL with the API
3. Stop the server
4. Start the server again
5. Request the same short code and confirm it still resolves

## Run tests

```bash
pytest
```

## Planned stages

1. Single-node service and API contract
2. Persistent storage with database-backed mappings
3. Multi-node deployment with Docker and nginx
4. Fault-tolerance testing, load testing, and performance analysis
