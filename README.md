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

Install dependencies with uv:

```bash
uv sync --dev
```

```bash
uv run python run.py
```

This creates or reuses a SQLite database at `data/url_shortener.db`.

Once the server is running, open `http://127.0.0.1:5000/` in a browser to use the minimal GUI. The interface lets you:

- create short URLs without using `curl`
- view the current backend instance name and database path
- inspect all stored mappings and their click counts
- open generated short links directly from the page

To verify persistence manually:

1. Start the server with `uv run python run.py`
2. Create a short URL with the API
3. Stop the server
4. Start the server again
5. Request the same short code and confirm it still resolves

## Run tests

```bash
uv run pytest
```

## Print Database Contents

To print every stored URL mapping from the local SQLite database:

```bash
uv run python print_db.py
```

If you want to point at a different database file:

```bash
DATABASE_PATH=some/other/file.db uv run python print_db.py
```

If you are running the distributed Docker stack and want to inspect the shared Docker-backed database instead:

```bash
uv run python print_db.py --docker
```

## Planned stages

1. Single-node service and API contract
2. Persistent storage with database-backed mappings
3. Multi-node deployment with Docker and nginx
4. Fault-tolerance testing and failover behavior
5. Performance and evaluation

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

If Docker is not already running, start Docker Desktop first. `docker compose` will fail if the local Docker daemon is unavailable.

Once the stack is running, open `http://127.0.0.1:8080/` to use the same GUI through nginx. Refreshing the page or the dashboard data is a quick way to observe which backend node served the request.

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

## Stage 4

Stage 4 demonstrates partial-failure behavior when one backend node is unavailable.

The nginx configuration is set up so that:

- upstream failures are detected quickly with `max_fails=1` and `fail_timeout=5s`
- nginx retries another backend when it sees connection or upstream errors
- retries also apply to non-idempotent requests such as `POST /api/v1/urls`

This matters because Stage 4 is not only about redirects continuing to work. It also needs URL creation to keep working even if nginx first selects the backend container that has been stopped.

### Stage 4 Runbook

Use this exact sequence when you want to demonstrate fault tolerance and capture evidence for the report.

#### 1. Start the distributed stack

```bash
docker compose up --build -d
docker compose ps
```

Expected result:

- `backend1`, `backend2`, and `nginx` should all be listed as running
- nginx should be reachable on `http://127.0.0.1:8080`

#### 2. Confirm both backends are serving traffic before failure

Run the node endpoint several times:

```bash
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
```

What to look for:

- the `X-Backend-Node` response header should alternate between `backend1` and `backend2`
- the JSON body should report the same instance name as the header

This is your pre-failure evidence that nginx is distributing requests across both backends.

#### 3. Create a short URL before inducing failure

```bash
curl -X POST http://127.0.0.1:8080/api/v1/urls \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/fault-tolerance","expires_in_days":7}'
```

Save the returned `code`. You will use it later to prove redirects still work after one node is stopped.

#### 4. Stop one backend node

Stop exactly one backend container:

```bash
docker compose stop backend1
docker compose ps
```

Expected result:

- `backend1` should show as stopped or exited
- `backend2` and `nginx` should still be running

#### 5. Verify read requests still work during partial failure

Check node identity a few times:

```bash
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
```

Expected result:

- responses should continue succeeding with `HTTP/1.1 200 OK`
- `X-Backend-Node` should now consistently show `backend2`

Then verify redirect behavior using the code created earlier:

```bash
curl -i http://127.0.0.1:8080/<code>
```

Expected result:

- nginx should still return `302 FOUND`
- the `Location` header should still point to the original long URL
- the response header should show `X-Backend-Node: backend2`

#### 6. Verify write requests still work during partial failure

Create a new short URL while `backend1` is still stopped:

```bash
curl -i -X POST http://127.0.0.1:8080/api/v1/urls \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/post-failover","expires_in_days":7}'
```

Expected result:

- the request should still succeed with `HTTP/1.1 201 CREATED`
- the response should include `X-Backend-Node: backend2`
- the returned `short_url` should be usable immediately

This is the key Stage 4 write-path check. Because nginx is configured with `proxy_next_upstream ... non_idempotent`, a `POST` can be retried against the surviving backend if the first upstream choice is unavailable.

#### 7. Confirm the shared database still serves data

List stored URLs through nginx:

