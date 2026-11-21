#!/usr/bin/env python3
"""Payment Service for the fault-tolerance and observability lab."""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from uuid import uuid4


HOST = "127.0.0.1"
PORT = int(os.getenv("PAYMENT_PORT", "8001"))
metrics_lock = Lock()
metrics = {"requests": 0, "successes": 0, "failures": 0, "total_latency_ms": 0.0}
payment_delay_seconds = float(os.getenv("PAYMENT_DELAY_SECONDS", "0"))


def log_event(event: str, trace_id: str, **fields: object) -> None:
    record = {"service": "payment", "event": event, "trace_id": trace_id, **fields}
    print(json.dumps(record), flush=True)


def response_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Trace-ID", payload.get("trace_id", ""))
    handler.end_headers()
    handler.wfile.write(body)


class PaymentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            response_json(self, 200, {"service": "payment", "status": "ok"})
            return
        if self.path == "/metrics":
            with metrics_lock:
                snapshot = dict(metrics)
            requests = snapshot["requests"]
            snapshot["average_latency_ms"] = round(snapshot["total_latency_ms"] / requests, 2) if requests else 0.0
            snapshot["delay_seconds"] = payment_delay_seconds
            snapshot.pop("total_latency_ms")
            response_json(self, 200, snapshot)
            return
        response_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        global payment_delay_seconds
        trace_id = self.headers.get("X-Trace-ID", str(uuid4()))
        if self.path == "/admin/delay":
            payload = self._read_json()
            payment_delay_seconds = float(payload.get("seconds", 0))
            log_event("delay_updated", trace_id, delay_seconds=payment_delay_seconds)
            response_json(self, 200, {"trace_id": trace_id, "delay_seconds": payment_delay_seconds})
            return
        if self.path != "/pay":
            response_json(self, 404, {"trace_id": trace_id, "error": "not found"})
            return

        started = time.perf_counter()
        with metrics_lock:
            metrics["requests"] += 1
        log_event("request_started", trace_id, delay_seconds=payment_delay_seconds)
        if payment_delay_seconds > 0:
            time.sleep(payment_delay_seconds)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        with metrics_lock:
            metrics["successes"] += 1
            metrics["total_latency_ms"] += latency_ms
        log_event("request_succeeded", trace_id, latency_ms=latency_ms)
        response_json(self, 200, {"trace_id": trace_id, "status": "paid", "latency_ms": latency_ms})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    print(f"Payment Service listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), PaymentHandler).serve_forever()
