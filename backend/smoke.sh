#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
OUTCOME="${OUTCOME:-YES}"
SMOKE_PRICE_MICROS="${SMOKE_PRICE_MICROS:-500000}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd jq

echo "==> Checking API and DB health"
ROOT_STATUS="$(curl -fsS "$BASE_URL/" | jq -r '.status // empty')"
if [[ "$ROOT_STATUS" != "ok" ]]; then
  echo "root health check failed: status=$ROOT_STATUS" >&2
  exit 1
fi
DB_STATUS="$(curl -fsS "$BASE_URL/db-check" | jq -r '.db // empty')"
if [[ "$DB_STATUS" != "1" ]]; then
  echo "db health check failed: db=$DB_STATUS" >&2
  exit 1
fi

STAMP="$(date +%s)"
EMAIL="smoke+$STAMP@example.com"
PASSWORD="1234"

echo "==> Registering smoke user: $EMAIL"
REGISTER_RESP="$(curl -fsS -X POST "$BASE_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")"
TOKEN="$(echo "$REGISTER_RESP" | jq -r '.access_token // empty')"
if [[ -z "$TOKEN" ]]; then
  echo "register failed: missing access_token" >&2
  echo "$REGISTER_RESP" >&2
  exit 1
fi
AUTH_HEADER="Authorization: Bearer $TOKEN"

echo "==> Fetching a market"
MARKETS_RESP="$(curl -fsS "$BASE_URL/api/markets?limit=1")"
MARKET_ID="$(echo "$MARKETS_RESP" | jq -r '.markets[0].id // empty')"
if [[ -z "$MARKET_ID" ]]; then
  echo "no markets available from /api/markets" >&2
  echo "$MARKETS_RESP" >&2
  exit 1
fi

echo "==> Checking order book for market=$MARKET_ID outcome=$OUTCOME"
BOOK_RESP="$(curl -fsS "$BASE_URL/api/markets/$MARKET_ID/book?outcome=$OUTCOME&depth=5")"
ASK_PRICE="$(echo "$BOOK_RESP" | jq -r '.asks[0].price_micros // empty')"

if [[ -n "$ASK_PRICE" ]]; then
  PRICE_MICROS="$ASK_PRICE"
  echo "using top ask price: $PRICE_MICROS"
else
  PRICE_MICROS="$SMOKE_PRICE_MICROS"
  echo "no asks available, using fallback price: $PRICE_MICROS"
fi

echo "==> Creating order"
ORDER_RESP="$(curl -fsS -X POST "$BASE_URL/api/orders" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{\"market_id\":\"$MARKET_ID\",\"outcome\":\"$OUTCOME\",\"side\":\"BUY\",\"price_micros\":$PRICE_MICROS,\"qty\":1}")"
ORDER_ID="$(echo "$ORDER_RESP" | jq -r '.id // empty')"
ORDER_STATUS="$(echo "$ORDER_RESP" | jq -r '.status // empty')"
if [[ -z "$ORDER_ID" ]]; then
  echo "order creation failed: missing id" >&2
  echo "$ORDER_RESP" >&2
  exit 1
fi
echo "created order id=$ORDER_ID status=$ORDER_STATUS"

echo "==> Verifying order is visible in /api/me/orders"
MY_ORDERS_RESP="$(curl -fsS -H "$AUTH_HEADER" "$BASE_URL/api/me/orders")"
if ! echo "$MY_ORDERS_RESP" | jq -e --arg oid "$ORDER_ID" '.orders[] | select(.id == $oid)' >/dev/null; then
  echo "order $ORDER_ID not found in /api/me/orders" >&2
  echo "$MY_ORDERS_RESP" >&2
  exit 1
fi

if [[ "$ORDER_STATUS" == "OPEN" || "$ORDER_STATUS" == "PARTIAL" ]]; then
  echo "==> Canceling open/partial order"
  CANCEL_RESP="$(curl -fsS -X POST "$BASE_URL/api/orders/$ORDER_ID/cancel" -H "$AUTH_HEADER")"
  CANCEL_OK="$(echo "$CANCEL_RESP" | jq -r '.ok // empty')"
  if [[ "$CANCEL_OK" != "true" ]]; then
    echo "cancel failed for order $ORDER_ID" >&2
    echo "$CANCEL_RESP" >&2
    exit 1
  fi
fi

echo "==> Checking balance and position endpoints"
BALANCE_RESP="$(curl -fsS -H "$AUTH_HEADER" "$BASE_URL/api/me/balance")"
if ! echo "$BALANCE_RESP" | jq -e '.balance_cents | numbers' >/dev/null; then
  echo "invalid /api/me/balance response" >&2
  echo "$BALANCE_RESP" >&2
  exit 1
fi

POSITION_RESP="$(curl -fsS -H "$AUTH_HEADER" "$BASE_URL/api/markets/$MARKET_ID/position")"
POSITION_MARKET_ID="$(echo "$POSITION_RESP" | jq -r '.market_id // empty')"
if [[ "$POSITION_MARKET_ID" != "$MARKET_ID" ]]; then
  echo "invalid /api/markets/{id}/position response" >&2
  echo "$POSITION_RESP" >&2
  exit 1
fi

echo "✅ Smoke test passed"
echo "user=$EMAIL market_id=$MARKET_ID order_id=$ORDER_ID"
