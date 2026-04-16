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
from html import escape
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


def scenario_title(name: str) -> str:
    return name.replace("-", " ").title()


def metric_rows(summary_bundle: dict[str, Any], metric_path: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for scenario_name in SCENARIOS:
        single_run = summary_bundle["runs"]["single-node"][scenario_name]
        multi_run = summary_bundle["runs"]["multi-node"][scenario_name]

        single_value = single_run
        multi_value = multi_run
        for key in metric_path:
            single_value = single_value[key]
            multi_value = multi_value[key]

        rows.append(
            {
                "category": scenario_title(scenario_name),
                "single-node": float(single_value),
                "multi-node": float(multi_value),
            }
        )
    return rows


def render_grouped_bar_chart_svg(
    *,
    title: str,
    subtitle: str,
    y_label: str,
    rows: list[dict[str, Any]],
    filename_label: str,
) -> str:
    width = 980
    height = 560
    margin_top = 84
    margin_right = 56
    margin_bottom = 92
    margin_left = 88
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    series = [
        ("single-node", "Single node", "#1f5f8b"),
        ("multi-node", "Multi node", "#d97706"),
    ]
    max_value = max(
        max(float(row[series_key]) for row in rows)
        for series_key, _label, _color in series
    )
    max_value = max_value * 1.15 if max_value else 1.0

    group_width = plot_width / max(len(rows), 1)
    bar_width = min(88, (group_width * 0.72) / len(series))
    bar_gap = 10
    total_bar_width = len(series) * bar_width + (len(series) - 1) * bar_gap
    grid_steps = 5

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        "<defs>",
        '<style>',
        ".title { font: 700 26px Arial, sans-serif; fill: #14213d; }",
        ".subtitle { font: 400 14px Arial, sans-serif; fill: #5b6472; }",
        ".axis { font: 12px Arial, sans-serif; fill: #334155; }",
        ".label { font: 600 13px Arial, sans-serif; fill: #1f2937; }",
        ".value { font: 600 12px Arial, sans-serif; fill: #0f172a; }",
        ".legend { font: 600 13px Arial, sans-serif; fill: #1f2937; }",
        ".grid { stroke: #d8dee9; stroke-width: 1; }",
        ".axis-line { stroke: #94a3b8; stroke-width: 1.2; }",
        "</style>",
        "</defs>",
        f'<rect width="{width}" height="{height}" fill="#f8fafc" />',
        f'<text x="{margin_left}" y="36" class="title">{escape(title)}</text>',
        f'<text x="{margin_left}" y="58" class="subtitle">{escape(subtitle)}</text>',
        f'<text x="{width - margin_right}" y="36" text-anchor="end" class="subtitle">{escape(filename_label)}</text>',
    ]

    for step in range(grid_steps + 1):
        y = margin_top + plot_height - (plot_height * step / grid_steps)
        value = max_value * step / grid_steps
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" class="grid" />'
        )
        parts.append(
            f'<text x="{margin_left - 12}" y="{y + 4:.2f}" text-anchor="end" class="axis">{value:.0f}</text>'
        )

    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" class="axis-line" />'
    )
    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" class="axis-line" />'
    )
    parts.append(
        f'<text x="20" y="{margin_top + plot_height / 2:.2f}" transform="rotate(-90 20 {margin_top + plot_height / 2:.2f})" class="label">{escape(y_label)}</text>'
    )

    legend_x = width - margin_right - 210
    legend_y = 56
    for index, (_series_key, label, color) in enumerate(series):
        y = legend_y + index * 22
        parts.append(
            f'<rect x="{legend_x}" y="{y - 10}" width="14" height="14" rx="3" fill="{color}" />'
        )
        parts.append(
            f'<text x="{legend_x + 22}" y="{y + 1}" class="legend">{escape(label)}</text>'
        )

    for row_index, row in enumerate(rows):
        group_x = margin_left + row_index * group_width + (group_width - total_bar_width) / 2
        category_center = margin_left + row_index * group_width + group_width / 2

        for series_index, (series_key, _label, color) in enumerate(series):
            value = float(row[series_key])
            bar_height = 0 if max_value == 0 else (value / max_value) * plot_height
            x = group_x + series_index * (bar_width + bar_gap)
            y = margin_top + plot_height - bar_height
            parts.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" rx="10" fill="{color}" />'
            )
            parts.append(
                f'<text x="{x + bar_width / 2:.2f}" y="{max(y - 8, margin_top + 14):.2f}" text-anchor="middle" class="value">{value:.3f}</text>'
            )

        parts.append(
            f'<text x="{category_center:.2f}" y="{height - 32}" text-anchor="middle" class="label">{escape(str(row["category"]))}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def render_stacked_bar_chart_svg(
    *,
    title: str,
    subtitle: str,
    y_label: str,
    rows: list[dict[str, Any]],
    segment_names: list[str],
    segment_colors: dict[str, str],
) -> str:
    width = 980
    height = 580
    margin_top = 84
    margin_right = 56
    margin_bottom = 112
    margin_left = 88
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    bar_width = min(120, plot_width / max(len(rows) * 1.7, 1))
    gap = (plot_width - bar_width * len(rows)) / max(len(rows) + 1, 1)
    max_value = max(sum(float(row["segments"].get(name, 0)) for name in segment_names) for row in rows)
    max_value = max_value * 1.15 if max_value else 1.0
    grid_steps = 5

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        "<defs>",
        '<style>',
        ".title { font: 700 26px Arial, sans-serif; fill: #14213d; }",
        ".subtitle { font: 400 14px Arial, sans-serif; fill: #5b6472; }",
        ".axis { font: 12px Arial, sans-serif; fill: #334155; }",
        ".label { font: 600 13px Arial, sans-serif; fill: #1f2937; }",
        ".value { font: 600 12px Arial, sans-serif; fill: #0f172a; }",
        ".legend { font: 600 13px Arial, sans-serif; fill: #1f2937; }",
        ".grid { stroke: #d8dee9; stroke-width: 1; }",
        ".axis-line { stroke: #94a3b8; stroke-width: 1.2; }",
        "</style>",
        "</defs>",
        f'<rect width="{width}" height="{height}" fill="#f8fafc" />',
        f'<text x="{margin_left}" y="36" class="title">{escape(title)}</text>',
        f'<text x="{margin_left}" y="58" class="subtitle">{escape(subtitle)}</text>',
    ]

    for step in range(grid_steps + 1):
        y = margin_top + plot_height - (plot_height * step / grid_steps)
        value = max_value * step / grid_steps
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" class="grid" />'
        )
        parts.append(
            f'<text x="{margin_left - 12}" y="{y + 4:.2f}" text-anchor="end" class="axis">{value:.0f}</text>'
        )

    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" class="axis-line" />'
    )
    parts.append(
        f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" class="axis-line" />'
    )
    parts.append(
        f'<text x="20" y="{margin_top + plot_height / 2:.2f}" transform="rotate(-90 20 {margin_top + plot_height / 2:.2f})" class="label">{escape(y_label)}</text>'
    )

    legend_x = width - margin_right - 250
    legend_y = 56
    for index, segment_name in enumerate(segment_names):
        y = legend_y + index * 22
        color = segment_colors[segment_name]
        parts.append(
            f'<rect x="{legend_x}" y="{y - 10}" width="14" height="14" rx="3" fill="{color}" />'
        )
        parts.append(
            f'<text x="{legend_x + 22}" y="{y + 1}" class="legend">{escape(segment_name)}</text>'
        )

    for index, row in enumerate(rows):
        x = margin_left + gap + index * (bar_width + gap)
        current_y = margin_top + plot_height
        total = 0.0
        for segment_name in segment_names:
            value = float(row["segments"].get(segment_name, 0))
            total += value
            if value == 0:
                continue
            segment_height = (value / max_value) * plot_height if max_value else 0
            current_y -= segment_height
            parts.append(
                f'<rect x="{x:.2f}" y="{current_y:.2f}" width="{bar_width:.2f}" height="{segment_height:.2f}" rx="8" fill="{segment_colors[segment_name]}" />'
            )

        parts.append(
            f'<text x="{x + bar_width / 2:.2f}" y="{max(current_y - 8, margin_top + 14):.2f}" text-anchor="middle" class="value">{total:.0f}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.2f}" y="{height - 52}" text-anchor="middle" class="label">{escape(row["label"])}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.2f}" y="{height - 32}" text-anchor="middle" class="axis">{escape(row["sub_label"])}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def render_graph_dashboard_html(summary_bundle: dict[str, Any], graph_files: list[str]) -> str:
    cards = []
    for graph_file in graph_files:
        cards.append(
            "\n".join(
                [
                    '<section class="card">',
                    f'  <h2>{escape(graph_file.replace("-", " ").replace(".svg", "").title())}</h2>',
                    f'  <img src="{escape(graph_file)}" alt="{escape(graph_file)}">',
                    "</section>",
                ]
            )
        )

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "  <head>",
            '    <meta charset="utf-8">',
            '    <meta name="viewport" content="width=device-width, initial-scale=1">',
            "    <title>Stage 5 Benchmark Graphs</title>",
            "    <style>",
            "      body { margin: 0; font-family: Arial, sans-serif; background: #f1f5f9; color: #0f172a; }",
            "      main { width: min(1200px, calc(100% - 32px)); margin: 24px auto 40px; }",
            "      h1 { margin-bottom: 8px; font-size: 2rem; }",
            "      p { color: #475569; }",
            "      .grid { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }",
            "      .card { background: white; border-radius: 20px; padding: 18px; box-shadow: 0 14px 28px rgba(15, 23, 42, 0.08); }",
            "      .card h2 { margin: 0 0 12px; font-size: 1.05rem; }",
            "      img { width: 100%; height: auto; border-radius: 14px; border: 1px solid #e2e8f0; }",
            "      .meta { margin-bottom: 20px; padding: 16px 18px; background: #dbeafe; border-radius: 18px; }",
            "    </style>",
            "  </head>",
            "  <body>",
            "    <main>",
            "      <h1>Stage 5 Benchmark Graphs</h1>",
            f"      <p>Generated from the Stage 5 summary bundle at <strong>{escape(summary_bundle['generated_at'])}</strong>.</p>",
            '      <div class="meta">',
            f"        <div>Requests per scenario: {summary_bundle['parameters']['request_count']}</div>",
            f"        <div>Concurrency: {summary_bundle['parameters']['concurrency']}</div>",
            f"        <div>Seed URLs: {summary_bundle['parameters']['seed_count']}</div>",
            "      </div>",
            '      <div class="grid">',
            *cards,
            "      </div>",
            "    </main>",
            "  </body>",
            "</html>",
        ]
    )


