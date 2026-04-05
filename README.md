# CS4675 Distributed URL Shortener

This repository is being built in stages so the implementation maps cleanly to the final report.

## Stage 1

Stage 1 implements a single-node URL shortener with:

- `POST /api/v1/urls` to create a short URL
- `GET /<code>` to redirect to the original URL
- `GET /api/v1/urls/<code>` to inspect metadata
- `GET /health` for a basic health check
- in-memory storage
- expiration support
- click-count and last-accessed analytics

## Run locally

```bash
python run.py
```

## Run tests

```bash
pytest
```

## Planned stages

1. Single-node service and API contract
2. Persistent storage with database-backed mappings
3. Multi-node deployment with Docker and nginx
4. Fault-tolerance testing, load testing, and performance analysis
