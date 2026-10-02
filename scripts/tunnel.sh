#!/usr/bin/env bash
# Start the marketalyzer web interface and publish it through Cloudflare.
#
# Fixed address without a domain (the interface on Netlify, the server here):
#   marketalyzer-netlify setup --token <Netlify personal access token>   # once
#   scripts/tunnel.sh
# Every start then opens a quick tunnel, writes its new address to the Netlify
# site and prints https://marketalyzer.netlify.app/?token=..., which stays the same.
#
# Fixed address (needs a free Cloudflare account and a domain on Cloudflare):
#   cloudflared tunnel login                       # once: pick the domain in the browser
#   scripts/tunnel.sh --hostname bist.alanadiniz.com
# The first run creates the tunnel and its DNS record and remembers the hostname,
# so later runs need no arguments: scripts/tunnel.sh
# Tunnel made in the Cloudflare dashboard instead: set CLOUDFLARE_TUNNEL_TOKEN (and
# give --hostname to have the address printed).
# Without a domain, --quick gives a new random https://*.trycloudflare.com address
# on every run. --forget drops the remembered hostname. --name sets the tunnel name.
# The access token is kept between runs; "marketalyzer-web --new-token" replaces it.
# Other arguments go to marketalyzer-web, e.g. scripts/tunnel.sh --demo
set -euo pipefail

PORT="${PORT:-8000}"
HOST_NAME=""
TUNNEL_NAME="marketalyzer"
NAME_GIVEN=0
QUICK=0
WEB_ARGS=()
while (($#)); do
  case "$1" in
    --hostname) HOST_NAME="$2"; shift 2 ;;
    --name) TUNNEL_NAME="$2"; NAME_GIVEN=1; shift 2 ;;
    --quick) QUICK=1; shift ;;
    --forget) FORGET=1; shift ;;
    *) WEB_ARGS+=("$1"); shift ;;
  esac
done

for tool in cloudflared marketalyzer-web; do
  if ! command -v "$tool" >/dev/null; then
    echo "$tool bulunamadı. cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/ ; marketalyzer-web için sanal ortamı etkinleştirin." >&2
    exit 1
  fi
done

DATA_DIR="${MARKETALYZER_HOME:-$HOME/.local/share/marketalyzer}"
mkdir -p "$DATA_DIR"
SAVED="$DATA_DIR/tunnel.env"
if [[ "${FORGET:-0}" == 1 && -f "$SAVED" ]]; then
  rm -f "$SAVED"
  echo "Kayıtlı tünel adresi silindi."
fi
SAVED_HOST=""
SAVED_NAME=""
if [[ -f "$SAVED" ]]; then
  SAVED_HOST="$(sed -n 's/^hostname=//p' "$SAVED")"
  SAVED_NAME="$(sed -n 's/^tunnel=//p' "$SAVED")"
fi
if [[ -z "$HOST_NAME" && "$QUICK" == 0 && -n "$SAVED_HOST" ]]; then
  HOST_NAME="$SAVED_HOST"
  if [[ "$NAME_GIVEN" == 0 && -n "$SAVED_NAME" ]]; then TUNNEL_NAME="$SAVED_NAME"; fi
fi

if [[ -z "${MARKETALYZER_TOKEN:-}" ]]; then
  MARKETALYZER_TOKEN="$(marketalyzer-web --print-token | tail -n 1)"
fi
export MARKETALYZER_TOKEN