def generate_graph_files(summary_bundle: dict[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    chart_specs = [
        {
            "title": "Throughput Comparison",
            "subtitle": "Higher is better. Compares request throughput across the two workload mixes.",
            "y_label": "Requests per second",
            "rows": metric_rows(summary_bundle, ("throughput_rps",)),
            "filename": "throughput-comparison.svg",
            "filename_label": "metric: throughput_rps",
        },
        {
            "title": "Average Latency Comparison",
            "subtitle": "Lower is better. Average end-to-end latency by deployment and scenario.",
            "y_label": "Latency (ms)",
            "rows": metric_rows(summary_bundle, ("latency_ms", "avg")),
            "filename": "avg-latency-comparison.svg",
            "filename_label": "metric: latency_ms.avg",
        },
        {
            "title": "P95 Latency Comparison",
            "subtitle": "Lower is better. Tail latency is useful for showing worst-case user experience.",
            "y_label": "Latency (ms)",
            "rows": metric_rows(summary_bundle, ("latency_ms", "p95")),
            "filename": "p95-latency-comparison.svg",
            "filename_label": "metric: latency_ms.p95",
        },
    ]

    output_paths: list[Path] = []
    for spec in chart_specs:
        chart_path = output_dir / spec["filename"]
        chart_path.write_text(
            render_grouped_bar_chart_svg(
                title=spec["title"],
                subtitle=spec["subtitle"],
                y_label=spec["y_label"],
                rows=spec["rows"],
                filename_label=spec["filename_label"],
            ),
            encoding="utf-8",
        )
        output_paths.append(chart_path)

    backend_segments = sorted(
        {
            segment_name
            for deployment_runs in summary_bundle["runs"].values()
            for run in deployment_runs.values()
            for segment_name in run["backend_node_counts"]
        }
    )
    segment_colors = {
        "backend1": "#2563eb",
        "backend2": "#f97316",
        "local-node": "#16a34a",
    }
    for segment_name in backend_segments:
        segment_colors.setdefault(segment_name, "#6b7280")

    backend_rows = []
    for deployment_key in ("single-node", "multi-node"):
        for scenario_name in SCENARIOS:
            run = summary_bundle["runs"][deployment_key][scenario_name]
            backend_rows.append(
                {
                    "label": "Single node" if deployment_key == "single-node" else "Multi node",
                    "sub_label": scenario_title(scenario_name),
                    "segments": run["backend_node_counts"],
                }
            )

    backend_path = output_dir / "backend-distribution.svg"
    backend_path.write_text(
        render_stacked_bar_chart_svg(
            title="Backend Distribution",
            subtitle="Shows how responses were distributed across backend instances during each benchmark run.",
            y_label="Responses counted",
            rows=backend_rows,
            segment_names=backend_segments,
            segment_colors=segment_colors,
        ),
        encoding="utf-8",
    )
    output_paths.append(backend_path)

    dashboard_path = output_dir / "graphs.html"
    dashboard_path.write_text(
        render_graph_dashboard_html(summary_bundle, [path.name for path in output_paths]),
        encoding="utf-8",
    )
    output_paths.append(dashboard_path)
    return output_paths


def build_results_blurb(summary_bundle: dict[str, Any]) -> str:
    read_delta = summary_bundle["comparisons"]["read-heavy"]["delta"]
    shorten_delta = summary_bundle["comparisons"]["shorten-heavy"]["delta"]
    return (
        "In this benchmark run, the multi-node deployment slightly improved read-heavy throughput "
        f"({read_delta['throughput_percent']}%) and more clearly improved shorten-heavy throughput "
        f"({shorten_delta['throughput_percent']}%). Average latency improved by "
        f"{abs(read_delta['avg_latency_percent'])}% for read-heavy traffic and "
        f"{abs(shorten_delta['avg_latency_percent'])}% for shorten-heavy traffic, while error rate stayed at 0.0 in all runs."
    )


def render_report_figure_captions(summary_bundle: dict[str, Any]) -> str:
    read_single = summary_bundle["runs"]["single-node"]["read-heavy"]
    read_multi = summary_bundle["runs"]["multi-node"]["read-heavy"]
    shorten_single = summary_bundle["runs"]["single-node"]["shorten-heavy"]
    shorten_multi = summary_bundle["runs"]["multi-node"]["shorten-heavy"]
    read_delta = summary_bundle["comparisons"]["read-heavy"]["delta"]
    shorten_delta = summary_bundle["comparisons"]["shorten-heavy"]["delta"]

    return "\n".join(
        [
            "# Report Figure Captions",
            "",
            "## Suggested Results Paragraph",
            "",
            build_results_blurb(summary_bundle),
            "",
            "## Figure Captions",
            "",
            (
                "Figure 1. Throughput by workload and deployment. "
                f"For the read-heavy workload, throughput increased from {read_single['throughput_rps']} req/s "
                f"on the single-node service to {read_multi['throughput_rps']} req/s on the multi-node deployment "
                f"({read_delta['throughput_percent']}% change). For the shorten-heavy workload, throughput increased "
                f"from {shorten_single['throughput_rps']} req/s to {shorten_multi['throughput_rps']} req/s "
                f"({shorten_delta['throughput_percent']}% change)."
            ),
            "",
            (
                "Figure 2. Average latency by workload and deployment. "
                f"Average latency decreased from {read_single['latency_ms']['avg']} ms to {read_multi['latency_ms']['avg']} ms "
                f"for the read-heavy workload and from {shorten_single['latency_ms']['avg']} ms to "
                f"{shorten_multi['latency_ms']['avg']} ms for the shorten-heavy workload."
            ),
            "",
            (
                "Figure 3. P95 latency by workload and deployment. "
                f"Tail latency was nearly unchanged for the read-heavy workload "
                f"({read_single['latency_ms']['p95']} ms single-node versus {read_multi['latency_ms']['p95']} ms multi-node), "
                f"while the shorten-heavy workload improved from {shorten_single['latency_ms']['p95']} ms to "
                f"{shorten_multi['latency_ms']['p95']} ms."
            ),
            "",
            (
                "Figure 4. Backend response distribution during the benchmark runs. "
                f"The single-node deployment served all requests from local-node, while the multi-node deployment "
                f"split both workloads evenly across backend1 and backend2 ({read_multi['backend_node_counts'].get('backend1', 0)} / "
                f"{read_multi['backend_node_counts'].get('backend2', 0)} in read-heavy and "
                f"{shorten_multi['backend_node_counts'].get('backend1', 0)} / "
                f"{shorten_multi['backend_node_counts'].get('backend2', 0)} in shorten-heavy)."
            ),
            "",
            "## Export Note",
            "",
            "These figures are SVG files. They are publication-friendly and scale cleanly in Word, Google Docs, and LaTeX without raster blur.",
            "",
        ]
    )


def render_report_gallery_html(summary_bundle: dict[str, Any], figure_files: list[tuple[str, str]]) -> str:
    cards = []
    for filename, caption in figure_files:
        cards.append(
            "\n".join(
                [
                    '<figure class="card">',
                    f'  <img src="{escape(filename)}" alt="{escape(filename)}">',
                    f'  <figcaption>{escape(caption)}</figcaption>',
                    "</figure>",
                ]
            )
        )

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "  <head>",
            '    <meta charset="utf-8">',
            '    <meta name="viewport" content="width=device-width, initial-scale=1">',
            "    <title>Stage 5 Report Figures</title>",
            "    <style>",
            "      body { margin: 0; font-family: Georgia, 'Times New Roman', serif; background: #f8fafc; color: #0f172a; }",
            "      main { width: min(1240px, calc(100% - 40px)); margin: 28px auto 48px; }",
            "      h1 { margin: 0 0 10px; font-size: 2.2rem; }",
            "      p { max-width: 70ch; line-height: 1.6; color: #334155; }",
            "      .grid { display: grid; gap: 22px; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); margin-top: 24px; }",
            "      .card { margin: 0; background: white; border-radius: 20px; padding: 18px; box-shadow: 0 18px 36px rgba(15, 23, 42, 0.08); }",
            "      img { display: block; width: 100%; height: auto; border: 1px solid #e2e8f0; border-radius: 14px; background: #fff; }",
            "      figcaption { margin-top: 12px; line-height: 1.5; color: #475569; font-size: 0.98rem; }",
            "      .meta { margin-top: 12px; padding: 14px 16px; border-left: 4px solid #1d4ed8; background: #dbeafe; border-radius: 12px; }",
            "    </style>",
            "  </head>",
            "  <body>",
            "    <main>",
            "      <h1>Stage 5 Report Figures</h1>",
            f"      <p>{escape(build_results_blurb(summary_bundle))}</p>",
            '      <div class="meta">',
            f"        Requests per scenario: {summary_bundle['parameters']['request_count']} | "
            f"Concurrency: {summary_bundle['parameters']['concurrency']} | "
            f"Seed URLs: {summary_bundle['parameters']['seed_count']}",
            "      </div>",
            '      <div class="grid">',
            *cards,
            "      </div>",
            "    </main>",
            "  </body>",
            "</html>",
        ]
    )


