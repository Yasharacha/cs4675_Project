# Easy, Medium, Hard Case Results

This document summarizes the latest presentation-oriented case runs and explains whether each case passes with respect to the project goals.

These case runs are intentionally smaller and faster than the full Stage 5 benchmark suite. They are meant for demos and presentations, not as the final high-volume evaluation.

## Project Goals Used For Evaluation

The cases are evaluated against these goals from the project:

- `Easy`: demonstrate that the single-node URL shortener works correctly under a simple read-heavy workload
- `Medium`: demonstrate that the multi-node nginx deployment works correctly under both read-heavy and shorten-heavy traffic
- `Hard`: demonstrate that the service continues working when one backend node is stopped and nginx fails over to the surviving backend

## Easy Case

Artifacts:

- Summary: [easy-case.md](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/easy-20260416-145610/easy-case.md)
- Graph dashboard: [graphs.html](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/easy-20260416-145610/graphs.html)
- Throughput graph: [throughput.svg](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/easy-20260416-145610/throughput.svg)

Measured results:

- workload: single-node `read-heavy`
- requests: `80`
- concurrency: `6`
- success count: `80`
- error count: `0`
- throughput: `56.268 req/s`
- average latency: `102.978 ms`
- p95 latency: `189.198 ms`

Pass/fail:

- `Pass`

Why it passes:

- the baseline single-node service completed every request successfully
- error rate stayed at `0.0`
- the result establishes a working baseline before adding load balancing or failover
- all responses came from `local-node`, which matches the expected single-node behavior

## Medium Case

Artifacts:

- Summary: [medium-case.md](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/medium-20260416-145610/medium-case.md)
- Graph dashboard: [graphs.html](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/medium-20260416-145610/graphs.html)
- Backend distribution graph: [backend-distribution.svg](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/medium-20260416-145610/backend-distribution.svg)

Measured results:

- workload 1: multi-node `read-heavy`
- success count: `120`
- error count: `0`
- throughput: `82.476 req/s`
- average latency: `94.245 ms`
- p95 latency: `198.637 ms`
- backend distribution: `backend1: 60`, `backend2: 60`

- workload 2: multi-node `shorten-heavy`
- success count: `120`
- error count: `0`
- throughput: `68.221 req/s`
- average latency: `112.1 ms`
- p95 latency: `234.196 ms`
- backend distribution: `backend1: 60`, `backend2: 60`

Pass/fail:

- `Pass`

Why it passes:

- both workload mixes completed successfully with `0.0` error rate
- nginx distributed traffic evenly across the two backend containers in both runs
- the system handled both read-heavy and shorten-heavy traffic while all nodes were healthy
- this validates the multi-node deployment goal rather than just the single-node baseline

## Hard Case

Artifacts:

- Summary: [hard-case.md](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/hard-20260416-145733/hard-case.md)
- Graph dashboard: [graphs.html](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/hard-20260416-145733/graphs.html)
- Backend distribution graph: [backend-distribution.svg](/C:/Users/yasha/cs4675_proj/cs4675_Project/benchmarks/cases/hard-20260416-145733/backend-distribution.svg)

Measured results:

- setup: `backend1` was stopped before the run
- node samples before benchmark: `backend2`, `backend2`, `backend2`
- workload: failover `read-heavy`
- requests: `60`
- concurrency: `4`
- success count: `60`
- error count: `0`
- throughput: `65.113 req/s`
- average latency: `60.226 ms`
- p95 latency: `171.399 ms`
- backend distribution during the run: `backend2: 60`

Pass/fail:

- `Pass`

Why it passes:

- the pre-run node samples confirmed nginx was routing only to the surviving backend
- the smaller failover benchmark still completed with `0.0` error rate
- all measured responses during the run came from `backend2`, which is exactly what should happen after one backend is stopped
- this demonstrates partial-failure tolerance rather than just healthy-cluster load balancing

## Overall Interpretation

Overall result:

- `Pass`

Why:

- the easy case confirms the single-node baseline works
- the medium case confirms the nginx multi-node deployment works under both read-heavy and shorten-heavy traffic
- the hard case confirms the service stays available when one backend is removed and nginx fails over to the surviving node

What these cases do not mean:

- they do not prove the architecture is fully distributed at the database layer
- they do not replace the larger Stage 5 benchmark suite
- they do not measure every possible failure mode

What they do show clearly:

- baseline correctness
- multi-node request distribution
- partial-failure survivability
