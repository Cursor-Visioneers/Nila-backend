#!/usr/bin/env bash
# After tunnel is running and NILA_PUBLIC_BASE_URL is set, register Bey external LLM.
set -euo pipefail
API="${NILA_API:-http://127.0.0.1:8000}"
PUBLIC="${1:-}"

if [[ -n "$PUBLIC" ]]; then
  BODY=$(printf '{"public_base_url":"%s"}' "${PUBLIC%/}")
  curl -sS -X POST "$API/api/avatar/setup" -H "Content-Type: application/json" -d "$BODY" | python3 -m json.tool
else
  curl -sS -X POST "$API/api/avatar/setup" | python3 -m json.tool
fi

echo ""
curl -sS "$API/api/avatar/live/status" | python3 -m json.tool
