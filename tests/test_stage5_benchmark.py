from __future__ import annotations

import json
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import socket
from typing import ClassVar

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


if __name__ == "__main__":
    unittest.main()
