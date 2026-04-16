# Easy, Medium, Hard Presentation Script

This file is a short demo/presentation guide for the `run-case` workflow in `benchmark.py`.

## What The Case Commands Generate

The `run-case` commands generate case summaries, and benchmark-mode cases also generate graphs.

They generate:

- a timestamped folder under `benchmarks/cases/`
- one JSON file such as `easy-case.json`, `medium-case.json`, or `hard-case.json`
- one Markdown file such as `easy-case.md`, `medium-case.md`, or `hard-case.md`
- benchmark-mode cases also generate:
  `throughput.svg`, `avg-latency.svg`, `p95-latency.svg`, `backend-distribution.svg`, and `graphs.html`

Those files contain:

- benchmark statistics if the case is `easy`, `medium`, or `hard --hard-mode benchmark`
- a manual failover runbook if the case is `hard --hard-mode manual`

In other words:

- `run-case` benchmark mode = case-specific statistics plus graphs
- `run-case` manual mode = runbook output
- `run-stage5` + `make-graphs` = engineering charts
- `run-stage5` + `make-report-figures` = report-ready figures and captions

## Setup

Before the demo:

1. Start the single-node app on `http://127.0.0.1:5000`
2. Start the Docker stack on `http://127.0.0.1:8080`
3. Confirm both are healthy

## Easy Case

Command:

```bash
python benchmark.py run-case --case easy --single-url http://127.0.0.1:5000
```

What it does:

- runs one read-heavy benchmark against the single-node deployment
- writes `easy-case.json`, `easy-case.md`, and a small graph set
- gives you a simple baseline result

What to say:

- "The easy case is the simplest baseline: one node, one workload, no failover."
- "This shows the system working in its most basic configuration before we add distribution or failure."
- "The output here includes both a benchmark summary and presentation graphs for the single-node read-heavy case."

What to point at:

- throughput
- average latency
- p95 latency
- error rate

## Medium Case

Command:

```bash
python benchmark.py run-case --case medium --multi-url http://127.0.0.1:8080
```

What it does:

- runs read-heavy and shorten-heavy benchmarks through nginx
- keeps both backends healthy
- writes `medium-case.json`, `medium-case.md`, and a graph set

What to say:

- "The medium case moves to the distributed deployment but keeps the system healthy."
- "Instead of only one workload, this case covers both read-heavy and shorten-heavy traffic."
- "This output includes both benchmark statistics and graphs, so it is easy to compare the two workload mixes live."

What to point at:

- whether read-heavy and shorten-heavy both complete successfully
- how throughput and latency differ between the two workloads
- backend-node counts if you want to show that nginx is distributing requests

## Hard Case: Smaller Benchmark During Failover

Preparation:

```bash
docker compose stop backend1
```

Command:

```bash
python benchmark.py run-case --case hard --multi-url http://127.0.0.1:8080 --hard-mode benchmark --hard-scenario read-heavy
```

What it does:

- assumes one backend has already been stopped
- samples `/api/v1/node` first to confirm requests are only hitting the surviving backend
- runs a smaller benchmark during partial failure
- writes `hard-case.json`, `hard-case.md`, and a graph set

What to say:

- "The hard case introduces partial failure by stopping one backend first."
- "The benchmark checks that nginx is still routing traffic to the surviving backend before it begins."
- "This shows that the service can keep handling requests even when one backend is unavailable."

What to point at:

- the node samples in `hard-case.md`
- the surviving backend name
- whether throughput and latency remain acceptable
- the fact that the benchmark still completes with low or zero error rate

## Hard Case: Manual Failover Demo

Preparation:

```bash
docker compose stop backend1
```

Command:

```bash
python benchmark.py run-case --case hard --multi-url http://127.0.0.1:8080 --hard-mode manual --manual-code <saved-code>
```

What it does:

- does not run a benchmark
- writes `hard-case.json` and `hard-case.md`
- gives you a manual step-by-step curl runbook for the failover demo

What to say:

- "This version of the hard case is presentation-friendly because it walks through failover manually."
- "Instead of benchmark statistics or graphs, this output is a scripted runbook with the exact curl commands to demonstrate the surviving backend still works."
- "This is useful if I want to show the failover behavior live rather than summarize it through one more benchmark."

What to point at:

- the `docker compose stop backend1` step
- repeated `curl /api/v1/node` calls
- a redirect request using the saved short code
- a new `POST /api/v1/urls` request after failure

## If You Need Graphs

The case runner is mainly for case-by-case summaries and demo flow.

If you want graphs, use the full Stage 5 results:

```bash
python benchmark.py run-stage5 --single-url http://127.0.0.1:5000 --multi-url http://127.0.0.1:8080
python benchmark.py make-graphs --summary-json benchmarks/results/<timestamp>/stage5-summary.json
python benchmark.py make-report-figures --summary-json benchmarks/results/<timestamp>/stage5-summary.json
```

That path generates:

- SVG charts
- HTML graph dashboards
- report-ready figures and captions
