#!/usr/bin/env bash
# Expose local :8000 so Beyond Presence can call /api/avatar/openai/v1/...
# Usage:
#   ./scripts/start-public-tunnel.sh          # Cloudflare (no account)
#   ./scripts/start-public-tunnel.sh ngrok    # requires: ngrok config add-authtoken ...

set -euo pipefail
PORT="${PORT:-8000}"
MODE="${1:-cloudflare}"

case "$MODE" in
  ngrok)
    NGROK="${NGROK_BIN:-ngrok}"
    if ! command -v "$NGROK" >/dev/null 2>&1; then
      echo "Install ngrok: brew install ngrok/ngrok/ngrok && ngrok config add-authtoken YOUR_TOKEN"
      exit 1
    fi
    echo "Starting ngrok on port $PORT (keep this terminal open)..."
    echo "Then set NILA_PUBLIC_BASE_URL to the https URL shown."
    exec "$NGROK" http "$PORT"
    ;;
  cloudflare|cf)
    CF="${CLOUDFLARED_BIN:-cloudflared}"
    if ! command -v "$CF" >/dev/null 2>&1; then
      CF="/tmp/cloudflared"
      if [[ ! -x "$CF" ]]; then
        echo "Downloading cloudflared..."
        ARCH="$(uname -m)"
        [[ "$ARCH" == "arm64" ]] && ARCH="arm64" || ARCH="amd64"
        curl -sL -o /tmp/cf.tgz "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-${ARCH}.tgz"
        tar -xzf /tmp/cf.tgz -C /tmp
      fi
    fi
    echo "Starting Cloudflare quick tunnel → http://127.0.0.1:$PORT"
    echo "Copy the https://....trycloudflare.com URL into NILA_PUBLIC_BASE_URL"
    exec "$CF" tunnel --url "http://127.0.0.1:$PORT"
    ;;
  *)
    echo "Usage: $0 [cloudflare|ngrok]"
    exit 1
    ;;
esac
