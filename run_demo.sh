#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$(mktemp -d)"
PAYMENT_LOG="$LOG_DIR/payment.log"
CHECKOUT_LOG="$LOG_DIR/checkout.log"

cleanup() {
  kill "$PAYMENT_PID" "$CHECKOUT_PID" 2>/dev/null || true
  wait "$PAYMENT_PID" "$CHECKOUT_PID" 2>/dev/null || true
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT

cd "$ROOT_DIR"
PYTHONUNBUFFERED=1 python3 payment_service.py >"$PAYMENT_LOG" 2>&1 & PAYMENT_PID=$!
PYTHONUNBUFFERED=1 python3 checkout_service.py >"$CHECKOUT_LOG" 2>&1 & CHECKOUT_PID=$!
sleep 1.5

echo "=== OBSERVABILITY AND FAULT-TOLERANCE DEMO ==="
echo
echo "[1] Successful Checkout -> Payment request"
curl -sS -X POST http://127.0.0.1:8000/checkout \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"order-success","amount":42.50}'
echo

echo
echo "[2] Enable 2-second Payment delay (Checkout timeout is 1 second)"
curl -sS -X POST http://127.0.0.1:8001/admin/delay \
  -H 'Content-Type: application/json' \
  -d '{"seconds":2}'
echo
echo "[2] Failed request with shared trace ID"
curl -sS -X POST http://127.0.0.1:8000/checkout \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"order-timeout","amount":99.99}'
echo

echo
echo "[3] Remove Payment delay and verify recovery"
curl -sS -X POST http://127.0.0.1:8001/admin/delay \
  -H 'Content-Type: application/json' \
  -d '{"seconds":0}'
echo
curl -sS -X POST http://127.0.0.1:8000/checkout \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"order-recovered","amount":17.25}'
echo

echo
echo "[METRICS] Checkout"
curl -sS http://127.0.0.1:8000/metrics
echo
echo "[METRICS] Payment"
curl -sS http://127.0.0.1:8001/metrics
echo

echo
echo "[LOGS] Checkout service"
cat "$CHECKOUT_LOG"
echo
echo "[LOGS] Payment service"
cat "$PAYMENT_LOG"
