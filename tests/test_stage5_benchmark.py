from __future__ import annotations

import json
import shutil
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import socket
from typing import ClassVar
from uuid import uuid4

import benchmark


class StubBenchmarkHandler(BaseHTTPRequestHandler):
    mappings: ClassVar[dict[str, dict[str, object]]] = {}
    next_code: ClassVar[int] = 0

    def log_message(self, format, *args):  # noqa: A003
        return

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Backend-Node", "stub-node")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return

        if self.path == "/api/v1/node":
            self._send_json({"instance_name": "stub-node", "database_path": "stub.db"})
            return

        if self.path == "/api/v1/urls":
            payload = []
            for code, mapping in self.mappings.items():
                payload.append(
                    {
                        "code": code,
                        "long_url": mapping["long_url"],
                        "click_count": mapping["click_count"],
                        "short_url": f"http://127.0.0.1:{self.server.server_port}/{code}",
                    }
                )
            self._send_json(payload)
            return

        if self.path.startswith("/api/v1/urls/"):
            code = self.path.rsplit("/", 1)[-1]
            mapping = self.mappings.get(code)
            if mapping is None:
                self._send_json({"error": "missing"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(
                {
                    "code": code,
                    "long_url": mapping["long_url"],
                    "click_count": mapping["click_count"],
                }
            )
            return

        code = self.path.lstrip("/")
        mapping = self.mappings.get(code)
        if mapping is None:
            self._send_json({"error": "missing"}, status=HTTPStatus.NOT_FOUND)
            return

        mapping["click_count"] += 1
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", str(mapping["long_url"]))
        self.send_header("X-Backend-Node", "stub-node")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/v1/urls":
            self._send_json({"error": "unsupported"}, status=HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length) or b"{}")
        long_url = payload["url"]
        code = f"c{self.next_code:04d}"
        type(self).next_code += 1
        self.mappings[code] = {
            "long_url": long_url,
            "click_count": 0,
        }
        self._send_json(
            {
                "code": code,
                "long_url": long_url,
                "click_count": 0,
                "short_url": f"http://127.0.0.1:{self.server.server_port}/{code}",
            },
            status=HTTPStatus.CREATED,
        )


class BenchmarkScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        StubBenchmarkHandler.mappings = {}
        StubBenchmarkHandler.next_code = 0

        with socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]

        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), StubBenchmarkHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)

    def test_run_benchmark_read_heavy(self):
        summary = benchmark.run_benchmark(
            base_url=self.base_url,
            deployment_label="stub-single",
            scenario_name="read-heavy",
            request_count=20,
            concurrency=4,
            seed_count=6,
            timeout_seconds=2.0,
            random_seed=123,
        )

        self.assertEqual(summary["scenario"], "read-heavy")
        self.assertEqual(summary["request_count"], 20)
        self.assertEqual(summary["error_count"], 0)
        self.assertGreater(summary["throughput_rps"], 0)
        self.assertIn("stub-node", summary["backend_node_counts"])

    def test_run_benchmark_shorten_heavy(self):
        summary = benchmark.run_benchmark(
            base_url=self.base_url,
            deployment_label="stub-multi",
            scenario_name="shorten-heavy",
            request_count=20,
            concurrency=4,
            seed_count=6,
            timeout_seconds=2.0,
            random_seed=321,
        )

        self.assertEqual(summary["scenario"], "shorten-heavy")
        self.assertEqual(summary["request_count"], 20)
        self.assertGreater(summary["operation_counts"]["create"], 0)
        self.assertEqual(summary["error_count"], 0)

    def test_compare_summaries(self):
        single = {
            "scenario": "read-heavy",
            "throughput_rps": 100.0,
            "latency_ms": {"avg": 10.0, "p95": 20.0},
            "error_rate": 0.0,
        }
        multi = {
            "scenario": "read-heavy",
            "throughput_rps": 120.0,
            "latency_ms": {"avg": 8.0, "p95": 15.0},
            "error_rate": 0.01,
        }

        comparison = benchmark.compare_summaries(single, multi)

        self.assertEqual(comparison["scenario"], "read-heavy")
        self.assertEqual(comparison["delta"]["throughput_rps"], 20.0)
        self.assertEqual(comparison["delta"]["avg_latency_ms"], -2.0)
        self.assertEqual(comparison["delta"]["p95_latency_ms"], -5.0)

    def test_sample_backend_nodes(self):
        samples = benchmark.sample_backend_nodes(
            self.base_url,
            attempts=3,
            timeout_seconds=2.0,
        )

        self.assertEqual(samples, ["stub-node", "stub-node", "stub-node"])

    def test_run_easy_case(self):
        case_bundle = benchmark.run_easy_case(
            single_url=self.base_url,
            request_count=12,
            concurrency=3,
            seed_count=4,
            timeout_seconds=2.0,
            random_seed=222,
        )

        self.assertEqual(case_bundle["case"], "easy")
        self.assertIn("single-node / read-heavy", case_bundle["runs"])
        self.assertEqual(
            case_bundle["runs"]["single-node / read-heavy"]["request_count"], 12
        )

    def test_build_hard_manual_bundle(self):
        case_bundle = benchmark.build_hard_manual_bundle(
            multi_url="http://127.0.0.1:8080",
            manual_code="abc123",
            timeout_seconds=5.0,
            random_seed=4675,
        )

        self.assertEqual(case_bundle["case"], "hard")
        self.assertEqual(case_bundle["mode"], "manual")
        self.assertIn("abc123", "\n".join(case_bundle["manual_commands"]))

    def test_generate_graph_files(self):
        summary_bundle = {
            "generated_at": "2026-04-15T17:36:37+00:00",
            "parameters": {
                "request_count": 400,
                "concurrency": 20,
                "seed_count": 100,
                "timeout_seconds": 5.0,
                "random_seed": 4675,
            },
            "runs": {
                "single-node": {
                    "read-heavy": {
                        "throughput_rps": 10.0,
                        "latency_ms": {"avg": 100.0, "p95": 150.0},
                        "backend_node_counts": {"local-node": 400},
                    },
                    "shorten-heavy": {
                        "throughput_rps": 9.0,
                        "latency_ms": {"avg": 120.0, "p95": 180.0},
                        "backend_node_counts": {"local-node": 400},
                    },
                },
                "multi-node": {
                    "read-heavy": {
                        "throughput_rps": 11.0,
                        "latency_ms": {"avg": 90.0, "p95": 140.0},
                        "backend_node_counts": {"backend1": 200, "backend2": 200},
                    },
                    "shorten-heavy": {
                        "throughput_rps": 10.5,
                        "latency_ms": {"avg": 95.0, "p95": 145.0},
                        "backend_node_counts": {"backend1": 200, "backend2": 200},
                    },
                },
            },
            "comparisons": {},
        }

        output_dir = Path("test_data") / f"graphs-{uuid4().hex}"
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            output_paths = benchmark.generate_graph_files(summary_bundle, output_dir)

            self.assertEqual(len(output_paths), 5)
            throughput_svg = output_dir / "throughput-comparison.svg"
            dashboard_html = output_dir / "graphs.html"

            self.assertTrue(throughput_svg.exists())
            self.assertTrue(dashboard_html.exists())
            self.assertIn("<svg", throughput_svg.read_text(encoding="utf-8"))
            self.assertIn("Throughput Comparison", throughput_svg.read_text(encoding="utf-8"))
            self.assertIn("Stage 5 Benchmark Graphs", dashboard_html.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_run_concurrency_study_and_generate_graphs(self):
        output_dir = Path("test_data") / f"concurrency-study-{uuid4().hex}"
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            summary_bundle = benchmark.run_concurrency_study(
                single_url=self.base_url,
                multi_url=self.base_url,
                request_count=12,
                concurrency_levels=[1, 2],
                seed_count=4,
                timeout_seconds=2.0,
                random_seed=999,
                pause_seconds=0.0,
                run_dir=output_dir,
            )

            self.assertEqual(summary_bundle["concurrency_levels"], [1, 2])
            self.assertEqual(summary_bundle["parameters"]["request_count"], 12)
            self.assertEqual(
                summary_bundle["runs"]["single-node"]["read-heavy"]["1"]["concurrency"],
                1,
            )
            self.assertEqual(
                summary_bundle["runs"]["multi-node"]["shorten-heavy"]["2"]["concurrency"],
                2,
            )
            self.assertTrue((output_dir / "single-node-read-heavy-c1.json").exists())
            self.assertTrue((output_dir / "multi-node-shorten-heavy-c2.json").exists())

            output_paths = benchmark.generate_concurrency_graph_files(summary_bundle, output_dir)

            self.assertEqual(len(output_paths), 5)
            self.assertTrue((output_dir / "throughput-by-concurrency.svg").exists())
            self.assertTrue((output_dir / "avg-latency-by-concurrency.svg").exists())
            self.assertTrue((output_dir / "p95-latency-by-concurrency.svg").exists())
            self.assertTrue((output_dir / "error-rate-by-concurrency.svg").exists())
            self.assertTrue((output_dir / "graphs.html").exists())
            self.assertIn(
                "Throughput by Concurrency",
                (output_dir / "throughput-by-concurrency.svg").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Controlled Concurrency Study Graphs",
                (output_dir / "graphs.html").read_text(encoding="utf-8"),
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_generate_report_figure_files(self):
        summary_bundle = {
            "generated_at": "2026-04-15T17:36:37+00:00",
            "parameters": {
                "request_count": 400,
                "concurrency": 20,
                "seed_count": 100,
                "timeout_seconds": 5.0,
                "random_seed": 4675,
            },
            "runs": {
                "single-node": {
                    "read-heavy": {
                        "throughput_rps": 10.0,
                        "latency_ms": {"avg": 100.0, "p95": 150.0},
                        "backend_node_counts": {"local-node": 400},
                    },
                    "shorten-heavy": {
                        "throughput_rps": 9.0,
                        "latency_ms": {"avg": 120.0, "p95": 180.0},
                        "backend_node_counts": {"local-node": 400},
                    },
                },
                "multi-node": {
                    "read-heavy": {
                        "throughput_rps": 11.0,
                        "latency_ms": {"avg": 90.0, "p95": 140.0},
                        "backend_node_counts": {"backend1": 200, "backend2": 200},
                    },
                    "shorten-heavy": {
                        "throughput_rps": 10.5,
                        "latency_ms": {"avg": 95.0, "p95": 145.0},
                        "backend_node_counts": {"backend1": 200, "backend2": 200},
                    },
                },
            },
            "comparisons": {
                "read-heavy": {
                    "delta": {
                        "throughput_percent": 10.0,
                        "avg_latency_percent": -10.0,
                    }
                },
                "shorten-heavy": {
                    "delta": {
                        "throughput_percent": 16.0,
                        "avg_latency_percent": -20.0,
                    }
                },
            },
        }

        output_dir = Path("test_data") / f"report-figures-{uuid4().hex}"
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            output_paths = benchmark.generate_report_figure_files(summary_bundle, output_dir)

            self.assertEqual(len(output_paths), 6)
            figure_1 = output_dir / "figure-1-throughput.svg"
            captions_md = output_dir / "report-figure-captions.md"
            gallery_html = output_dir / "report-figures.html"

            self.assertTrue(figure_1.exists())
            self.assertTrue(captions_md.exists())
            self.assertTrue(gallery_html.exists())
            self.assertIn("Figure 1. Throughput by Workload and Deployment", figure_1.read_text(encoding="utf-8"))
            self.assertIn("Figure 1. Throughput by workload and deployment.", captions_md.read_text(encoding="utf-8"))
            self.assertIn("Stage 5 Report Figures", gallery_html.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_generate_case_graph_files(self):
        case_bundle = {
            "case": "medium",
            "mode": "benchmark",
            "title": "Medium Case",
            "description": "Test case bundle.",
            "generated_at": "2026-04-16T18:00:00+00:00",
            "parameters": {
                "request_count": 10,
                "concurrency": 2,
                "seed_count": 3,
                "timeout_seconds": 2.0,
                "random_seed": 1,
            },
            "runs": {
                "multi-node / read-heavy": {
                    "throughput_rps": 11.0,
                    "latency_ms": {"avg": 90.0, "p95": 140.0},
                    "backend_node_counts": {"backend1": 5, "backend2": 5},
                    "error_rate": 0.0,
                },
                "multi-node / shorten-heavy": {
                    "throughput_rps": 10.5,
                    "latency_ms": {"avg": 95.0, "p95": 145.0},
                    "backend_node_counts": {"backend1": 5, "backend2": 5},
                    "error_rate": 0.0,
                },
            },
            "notes": [],
        }

        output_dir = Path("test_data") / f"case-graphs-{uuid4().hex}"
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            output_paths = benchmark.generate_case_graph_files(case_bundle, output_dir)

            self.assertEqual(len(output_paths), 5)
            self.assertTrue((output_dir / "throughput.svg").exists())
            self.assertTrue((output_dir / "graphs.html").exists())
            self.assertIn(
                "Medium Case: Throughput",
                (output_dir / "throughput.svg").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Medium Case Graphs",
                (output_dir / "graphs.html").read_text(encoding="utf-8"),
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_generate_case_comparison_graph_files(self):
        case_sources = [
            (
                Path("easy-case.json"),
                {
                    "case": "easy",
                    "mode": "benchmark",
                    "title": "Easy Case",
                    "runs": {
                        "single-node / read-heavy": {
                            "scenario": "read-heavy",
                            "deployment": "single-node",
                            "throughput_rps": 56.268,
                            "latency_ms": {"avg": 102.978, "p95": 189.198},
                            "error_rate": 0.0,
                        }
                    },
                },
            ),
            (
                Path("medium-case.json"),
                {
                    "case": "medium",
                    "mode": "benchmark",
                    "title": "Medium Case",
                    "runs": {
                        "multi-node / read-heavy": {
                            "scenario": "read-heavy",
                            "deployment": "multi-node",
                            "throughput_rps": 82.476,
                            "latency_ms": {"avg": 94.245, "p95": 198.637},
                            "error_rate": 0.0,
                        },
                        "multi-node / shorten-heavy": {
                            "scenario": "shorten-heavy",
                            "deployment": "multi-node",
                            "throughput_rps": 68.221,
                            "latency_ms": {"avg": 112.1, "p95": 234.196},
                            "error_rate": 0.0,
                        },
                    },
                },
            ),
            (
                Path("hard-case.json"),
                {
                    "case": "hard",
                    "mode": "benchmark",
                    "title": "Hard Case",
                    "runs": {
                        "multi-node failover / read-heavy": {
                            "scenario": "read-heavy",
                            "deployment": "multi-node-failover",
                            "throughput_rps": 65.113,
                            "latency_ms": {"avg": 60.226, "p95": 171.399},
                            "error_rate": 0.0,
                        }
                    },
                },
            ),
            (
                Path("hard-manual-case.json"),
                {
                    "case": "hard",
                    "mode": "manual",
                    "title": "Hard Case Manual Runbook",
                    "manual_steps": ["1. Stop backend1"],
                },
            ),
        ]

        comparison_bundle = benchmark.build_case_comparison_bundle(case_sources)

        self.assertEqual(len(comparison_bundle["runs"]), 4)
        self.assertEqual(
            [run["label"] for run in comparison_bundle["runs"]],
            [
                "Easy: Single-node Read-Heavy",
                "Medium: Read Heavy",
                "Medium: Shorten Heavy",
                "Hard: Failover Read Heavy",
            ],
        )
        self.assertEqual(len(comparison_bundle["skipped_cases"]), 1)

        output_dir = Path("test_data") / f"combined-case-graphs-{uuid4().hex}"
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            output_paths = benchmark.generate_case_comparison_graph_files(
                comparison_bundle,
                output_dir,
            )

            self.assertEqual(len(output_paths), 5)
            self.assertTrue((output_dir / "throughput.svg").exists())
            self.assertTrue((output_dir / "avg-latency.svg").exists())
            self.assertTrue((output_dir / "p95-latency.svg").exists())
            self.assertTrue((output_dir / "error-rate.svg").exists())
            self.assertTrue((output_dir / "graphs.html").exists())
            self.assertIn(
                "Easy, Medium, and Hard: Error Rate",
                (output_dir / "error-rate.svg").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Manual case has no benchmark metrics to chart.",
                (output_dir / "graphs.html").read_text(encoding="utf-8"),
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