if [[ "$QUICK" == 1 ]]; then MODE=quick
elif [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then MODE=token
elif [[ -n "$HOST_NAME" ]]; then MODE=named
else MODE=quick
fi

# With a Netlify site, the quick tunnel's address is published there on every start.
NETLIFY=""
if [[ "$MODE" == quick ]] && command -v marketalyzer-netlify >/dev/null; then
  NETLIFY="$(marketalyzer-netlify url 2>/dev/null | tail -n 1 || true)"
fi

if [[ "$MODE" == named ]]; then
  CF_DIR="$HOME/.cloudflared"
  if [[ ! -f "$CF_DIR/cert.pem" ]]; then
    echo "Sabit adres için önce bir kez Cloudflare'e giriş yapın: cloudflared tunnel login" >&2
    echo "Açılan sayfada $HOST_NAME alan adını seçin, sonra bu scripti tekrar çalıştırın." >&2
    exit 1
  fi
  tunnel_id() {
    cloudflared tunnel list --name "$TUNNEL_NAME" --output json 2>/dev/null |
      python3 -c 'import json, sys; rows = json.load(sys.stdin) or []; print(rows[0]["id"] if rows else "")'
  }
  TUNNEL_ID="$(tunnel_id)"
  if [[ -z "$TUNNEL_ID" ]]; then
    echo "'$TUNNEL_NAME' tüneli oluşturuluyor…"
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID="$(tunnel_id)"
  fi
  CREDENTIALS="$CF_DIR/$TUNNEL_ID.json"
  if [[ ! -f "$CREDENTIALS" ]]; then
    echo "Tünelin kimlik dosyası bu bilgisayarda yok: $CREDENTIALS. Farklı bir ad deneyin: --name marketalyzer-2" >&2
    exit 1
  fi
  if [[ "$SAVED_HOST" != "$HOST_NAME" || "$SAVED_NAME" != "$TUNNEL_NAME" ]]; then
    echo "DNS kaydı ekleniyor: $HOST_NAME -> $TUNNEL_NAME"
    cloudflared tunnel route dns "$TUNNEL_NAME" "$HOST_NAME" ||
      echo "Uyarı: DNS kaydı eklenemedi. Kayıt başka bir hedefi gösteriyorsa Cloudflare panelinden silin." >&2
    printf 'hostname=%s\ntunnel=%s\n' "$HOST_NAME" "$TUNNEL_NAME" > "$SAVED"
  fi
  CONFIG="$DATA_DIR/cloudflared-$TUNNEL_NAME.yml"
  cat > "$CONFIG" <<YAML
tunnel: $TUNNEL_ID
credentials-file: '$CREDENTIALS'
ingress:
  - hostname: $HOST_NAME
    service: http://127.0.0.1:$PORT
  - service: http_status:404
YAML
  CLOUDFLARED=(cloudflared tunnel --no-autoupdate --config "$CONFIG" run "$TUNNEL_NAME")
elif [[ "$MODE" == token ]]; then
  CLOUDFLARED=(cloudflared tunnel --no-autoupdate run --token "$CLOUDFLARE_TUNNEL_TOKEN")
else
  CLOUDFLARED=(cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${PORT}")
fi

marketalyzer-web --port "$PORT" ${WEB_ARGS[@]+"${WEB_ARGS[@]}"} &
WEB_PID=$!
trap 'kill "$WEB_PID" 2>/dev/null' EXIT

show() {
  echo
  echo ">>> Arayüz adresi: $1/?token=${MARKETALYZER_TOKEN}"
  echo ">>> Bu adresi bilen herkes arayüze erişebilir; paylaşmayın."
  echo
}

# cloudflared logs to stderr; print every line and the address once it is known.
SHOWN=0
"${CLOUDFLARED[@]}" 2>&1 |
  while IFS= read -r line; do
    echo "$line"
    [[ "$SHOWN" == 1 ]] && continue
    if [[ "$MODE" == quick && "$line" =~ (https://[a-z0-9-]+\.trycloudflare\.com) ]]; then
      TUNNEL_URL="${BASH_REMATCH[1]}"
      SHOWN=1
      if [[ -n "$NETLIFY" ]]; then
        echo "Netlify'a yeni tünel adresi yazılıyor…"
        if marketalyzer-netlify deploy --backend "$TUNNEL_URL"; then
          show "$NETLIFY"
          echo ">>> Bu adres hiç değişmez; telefona uygulama olarak kurabilirsiniz."
          continue
        fi
        echo "Netlify güncellenemedi; geçici adres kullanılıyor."
      fi
      show "$TUNNEL_URL"
      echo ">>> Bu adres her başlatmada değişir. Sabit adres için: marketalyzer-netlify setup --token <anahtar>"
    elif [[ "$MODE" != quick && "$line" == *"Registered tunnel connection"* ]]; then
      if [[ -n "$HOST_NAME" ]]; then show "https://$HOST_NAME"; else echo ">>> Tünel bağlandı; panelde tanımladığınız adrese /?token=${MARKETALYZER_TOKEN} ekleyin."; fi
      SHOWN=1
    fi
  done
