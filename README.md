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

If Docker is not already running, start Docker Desktop first. `docker compose` will fail if the local Docker daemon is unavailable.

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
