from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    operation_weights: tuple[tuple[str, int], ...]


SCENARIOS: dict[str, Scenario] = {
    "read-heavy": Scenario(
        name="read-heavy",
        description="70% redirect, 20% metadata lookup, 10% full-list reads against existing short codes.",
        operation_weights=(("redirect", 70), ("details", 20), ("list", 10)),
    ),
    "shorten-heavy": Scenario(
        name="shorten-heavy",
        description="85% shorten requests, 10% metadata lookup, 5% full-list reads.",
        operation_weights=(("create", 85), ("details", 10), ("list", 5)),
    ),
}


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(slots=True)
class RequestResult:
    operation: str
    latency_ms: float
    ok: bool
    status_code: int | None
    backend_node: str | None = None
    error: str | None = None


class CodePool:
    def __init__(self) -> None:
        self._codes: list[str] = []
        self._lock = threading.Lock()

    def add(self, code: str) -> None:
        with self._lock:
            self._codes.append(code)

    def extend(self, codes: list[str]) -> None:
        with self._lock:
            self._codes.extend(codes)

    def choose(self, rng: random.Random) -> str | None:
        with self._lock:
            if not self._codes:
                return None
            return rng.choice(self._codes)


def iso_utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0

    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])

    rank = (len(sorted_values) - 1) * (pct / 100)
    low_index = int(rank)
    high_index = min(low_index + 1, len(sorted_values) - 1)
    weight = rank - low_index
    return sorted_values[low_index] * (1 - weight) + sorted_values[high_index] * weight


def round_float(value: float) -> float:
    return round(value, 3)


def build_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def make_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(NoRedirectHandler())


def send_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[int | None, dict[str, str], str, str | None]:
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    opener = make_opener()

    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.getcode(), dict(response.headers.items()), body, None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, dict(exc.headers.items()), body, None
    except Exception as exc:  # noqa: BLE001
        return None, {}, "", str(exc)


def check_health(base_url: str, timeout_seconds: float) -> None:
    status_code, _headers, body, error = send_request(
        "GET",
        build_url(base_url, "/health"),
        timeout_seconds=timeout_seconds,
    )
    if error is not None:
        raise RuntimeError(f"Health check failed for {base_url}: {error}")
    if status_code != 200:
        raise RuntimeError(
            f"Health check failed for {base_url}: expected 200, got {status_code} with body {body!r}"
        )


