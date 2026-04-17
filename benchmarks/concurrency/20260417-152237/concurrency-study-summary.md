# Controlled Concurrency Study

Generated at: `2026-04-17T19:28:52+00:00`

## Experimental Control

- This study varies only concurrency.
- Requests per run were fixed at `400`.
- Seed URLs per run were fixed at `100`.
- Timeout seconds were fixed at `5.0`.
- Random seed was fixed at `4675`.
- Concurrency levels tested: `4, 8, 16`.

## Read Heavy

70% redirect, 20% metadata lookup, 10% full-list reads against existing short codes.

| Concurrency | Single Throughput (req/s) | Multi Throughput (req/s) | Single Avg (ms) | Multi Avg (ms) | Single P95 (ms) | Multi P95 (ms) | Single Error | Multi Error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 23.697 | 18.174 | 168.271 | 219.409 | 294.424 | 327.816 | 0.0 | 0.0 |
| 8 | 23.788 | 45.686 | 332.045 | 173.046 | 612.335 | 301.855 | 0.0 | 0.0 |
| 16 | 22.843 | 41.811 | 680.279 | 365.404 | 1223.74 | 647.563 | 0.0 | 0.0 |

Observed comparison by concurrency:
- Concurrency `4`: multi-node reduced throughput by `5.523` req/s, increased average latency by `51.138` ms, increased p95 latency by `33.392` ms, and had error-rate delta `0.0`.
- Concurrency `8`: multi-node improved throughput by `21.898` req/s, reduced average latency by `158.999` ms, reduced p95 latency by `310.48` ms, and had error-rate delta `0.0`.
- Concurrency `16`: multi-node improved throughput by `18.968` req/s, reduced average latency by `314.875` ms, reduced p95 latency by `576.177` ms, and had error-rate delta `0.0`.

## Shorten Heavy

85% shorten requests, 10% metadata lookup, 5% full-list reads.

| Concurrency | Single Throughput (req/s) | Multi Throughput (req/s) | Single Avg (ms) | Multi Avg (ms) | Single P95 (ms) | Multi P95 (ms) | Single Error | Multi Error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 23.028 | 24.783 | 173.194 | 160.841 | 312.175 | 301.179 | 0.0 | 0.0 |
| 8 | 36.516 | 39.275 | 216.185 | 201.081 | 331.109 | 330.148 | 0.0 | 0.0 |
| 16 | 30.831 | 49.91 | 484.66 | 304.694 | 818.459 | 497.574 | 0.0 | 0.0 |

Observed comparison by concurrency:
- Concurrency `4`: multi-node improved throughput by `1.755` req/s, reduced average latency by `12.353` ms, reduced p95 latency by `10.996` ms, and had error-rate delta `0.0`.
- Concurrency `8`: multi-node improved throughput by `2.759` req/s, reduced average latency by `15.104` ms, reduced p95 latency by `0.961` ms, and had error-rate delta `0.0`.
- Concurrency `16`: multi-node improved throughput by `19.079` req/s, reduced average latency by `179.966` ms, reduced p95 latency by `320.885` ms, and had error-rate delta `0.0`.

## Interpretation

- This experiment is intended for controlled performance evaluation rather than a presentation-only smoke test.
- Use it to discuss how latency and throughput respond as parallel request pressure increases.
- Compare read-heavy and shorten-heavy behavior separately, because write-heavy traffic may hit the shared SQLite layer differently.

## Current Limitations

- The deployment still shares one SQLite persistence layer, so concurrency scaling can be limited by storage coordination.
- The study captures application-visible metrics, not CPU, memory, or disk counters.
- Synthetic workloads are consistent and useful for controlled comparisons, but they do not replace real user traffic traces.