```bash
curl http://127.0.0.1:8080/api/v1/urls
```

Expected result:

- URLs created before the failure should still exist
- URLs created after the failure should also appear

This works because both containers mount the same shared Docker volume at `/app/data`, and both app instances point to the same SQLite database file.

#### 8. Optional recovery check

Restart the stopped backend:

```bash
docker compose start backend1
docker compose ps
```

Then query the node endpoint again several times:

```bash
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
curl -i http://127.0.0.1:8080/api/v1/node
```

Expected result:

- traffic should begin appearing on both `backend1` and `backend2` again

### Stage 4 What nginx Is Doing

When one upstream backend is stopped, nginx will first see a connection failure or upstream error when it tries to proxy a request there. With the current configuration:

- `max_fails=1` and `fail_timeout=5s` cause nginx to mark that backend as unavailable quickly
- `proxy_next_upstream` tells nginx which failure classes should trigger a retry on another backend
- `proxy_next_upstream_tries 2` limits the retry chain to the two configured backends
- `non_idempotent` allows nginx to retry `POST` requests in this controlled demo setup

In practice, that means one failed backend should degrade capacity but not fully take the service down, as long as the other backend and the shared database volume remain available.

### Stage 4 Evidence To Capture For The Report

Capture these artifacts while running the drill:

- output of `docker compose ps` before and after stopping one backend
- several `curl -i http://127.0.0.1:8080/api/v1/node` responses before failure, showing both backend names
- several `curl -i http://127.0.0.1:8080/api/v1/node` responses after failure, showing only the surviving backend
- one successful redirect response after failure
- one successful `POST /api/v1/urls` response after failure
- one `GET /api/v1/urls` response showing data created before and after the failure

If you want nginx-side logs for extra evidence, run:

```bash
docker compose logs nginx
docker compose logs backend1
docker compose logs backend2
```

### Stage 5: Performance and Evaluation

Stage 5 is implemented with the benchmark runner in `benchmark.py`.

The script is designed to produce repeatable measurements for:

- a single-node deployment, usually `http://127.0.0.1:5000`
- the nginx multi-node deployment, usually `http://127.0.0.1:8080`
- a read-heavy workload
- a shorten-heavy workload
- latency, throughput, error rate, and backend-node distribution

### Stage 5 Workloads

The benchmark runner uses two built-in workload mixes:

- `read-heavy`: 70% redirects, 20% metadata lookups, 10% `GET /api/v1/urls`
- `shorten-heavy`: 85% `POST /api/v1/urls`, 10% metadata lookups, 5% `GET /api/v1/urls`

Before each timed run, the script seeds the target deployment with a configurable number of short URLs so read operations have realistic data to hit.

### Stage 5 Runbook

#### 1. Start the single-node app

```bash
uv run python run.py
```

Leave that process running on `http://127.0.0.1:5000`.

#### 2. Start the multi-node Docker stack in a second terminal

```bash
docker compose up --build -d
docker compose ps
```

Leave nginx running on `http://127.0.0.1:8080`.

#### 3. Run the full Stage 5 suite

```bash
uv run python benchmark.py run-stage5 \
  --single-url http://127.0.0.1:5000 \
  --multi-url http://127.0.0.1:8080 \
  --requests 400 \
  --concurrency 20 \
  --seed-count 100
```

This writes a timestamped results folder under `benchmarks/results/`.

Inside that folder you will get:

- one JSON result file for each deployment/scenario pair
- `stage5-summary.json` with the combined comparison data
- `stage5-summary.md` with a report-ready markdown summary
- `throughput-comparison.svg`, `avg-latency-comparison.svg`, `p95-latency-comparison.svg`, and `backend-distribution.svg`
- `graphs.html`, which places all generated charts on one page

#### 4. Run one scenario by itself if needed

```bash
uv run python benchmark.py run-scenario \
  --base-url http://127.0.0.1:8080 \
  --deployment multi-node \
  --scenario read-heavy \
  --output benchmarks/results/manual-read-heavy.json
```

#### 5. Rebuild graphs for an existing results folder if needed

```bash
uv run python benchmark.py make-graphs \
  --summary-json benchmarks/results/<timestamp>/stage5-summary.json
```

#### 6. Generate report-ready figures and captions

```bash
uv run python benchmark.py make-report-figures \
  --summary-json benchmarks/results/<timestamp>/stage5-summary.json
```

