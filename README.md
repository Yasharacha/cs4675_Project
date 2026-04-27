# CS4675 Distributed URL Shortener

This project extends a basic URL shortener into a small distributed system with persistence, load balancing, failover behavior, and repeatable performance evaluation.

## Project Summary

The final system supports:

- a single-node URL shortener API
- persistent SQLite storage
- a two-backend nginx deployment
- backend identification through response headers and a node-inspection endpoint
- partial-failure behavior when one backend is unavailable
- repeatable benchmark and graph generation workflows

The implementation was built in stages so the design changes are clear and the evaluation maps directly to the report.

## Implementation Details

### Stage 1: Single-Node Service

The single-node service provides:

- `POST /api/v1/urls` to create a short URL
- `GET /<code>` to redirect to the original URL
- `GET /api/v1/urls/<code>` to inspect metadata
- `GET /api/v1/urls` to list stored mappings
- `GET /health` for health checking

The service also tracks expiration, click counts, and last-accessed information.

### Stage 2: Persistent Storage

Stage 2 replaces the temporary in-memory store with SQLite so mappings survive restarts and can be shared by multiple application instances later in the project.

### Stage 3: Multi-Node Deployment

Stage 3 introduces:

- two backend app containers
- one nginx load balancer
- a shared Docker volume mounted at `/app/data`
- per-node identification through the `X-Backend-Node` header
- `GET /api/v1/node` for debugging and evidence collection

This is the point where the project moves from a single app instance to a small replicated service.

### Stage 4: Fault Tolerance

Stage 4 focuses on partial failure. The nginx configuration is set so requests can continue flowing to the surviving backend when one backend container is stopped.

The goal of this stage is not only redirect continuity. It also checks whether URL creation still works during partial failure.

### Stage 5: Performance and Evaluation

Stage 5 adds the benchmark runner in [benchmark.py](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmark.py). It produces repeatable measurements for:

- throughput
- average latency
- p95 latency
- error rate
- backend-node distribution

Two workload mixes are built into the benchmark runner:

- `read-heavy`: `70%` redirects, `20%` metadata lookups, `10%` full-list reads
- `shorten-heavy`: `85%` create requests, `10%` metadata lookups, `5%` full-list reads

These workloads let the project compare read-dominant traffic against write-dominant traffic.

## How To Run

Install dependencies:

```bash
uv sync --dev
```

Run the single-node app:

```bash
uv run python run.py
```

Run the distributed stack:

```bash
docker compose up --build -d
```

Run tests:

```bash
uv run pytest
```

## Benchmark Methodology

The final submission keeps three evaluation views:

### 1. Fixed-Concurrency Stage 5 Comparison

This is the original single-node vs multi-node comparison at fixed concurrency `20`.

Purpose:

- compare one backend against the nginx-fronted two-backend layout
- compare `read-heavy` and `shorten-heavy` workloads at one consistent operating point

Final artifact folder:

- [benchmarks/results/20260415-133202](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/results/20260415-133202)

Main files:

- [stage5-summary.md](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/results/20260415-133202/stage5-summary.md)
- [graphs.html](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/results/20260415-133202/graphs.html)

### 2. Controlled Concurrency Study

This is the cleaner one-variable-at-a-time experiment. It keeps request count, seed count, timeout, and random seed fixed while changing only concurrency.

Concurrency levels tested:

- `4`
- `8`
- `16`

Purpose:

- show how performance changes as parallel request pressure increases
- compare single-node and multi-node behavior at each concurrency level
- compare `read-heavy` and `shorten-heavy` scaling trends

Final artifact folder:

- [benchmarks/concurrency/20260417-152237](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/concurrency/20260417-152237)

Main files:

- [concurrency-study-summary.md](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/concurrency/20260417-152237/concurrency-study-summary.md)
- [graphs.html](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/concurrency/20260417-152237/graphs.html)
- [throughput-by-concurrency.svg](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/concurrency/20260417-152237/throughput-by-concurrency.svg)
- [avg-latency-by-concurrency.svg](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/concurrency/20260417-152237/avg-latency-by-concurrency.svg)
- [p95-latency-by-concurrency.svg](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/concurrency/20260417-152237/p95-latency-by-concurrency.svg)
- [error-rate-by-concurrency.svg](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/concurrency/20260417-152237/error-rate-by-concurrency.svg)

### 3. Easy / Medium / Hard Scenarios

These cases are smaller, presentation-friendly benchmark scenarios. They are not meant to replace the larger evaluation studies. They are meant to explain the system behavior progressively.

#### Easy Scenario

The easy case is the baseline.

- single-node deployment
- one `read-heavy` benchmark
- no load balancer
- no failover

Purpose:

- show that the core service works in the simplest configuration
- establish a baseline before distribution or failure is introduced

Artifacts:

- [easy-case.md](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/cases/easy-20260416-145610/easy-case.md)
- [graphs.html](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/cases/easy-20260416-145610/graphs.html)

#### Medium Scenario

The medium case is the average healthy distributed case.

- multi-node nginx deployment
- one `read-heavy` benchmark
- one `shorten-heavy` benchmark
- both backends healthy

Purpose:

- show the normal distributed operating mode
- compare read-heavy and shorten-heavy traffic while the system is healthy
- show request distribution across both backends

Artifacts:

- [medium-case.md](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/cases/medium-20260416-145610/medium-case.md)
- [graphs.html](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/cases/medium-20260416-145610/graphs.html)

#### Hard Scenario

The hard case is the failure case.

- one backend stopped before the run
- nginx routes to the surviving backend
- a smaller failover benchmark is executed

Purpose:

- show that the service still works during partial failure
- show that nginx continues routing requests to the surviving backend
- show that failover preserves availability even when capacity is reduced

Artifacts:

- [hard-case.md](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/cases/hard-20260416-145733/hard-case.md)
- [graphs.html](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/cases/hard-20260416-145733/graphs.html)

Combined case comparison:

- [benchmarks/cases/combined-20260416-152033/graphs.html](/C:/Users/yasha/CS4675_proj/cs4675_Project/benchmarks/cases/combined-20260416-152033/graphs.html)

## What The Benchmarks Were Showing

The evaluation work was meant to answer three questions:

1. Does the multi-node deployment outperform the single-node deployment under meaningful load?
2. Does the system behave differently under read-heavy and shorten-heavy traffic?
3. Does the system remain available during partial failure?

The final benchmark results support these conclusions:

- at low concurrency, multi-node overhead can outweigh the benefit of replication
- at medium and high concurrency, multi-node clearly improves throughput and latency
- `read-heavy` traffic benefits more strongly from multiple backends
- `shorten-heavy` traffic still improves, but gains are limited by the shared SQLite database
- error rate stayed at `0.0` in the final controlled concurrency study
- the hard scenario showed continued availability when one backend was removed

These outcomes are consistent with the architecture: the application layer is replicated, but persistence is still shared.
