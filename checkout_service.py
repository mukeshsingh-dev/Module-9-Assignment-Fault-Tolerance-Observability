#!/usr/bin/env python3
"""Checkout Service that calls Payment Service with a timeout and trace ID."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from uuid import uuid4


HOST = "127.0.0.1"
PORT = int(os.getenv("CHECKOUT_PORT", "8000"))
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://127.0.0.1:8001/pay")
PAYMENT_TIMEOUT_SECONDS = float(os.getenv("PAYMENT_TIMEOUT_SECONDS", "1"))
metrics_lock = Lock()
metrics = {"requests": 0, "successes": 0, "failures": 0, "timeouts": 0, "total_latency_ms": 0.0}


def log_event(event: str, trace_id: str, **fields: object) -> None:
    record = {"service": "checkout", "event": event, "trace_id": trace_id, **fields}
    print(json.dumps(record), flush=True)


def response_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Trace-ID", payload.get("trace_id", ""))
    handler.end_headers()
    handler.wfile.write(body)


class CheckoutHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            response_json(self, 200, {"service": "checkout", "status": "ok"})
            return
        if self.path == "/metrics":
            with metrics_lock:
                snapshot = dict(metrics)
            requests = snapshot["requests"]
            snapshot["average_latency_ms"] = round(snapshot["total_latency_ms"] / requests, 2) if requests else 0.0
            snapshot["timeout_seconds"] = PAYMENT_TIMEOUT_SECONDS
            snapshot.pop("total_latency_ms")
            response_json(self, 200, snapshot)
            return
        response_json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/checkout":
            response_json(self, 404, {"error": "not found"})
            return
        trace_id = self.headers.get("X-Trace-ID", str(uuid4()))
        started = time.perf_counter()
        with metrics_lock:
            metrics["requests"] += 1
        log_event("request_started", trace_id, payment_url=PAYMENT_URL)
        payload = self._read_json()
        payment_request = urllib.request.Request(
            PAYMENT_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Trace-ID": trace_id},
            method="POST",
        )
        try:
            with urllib.request.urlopen(payment_request, timeout=PAYMENT_TIMEOUT_SECONDS) as payment_response:
                payment_result = json.loads(payment_response.read())
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            with metrics_lock:
                metrics["successes"] += 1
                metrics["total_latency_ms"] += latency_ms
            log_event("request_succeeded", trace_id, latency_ms=latency_ms)
            response_json(self, 200, {"trace_id": trace_id, "status": "checkout_completed", "payment": payment_result, "latency_ms": latency_ms})
        except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            reason = "timeout" if isinstance(error, (TimeoutError, socket.timeout)) or "timed out" in str(error).lower() else "payment_unavailable"
            with metrics_lock:
                metrics["failures"] += 1
                if reason == "timeout":
                    metrics["timeouts"] += 1
                metrics["total_latency_ms"] += latency_ms
            log_event("request_failed", trace_id, reason=reason, latency_ms=latency_ms)
            response_json(self, 504 if reason == "timeout" else 502, {"trace_id": trace_id, "status": "checkout_failed", "error": reason, "latency_ms": latency_ms})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    print(f"Checkout Service listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), CheckoutHandler).serve_forever()
