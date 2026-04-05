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

## Print Database Contents

To print every stored URL mapping from the local SQLite database:

```bash
python print_db.py
```

If you want to point at a different database file:

```bash
DATABASE_PATH=some/other/file.db python print_db.py
```

If you are running the distributed Docker stack and want to inspect the shared Docker-backed database instead:

```bash
python print_db.py --docker
```

## Planned stages

1. Single-node service and API contract
2. Persistent storage with database-backed mappings
3. Multi-node deployment with Docker and nginx
4. Fault-tolerance testing, load testing, and performance analysis

## Stage 3

Stage 3 introduces a multi-node deployment layout:

- two backend app containers
- one nginx load balancer container
- a shared Docker volume mounted at `/app/data`
- per-node identification through the `X-Backend-Node` response header
- a debug endpoint at `GET /api/v1/node`

### Stage 3 Run

```bash
docker compose up --build
```

The load balancer is exposed at `http://127.0.0.1:8080`.

### Stage 3 Quick Checks

Create a short URL through nginx:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/urls \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/distributed","expires_in_days":7}'
```

Check which backend answered:

```bash
curl -i http://127.0.0.1:8080/api/v1/node
```

Repeat that command several times and the `X-Backend-Node` header should alternate between backend instances.

## Remaining Work

### Stage 4: Fault Tolerance

Still to do:

- demonstrate that the service continues working when one backend node is stopped
- document how nginx behaves when one upstream backend is unavailable
- verify that redirects and URL creation still work during partial node failure
- capture backend-node evidence for the report

### Stage 5: Performance and Evaluation

Still to do:

- run baseline measurements against the single-node deployment
- run the same measurements against the nginx multi-node deployment
- compare latency, throughput, and error rate
- test read-heavy and shorten-heavy request patterns
- summarize the observed tradeoffs and current architectural limitations

### Known Limitations To Discuss In The Final Report

- SQLite is shared for simplicity, but it is not a true distributed database
- backend replicas are distributed at the application layer, while persistence is still centralized
- nginx provides request distribution, but we have not yet added advanced health-check automation or dynamic scaling