def seed_codes(
    *,
    base_url: str,
    deployment_label: str,
    scenario_name: str,
    count: int,
    timeout_seconds: float,
) -> list[str]:
    created_codes: list[str] = []

    for index in range(count):
        payload = {
            "url": (
                f"https://example.com/{deployment_label}/{scenario_name}/seed/"
                f"{index}?token={uuid4().hex}"
            ),
            "expires_in_days": 7,
        }
        status_code, _headers, body, error = send_request(
            "POST",
            build_url(base_url, "/api/v1/urls"),
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if error is not None:
            raise RuntimeError(f"Seed request failed: {error}")
        if status_code != 201:
            raise RuntimeError(
                f"Seed request failed with status {status_code}: {body}"
            )
        response_payload = json.loads(body)
        created_codes.append(response_payload["code"])

    return created_codes


def build_operation_plan(
    scenario: Scenario,
    request_count: int,
    rng: random.Random,
) -> list[str]:
    operations = [name for name, _weight in scenario.operation_weights]
    weights = [weight for _name, weight in scenario.operation_weights]
    return rng.choices(operations, weights=weights, k=request_count)


def summarize_results(
    *,
    deployment_label: str,
    base_url: str,
    scenario: Scenario,
    request_count: int,
    concurrency: int,
    seed_count: int,
    timeout_seconds: float,
    started_at: str,
    duration_seconds: float,
    results: list[RequestResult],
) -> dict[str, Any]:
    total_requests = len(results)
    success_count = sum(1 for item in results if item.ok)
    error_count = total_requests - success_count
    latencies = [item.latency_ms for item in results]
    status_counts = Counter(
        "network-error" if item.status_code is None else str(item.status_code)
        for item in results
    )
    backend_counts = Counter(item.backend_node for item in results if item.backend_node)
    operation_counts = Counter(item.operation for item in results)
    error_counts = Counter(item.error for item in results if item.error)

    operation_summaries: dict[str, Any] = {}
    grouped_results: dict[str, list[RequestResult]] = defaultdict(list)
    for item in results:
        grouped_results[item.operation].append(item)

    for operation, operation_results in grouped_results.items():
        op_latencies = [item.latency_ms for item in operation_results]
        op_successes = sum(1 for item in operation_results if item.ok)
        operation_summaries[operation] = {
            "request_count": len(operation_results),
            "success_count": op_successes,
            "error_count": len(operation_results) - op_successes,
            "error_rate": round_float(
                (len(operation_results) - op_successes) / len(operation_results)
            ),
            "latency_ms": {
                "avg": round_float(statistics.fmean(op_latencies)),
                "median": round_float(statistics.median(op_latencies)),
                "p95": round_float(percentile(op_latencies, 95)),
                "min": round_float(min(op_latencies)),
                "max": round_float(max(op_latencies)),
            },
        }

    return {
        "deployment": deployment_label,
        "base_url": base_url,
        "scenario": scenario.name,
        "scenario_description": scenario.description,
        "started_at": started_at,
        "duration_seconds": round_float(duration_seconds),
        "request_count": total_requests,
        "concurrency": concurrency,
        "seed_count": seed_count,
        "timeout_seconds": timeout_seconds,
        "success_count": success_count,
        "error_count": error_count,
        "error_rate": round_float(error_count / total_requests if total_requests else 0.0),
        "throughput_rps": round_float(
            total_requests / duration_seconds if duration_seconds else 0.0
        ),
        "latency_ms": {
            "avg": round_float(statistics.fmean(latencies) if latencies else 0.0),
            "median": round_float(statistics.median(latencies) if latencies else 0.0),
            "p95": round_float(percentile(latencies, 95)),
            "min": round_float(min(latencies) if latencies else 0.0),
            "max": round_float(max(latencies) if latencies else 0.0),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "backend_node_counts": dict(sorted(backend_counts.items())),
        "operation_counts": dict(sorted(operation_counts.items())),
        "operation_summaries": operation_summaries,
        "errors": dict(sorted(error_counts.items())),
    }


def run_benchmark(
    *,
    base_url: str,
    deployment_label: str,
    scenario_name: str,
    request_count: int,
    concurrency: int,
    seed_count: int,
    timeout_seconds: float,
    random_seed: int,
) -> dict[str, Any]:
    if scenario_name not in SCENARIOS:
        raise KeyError(f"Unknown scenario: {scenario_name}")

    scenario = SCENARIOS[scenario_name]
    check_health(base_url, timeout_seconds)

    code_pool = CodePool()
    seeded_codes = seed_codes(
        base_url=base_url,
        deployment_label=deployment_label,
        scenario_name=scenario_name,
        count=seed_count,
        timeout_seconds=timeout_seconds,
    )
    code_pool.extend(seeded_codes)

    started_at = iso_utc_now()
    overall_start = time.perf_counter()

    def execute_operation(index: int, operation: str) -> RequestResult:
        operation_rng = random.Random(random_seed + index)
        request_start = time.perf_counter()
        status_code: int | None = None
        headers: dict[str, str] = {}
        error: str | None = None
        body = ""

        if operation == "create":
            payload = {
                "url": (
                    f"https://example.com/{deployment_label}/{scenario_name}/run/"
                    f"{index}?token={uuid4().hex}"
                ),
                "expires_in_days": 7,
            }
            status_code, headers, body, error = send_request(
                "POST",
                build_url(base_url, "/api/v1/urls"),
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
            if error is None and status_code == 201:
                try:
                    response_payload = json.loads(body)
                    code_pool.add(response_payload["code"])
                except (KeyError, json.JSONDecodeError) as exc:
                    error = f"Invalid create response: {exc}"

        elif operation == "details":
            code = code_pool.choose(operation_rng)
            if code is None:
                error = "No short codes available for details request."
            else:
                status_code, headers, _body, error = send_request(
                    "GET",
                    build_url(base_url, f"/api/v1/urls/{code}"),
                    timeout_seconds=timeout_seconds,
                )

        elif operation == "redirect":
            code = code_pool.choose(operation_rng)
            if code is None:
                error = "No short codes available for redirect request."
            else:
                status_code, headers, _body, error = send_request(
                    "GET",
                    build_url(base_url, f"/{code}"),
                    timeout_seconds=timeout_seconds,
                )

        elif operation == "list":
            status_code, headers, _body, error = send_request(
                "GET",
                build_url(base_url, "/api/v1/urls"),
                timeout_seconds=timeout_seconds,
            )
        else:
            error = f"Unsupported operation: {operation}"

        latency_ms = (time.perf_counter() - request_start) * 1000
        ok = error is None and status_code is not None and status_code < 400
        return RequestResult(
            operation=operation,
            latency_ms=latency_ms,
            ok=ok,
            status_code=status_code,
            backend_node=headers.get("X-Backend-Node"),
            error=error,
        )

    rng = random.Random(random_seed)
    operation_plan = build_operation_plan(scenario, request_count, rng)
    results: list[RequestResult] = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(execute_operation, index, operation)
            for index, operation in enumerate(operation_plan)
        ]
        for future in as_completed(futures):
            results.append(future.result())

    duration_seconds = time.perf_counter() - overall_start
    return summarize_results(
        deployment_label=deployment_label,
        base_url=base_url,
        scenario=scenario,
        request_count=request_count,
        concurrency=concurrency,
        seed_count=seed_count,
        timeout_seconds=timeout_seconds,
        started_at=started_at,
        duration_seconds=duration_seconds,
        results=results,
    )


def compare_summaries(
    single_summary: dict[str, Any],
    multi_summary: dict[str, Any],
) -> dict[str, Any]:
    def percent_change(new_value: float, old_value: float) -> float | None:
        if old_value == 0:
            return None
        return round_float(((new_value - old_value) / old_value) * 100)

    scenario_name = single_summary["scenario"]
    return {
        "scenario": scenario_name,
        "single_node": {
            "throughput_rps": single_summary["throughput_rps"],
            "avg_latency_ms": single_summary["latency_ms"]["avg"],
            "p95_latency_ms": single_summary["latency_ms"]["p95"],
            "error_rate": single_summary["error_rate"],
        },
        "multi_node": {
            "throughput_rps": multi_summary["throughput_rps"],
            "avg_latency_ms": multi_summary["latency_ms"]["avg"],
            "p95_latency_ms": multi_summary["latency_ms"]["p95"],
            "error_rate": multi_summary["error_rate"],
        },
        "delta": {
            "throughput_rps": round_float(
                multi_summary["throughput_rps"] - single_summary["throughput_rps"]
            ),
            "throughput_percent": percent_change(
                multi_summary["throughput_rps"], single_summary["throughput_rps"]
            ),
            "avg_latency_ms": round_float(
                multi_summary["latency_ms"]["avg"] - single_summary["latency_ms"]["avg"]
            ),
            "avg_latency_percent": percent_change(
                multi_summary["latency_ms"]["avg"],
                single_summary["latency_ms"]["avg"],
            ),
            "p95_latency_ms": round_float(
                multi_summary["latency_ms"]["p95"] - single_summary["latency_ms"]["p95"]
            ),
            "p95_latency_percent": percent_change(
                multi_summary["latency_ms"]["p95"],
                single_summary["latency_ms"]["p95"],
            ),
            "error_rate": round_float(
                multi_summary["error_rate"] - single_summary["error_rate"]
            ),
        },
    }


def trend_word(delta: float, positive_word: str, negative_word: str) -> str:
    if delta > 0:
        return positive_word
    if delta < 0:
        return negative_word
    return "matched"


def format_node_counts(summary: dict[str, Any]) -> str:
    counts = summary.get("backend_node_counts", {})
    if not counts:
        return "no backend header data captured"
    return ", ".join(f"{name}: {count}" for name, count in counts.items())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def render_markdown_report(summary_bundle: dict[str, Any]) -> str:
    lines = [
        "# Stage 5 Benchmark Summary",
        "",
        f"Generated at: `{summary_bundle['generated_at']}`",
        "",
        "## Parameters",
        "",
        f"- Requests per scenario: `{summary_bundle['parameters']['request_count']}`",
        f"- Concurrency: `{summary_bundle['parameters']['concurrency']}`",
        f"- Seed URLs per run: `{summary_bundle['parameters']['seed_count']}`",
        f"- Timeout seconds: `{summary_bundle['parameters']['timeout_seconds']}`",
        f"- Random seed: `{summary_bundle['parameters']['random_seed']}`",
        "",
    ]

    for scenario_name, comparison in summary_bundle["comparisons"].items():
        single_summary = summary_bundle["runs"]["single-node"][scenario_name]
        multi_summary = summary_bundle["runs"]["multi-node"][scenario_name]
        throughput_delta = comparison["delta"]["throughput_rps"]
        avg_latency_delta = comparison["delta"]["avg_latency_ms"]
        p95_latency_delta = comparison["delta"]["p95_latency_ms"]
        error_delta = comparison["delta"]["error_rate"]

        lines.extend(
            [
                f"## {scenario_name.title()}",
                "",
                f"{SCENARIOS[scenario_name].description}",
                "",
                "| Deployment | Throughput (req/s) | Avg Latency (ms) | P95 Latency (ms) | Error Rate |",
                "| --- | ---: | ---: | ---: | ---: |",
                (
                    f"| Single node | {single_summary['throughput_rps']} | "
                    f"{single_summary['latency_ms']['avg']} | {single_summary['latency_ms']['p95']} | "
                    f"{single_summary['error_rate']} |"
                ),
                (
                    f"| Multi node | {multi_summary['throughput_rps']} | "
                    f"{multi_summary['latency_ms']['avg']} | {multi_summary['latency_ms']['p95']} | "
                    f"{multi_summary['error_rate']} |"
                ),
                "",
                "Observed comparison:",
                (
                    f"- Multi-node {trend_word(throughput_delta, 'improved', 'reduced')} "
                    f"throughput by `{abs(throughput_delta)}` req/s."
                ),
                (
                    f"- Multi-node {trend_word(-avg_latency_delta, 'reduced', 'increased')} "
                    f"average latency by `{abs(avg_latency_delta)}` ms."
                ),
                (
                    f"- Multi-node {trend_word(-p95_latency_delta, 'reduced', 'increased')} "
                    f"p95 latency by `{abs(p95_latency_delta)}` ms."
                ),
                (
                    f"- Error-rate delta (multi minus single): `{error_delta}`."
                ),
                (
                    f"- Single-node backend distribution: {format_node_counts(single_summary)}."
                ),
                (
                    f"- Multi-node backend distribution: {format_node_counts(multi_summary)}."
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Architectural Interpretation",
            "",
            "- These measurements compare one local app process against the nginx-fronted two-backend layout under the same synthetic workload.",
            "- Read-heavy gains usually come from sharing redirect traffic across replicas, while shorten-heavy gains can be limited by the shared SQLite database and its write coordination.",
            "- Error rate should stay near zero in healthy runs. Any increase under multi-node load is a signal to inspect nginx logs, backend logs, and SQLite lock contention.",
            "",
            "## Current Limitations",
            "",
            "- SQLite is still a single shared persistence layer, so the deployment is replicated at the application layer rather than fully distributed in storage.",
            "- The benchmark focuses on app-visible latency, throughput, and error rate. It does not capture CPU, memory, or disk metrics.",
            "- Synthetic request mixes are deterministic and useful for comparison, but they do not replace real user traffic traces.",
            "",
        ]
    )

    return "\n".join(lines)


def print_run_summary(summary: dict[str, Any]) -> None:
    print(
        f"[{summary['deployment']}][{summary['scenario']}] "
        f"throughput={summary['throughput_rps']} req/s "
        f"avg={summary['latency_ms']['avg']} ms "
        f"p95={summary['latency_ms']['p95']} ms "
        f"errors={summary['error_rate']}"
    )


def run_single_scenario_command(args: argparse.Namespace) -> int:
    summary = run_benchmark(
        base_url=args.base_url,
        deployment_label=args.deployment,
        scenario_name=args.scenario,
        request_count=args.requests,
        concurrency=args.concurrency,
        seed_count=args.seed_count,
        timeout_seconds=args.timeout_seconds,
        random_seed=args.random_seed,
    )
    write_json(Path(args.output), summary)
    print_run_summary(summary)
    print(f"Saved benchmark result to {args.output}")
    return 0


def run_stage5_command(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    runs: dict[str, dict[str, Any]] = {
        "single-node": {},
        "multi-node": {},
    }

    for deployment_label, base_url in (
        ("single-node", args.single_url),
        ("multi-node", args.multi_url),
    ):
        for scenario_name in SCENARIOS:
            summary = run_benchmark(
                base_url=base_url,
                deployment_label=deployment_label,
                scenario_name=scenario_name,
                request_count=args.requests,
                concurrency=args.concurrency,
                seed_count=args.seed_count,
                timeout_seconds=args.timeout_seconds,
                random_seed=args.random_seed,
            )
            runs[deployment_label][scenario_name] = summary
            result_path = run_dir / f"{deployment_label}-{scenario_name}.json"
            write_json(result_path, summary)
            print_run_summary(summary)

            if args.pause_seconds:
                time.sleep(args.pause_seconds)

    comparisons = {
        scenario_name: compare_summaries(
            runs["single-node"][scenario_name],
            runs["multi-node"][scenario_name],
        )
        for scenario_name in SCENARIOS
    }

    summary_bundle = {
        "generated_at": iso_utc_now(),
        "parameters": {
            "request_count": args.requests,
            "concurrency": args.concurrency,
            "seed_count": args.seed_count,
            "timeout_seconds": args.timeout_seconds,
            "random_seed": args.random_seed,
        },
        "runs": runs,
        "comparisons": comparisons,
    }

    summary_json_path = run_dir / "stage5-summary.json"
    summary_md_path = run_dir / "stage5-summary.md"
    write_json(summary_json_path, summary_bundle)
    summary_md_path.write_text(
        render_markdown_report(summary_bundle),
        encoding="utf-8",
    )

    print(f"Saved Stage 5 comparison JSON to {summary_json_path}")
    print(f"Saved Stage 5 comparison Markdown to {summary_md_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run repeatable Stage 5 benchmarks for the distributed URL shortener."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument(
        "--requests",
        type=int,
        default=400,
        help="Number of timed requests to issue per scenario. Default: 400",
    )
    common_parent.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Maximum concurrent requests during a run. Default: 20",
    )
    common_parent.add_argument(
        "--seed-count",
        type=int,
        default=100,
        help="How many short URLs to create before each timed scenario. Default: 100",
    )
    common_parent.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="Per-request timeout in seconds. Default: 5.0",
    )
    common_parent.add_argument(
        "--random-seed",
        type=int,
        default=4675,
        help="Random seed for reproducible request mixes. Default: 4675",
    )

    scenario_parser = subparsers.add_parser(
        "run-scenario",
        parents=[common_parent],
        help="Benchmark a single deployment/scenario combination.",
    )
    scenario_parser.add_argument(
        "--base-url",
        required=True,
        help="Base URL for the target deployment, for example http://127.0.0.1:5000",
    )
    scenario_parser.add_argument(
        "--deployment",
        required=True,
        help="Label to attach to the output, for example single-node or multi-node.",
    )
    scenario_parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        required=True,
        help="Which workload mix to run.",
    )
    scenario_parser.add_argument(
        "--output",
        required=True,
        help="Path to the JSON file where the scenario result should be written.",
    )
    scenario_parser.set_defaults(handler=run_single_scenario_command)

    stage5_parser = subparsers.add_parser(
        "run-stage5",
        parents=[common_parent],
        help="Run the full Stage 5 suite for single-node and multi-node deployments.",
    )
    stage5_parser.add_argument(
        "--single-url",
        required=True,
        help="Single-node base URL, typically http://127.0.0.1:5000",
    )
    stage5_parser.add_argument(
        "--multi-url",
        required=True,
        help="Nginx/multi-node base URL, typically http://127.0.0.1:8080",
    )
    stage5_parser.add_argument(
        "--output-dir",
        default="benchmarks/results",
        help="Directory where Stage 5 result files should be stored. Default: benchmarks/results",
    )
    stage5_parser.add_argument(
        "--pause-seconds",
        type=float,
        default=1.0,
        help="Optional pause between scenario runs to reduce overlap. Default: 1.0",
    )
    stage5_parser.set_defaults(handler=run_stage5_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