This creates a `report-figures/` folder with:

- figure-numbered SVG files for throughput, average latency, p95 latency, and backend distribution
- `report-figure-captions.md` with report-ready figure captions and a suggested results paragraph
- `report-figures.html` for quick review in a browser

### Easy, Medium, Hard Demo Cases

If you want to present the system as progressively harder cases instead of one full benchmark suite, use the built-in case runner.

Latest measured case results are summarized in [CASE_RESULTS.md](/C:/Users/yasha/cs4675_proj/cs4675_Project/CASE_RESULTS.md).

#### Easy Case

Single-node baseline with one read-heavy benchmark.

```bash
uv run python benchmark.py run-case \
  --case easy \
  --single-url http://127.0.0.1:5000
```

What it shows:

- the single-node app is healthy
- the benchmark runner works
- one simple read-heavy workload against the baseline deployment

What it generates:

- `benchmarks/cases/<timestamp>/easy-case.json`
- `benchmarks/cases/<timestamp>/easy-case.md`
- case statistics plus SVG graphs and `graphs.html`

#### Medium Case

Healthy multi-node benchmark covering both read-heavy and shorten-heavy traffic through nginx.

```bash
uv run python benchmark.py run-case \
  --case medium \
  --multi-url http://127.0.0.1:8080
```

What it shows:

- the nginx deployment is healthy
- both workload mixes run successfully on the multi-node stack
- the system handles both read-heavy and shorten-heavy traffic without inducing failure

What it generates:

- `benchmarks/cases/<timestamp>/medium-case.json`
- `benchmarks/cases/<timestamp>/medium-case.md`
- case statistics plus SVG graphs and `graphs.html`

#### Hard Case

Stop one backend first, then either run a smaller failover benchmark or use a manual curl-driven demo.

Stop one backend:

```bash
docker compose stop backend1
```

Smaller failover benchmark:

```bash
uv run python benchmark.py run-case \
  --case hard \
  --multi-url http://127.0.0.1:8080 \
  --hard-mode benchmark \
  --hard-scenario read-heavy
```

Manual curl runbook output:

```bash
uv run python benchmark.py run-case \
  --case hard \
  --multi-url http://127.0.0.1:8080 \
  --hard-mode manual \
  --manual-code <saved-code>
```

What it shows:

- partial-failure behavior after one backend is stopped
- nginx failover to the surviving backend
- either a smaller benchmark run during failure or a presentation-friendly manual runbook with curl commands

What it generates:

- benchmark mode:
  `benchmarks/cases/<timestamp>/hard-case.json` and `hard-case.md`
  This contains a smaller failover benchmark summary plus SVG graphs and `graphs.html`.
- manual mode:
  `benchmarks/cases/<timestamp>/hard-case.json` and `hard-case.md`
  This contains a manual runbook and curl commands, not benchmark metrics.

If you want graphs, use the full Stage 5 workflow:

```bash
uv run python benchmark.py run-stage5 --single-url http://127.0.0.1:5000 --multi-url http://127.0.0.1:8080
uv run python benchmark.py make-graphs --summary-json benchmarks/results/<timestamp>/stage5-summary.json
```

### Stage 5 Metrics

Each benchmark result includes:

- request count and concurrency
- throughput in requests per second
- average, median, and p95 latency in milliseconds
- success count, error count, and error rate
- HTTP status-code counts
- backend-node header counts
- per-operation breakdowns for create, redirect, details, and list requests

### Stage 5 How To Interpret Results

Use the generated markdown summary to compare:

- single-node throughput versus multi-node throughput
- average latency and p95 latency for each workload
- error rate under read-heavy versus shorten-heavy traffic
- whether nginx distributed requests across both backends during the multi-node runs

Expected tradeoffs to discuss:

- read-heavy traffic may benefit more from the replicated backend layout
- shorten-heavy traffic may show smaller gains because both backends still share one SQLite file
- large improvements in throughput with minimal latency regression are a good sign
- any non-zero error rate or major p95 spike is a signal to inspect nginx logs, backend logs, and SQLite contention

### Known Limitations To Discuss In The Final Report

- SQLite is shared for simplicity, but it is not a true distributed database
- backend replicas are distributed at the application layer, while persistence is still centralized
- nginx provides request distribution, but we have not yet added advanced health-check automation or dynamic scaling
