#!/usr/bin/env bash
# Start the marketalyzer web interface and publish it through a Cloudflare quick
# tunnel (https://<random>.trycloudflare.com). Needs cloudflared:
# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
set -euo pipefail

PORT="${PORT:-8000}"
if ! command -v cloudflared >/dev/null; then
  echo "cloudflared bulunamadı. Kurulum: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" >&2
  exit 1
fi
if [[ -z "${MARKETALYZER_TOKEN:-}" ]]; then
  MARKETALYZER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
fi
export MARKETALYZER_TOKEN

# Extra arguments go to marketalyzer-web, e.g. scripts/tunnel.sh --demo
marketalyzer-web --port "$PORT" "$@" &
WEB_PID=$!
trap 'kill "$WEB_PID" 2>/dev/null' EXIT

# Print the public address with the access token as soon as cloudflared reports it.
cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${PORT}" 2>&1 |
  while IFS= read -r line; do
    echo "$line"
    if [[ "$line" =~ (https://[a-z0-9-]+\.trycloudflare\.com) ]]; then
      echo
      echo ">>> Arayüz adresi: ${BASH_REMATCH[1]}/?token=${MARKETALYZER_TOKEN}"
      echo ">>> Bu adresi bilen herkes arayüze erişebilir; paylaşmayın."
      echo
    fi
  done