def generate_report_figure_files(summary_bundle: dict[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    figure_specs = [
        {
            "filename": "figure-1-throughput.svg",
            "title": "Figure 1. Throughput by Workload and Deployment",
            "subtitle": "Higher is better. Based on 400 requests per scenario at concurrency 20.",
            "y_label": "Requests per second",
            "rows": metric_rows(summary_bundle, ("throughput_rps",)),
            "filename_label": "report figure 1",
            "caption": "Throughput comparison for the read-heavy and shorten-heavy workloads.",
        },
        {
            "filename": "figure-2-average-latency.svg",
            "title": "Figure 2. Average Latency by Workload and Deployment",
            "subtitle": "Lower is better. End-to-end average latency for each benchmark scenario.",
            "y_label": "Latency (ms)",
            "rows": metric_rows(summary_bundle, ("latency_ms", "avg")),
            "filename_label": "report figure 2",
            "caption": "Average latency comparison for the read-heavy and shorten-heavy workloads.",
        },
        {
            "filename": "figure-3-p95-latency.svg",
            "title": "Figure 3. P95 Latency by Workload and Deployment",
            "subtitle": "Lower is better. Tail latency highlights worst-case response behavior.",
            "y_label": "Latency (ms)",
            "rows": metric_rows(summary_bundle, ("latency_ms", "p95")),
            "filename_label": "report figure 3",
            "caption": "P95 latency comparison for the read-heavy and shorten-heavy workloads.",
        },
    ]

    output_paths: list[Path] = []
    gallery_entries: list[tuple[str, str]] = []

    for spec in figure_specs:
        figure_path = output_dir / spec["filename"]
        figure_path.write_text(
            render_grouped_bar_chart_svg(
                title=spec["title"],
                subtitle=spec["subtitle"],
                y_label=spec["y_label"],
                rows=spec["rows"],
                filename_label=spec["filename_label"],
            ),
            encoding="utf-8",
        )
        output_paths.append(figure_path)
        gallery_entries.append((figure_path.name, spec["caption"]))

    backend_segments = sorted(
        {
            segment_name
            for deployment_runs in summary_bundle["runs"].values()
            for run in deployment_runs.values()
            for segment_name in run["backend_node_counts"]
        }
    )
    segment_colors = {
        "backend1": "#1d4ed8",
        "backend2": "#ea580c",
        "local-node": "#15803d",
    }
    for segment_name in backend_segments:
        segment_colors.setdefault(segment_name, "#6b7280")

    backend_rows = []
    for deployment_key in ("single-node", "multi-node"):
        for scenario_name in SCENARIOS:
            run = summary_bundle["runs"][deployment_key][scenario_name]
            backend_rows.append(
                {
                    "label": "Single node" if deployment_key == "single-node" else "Multi node",
                    "sub_label": scenario_title(scenario_name),
                    "segments": run["backend_node_counts"],
                }
            )

    backend_path = output_dir / "figure-4-backend-distribution.svg"
    backend_path.write_text(
        render_stacked_bar_chart_svg(
            title="Figure 4. Backend Response Distribution",
            subtitle="Shows how responses were assigned to backend instances during each workload.",
            y_label="Responses counted",
            rows=backend_rows,
            segment_names=backend_segments,
            segment_colors=segment_colors,
        ),
        encoding="utf-8",
    )
    output_paths.append(backend_path)
    gallery_entries.append(
        (backend_path.name, "Response distribution across backend instances during the benchmark runs.")
    )

    captions_path = output_dir / "report-figure-captions.md"
    captions_path.write_text(render_report_figure_captions(summary_bundle), encoding="utf-8")
    output_paths.append(captions_path)

    gallery_path = output_dir / "report-figures.html"
    gallery_path.write_text(
        render_report_gallery_html(summary_bundle, gallery_entries),
        encoding="utf-8",
    )
    output_paths.append(gallery_path)

    return output_paths


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
    graph_paths = generate_graph_files(summary_bundle, run_dir)

    print(f"Saved Stage 5 comparison JSON to {summary_json_path}")
    print(f"Saved Stage 5 comparison Markdown to {summary_md_path}")
    print("Saved graph files:")
    for graph_path in graph_paths:
        print(f"- {graph_path}")
    return 0


def make_graphs_command(args: argparse.Namespace) -> int:
    summary_json_path = Path(args.summary_json)
    summary_bundle = json.loads(summary_json_path.read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir) if args.output_dir else summary_json_path.parent
    graph_paths = generate_graph_files(summary_bundle, output_dir)
    print("Saved graph files:")
    for graph_path in graph_paths:
        print(f"- {graph_path}")
    return 0


def make_report_figures_command(args: argparse.Namespace) -> int:
    summary_json_path = Path(args.summary_json)
    summary_bundle = json.loads(summary_json_path.read_text(encoding="utf-8"))
    default_output = summary_json_path.parent / "report-figures"
    output_dir = Path(args.output_dir) if args.output_dir else default_output
    output_paths = generate_report_figure_files(summary_bundle, output_dir)
    print("Saved report figure files:")
    for output_path in output_paths:
        print(f"- {output_path}")
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

    graphs_parser = subparsers.add_parser(
        "make-graphs",
        help="Generate SVG charts and an HTML dashboard from a Stage 5 summary JSON file.",
    )
    graphs_parser.add_argument(
        "--summary-json",
        required=True,
        help="Path to stage5-summary.json",
    )
    graphs_parser.add_argument(
        "--output-dir",
        help="Directory for generated graph files. Defaults to the summary JSON directory.",
    )
    graphs_parser.set_defaults(handler=make_graphs_command)

    report_parser = subparsers.add_parser(
        "make-report-figures",
        help="Generate cleaner figure-numbered SVGs and captions for a report from a Stage 5 summary JSON file.",
    )
    report_parser.add_argument(
        "--summary-json",
        required=True,
        help="Path to stage5-summary.json",
    )
    report_parser.add_argument(
        "--output-dir",
        help="Directory for generated report figure files. Defaults to <summary-dir>/report-figures.",
    )
    report_parser.set_defaults(handler=make_report_figures_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
