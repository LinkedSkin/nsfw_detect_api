#!/bin/bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Usage: sudo scripts/setup_nginx.sh example.com
# This will:
#  - Load creds from project .env
#  - Create/Update /etc/nginx/.htpasswd-netdata using NETDATA_BASIC_* or ADMIN_*
#  - Write a minimal nginx vhost that:
#      * Protects /netdata/ with Basic Auth
#      * Strips the /netdata/ prefix and proxies remainder to 127.0.0.1:19999
#      * Sends everything else to FastAPI on 127.0.0.1:6969
#  - Test, reload, and restart nginx
#  - Run certbot with --reinstall --force-renewal
# ------------------------------------------------------------------------------

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "❌ Error: No domain name provided."
  echo "Usage: $0 example.com"
  exit 1
fi
ALT_DOMAIN="www.$DOMAIN"
PORT="6969"   # FastAPI/uvicorn port

NGINX_AVAIL="/etc/nginx/sites-available"
NGINX_ENAB="/etc/nginx/sites-enabled"
CONF_PATH="$NGINX_AVAIL/$DOMAIN"
HTPASSWD_FILE="/etc/nginx/.htpasswd-netdata"

# ------------------------------------------------------------------------------
# Load environment from project .env
# Prefer project root (parent of scripts/) but fall back to a couple locations
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_CANDIDATES=(
  "${SCRIPT_DIR}/../.env"   # project root (parent of scripts)
  "${SCRIPT_DIR}/.env"      # inside scripts (fallback)
  "${PWD}/.env"             # current working dir (last resort)
)
ENV_FILE=""
for f in "${ENV_CANDIDATES[@]}"; do
  if [[ -f "$f" ]]; then ENV_FILE="$f"; break; fi
done
if [[ -n "$ENV_FILE" ]]; then
  echo "📄 Loading environment from $ENV_FILE"
  set -o allexport
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +o allexport
else
  echo "⚠️  No .env file found (looked in: ${ENV_CANDIDATES[*]})"
fi

# ------------------------------------------------------------------------------
# Choose credentials: NETDATA_BASIC_* first, then ADMIN_*
# ------------------------------------------------------------------------------
NETDATA_BASIC_USER="${NETDATA_BASIC_USER:-${ADMIN_USER:-}}"
NETDATA_BASIC_PASS="${NETDATA_BASIC_PASS:-${ADMIN_PASS:-}}"
if [[ -z "${NETDATA_BASIC_USER}" || -z "${NETDATA_BASIC_PASS}" ]]; then
  echo "⚠️  No NETDATA_BASIC_* or ADMIN_* credentials found in .env; /netdata/ will be public."
fi

# ------------------------------------------------------------------------------
# Ensure htpasswd exists if we have creds
# ------------------------------------------------------------------------------
if [[ -n "${NETDATA_BASIC_USER}" && -n "${NETDATA_BASIC_PASS}" ]]; then
  if ! command -v htpasswd >/dev/null 2>&1; then
    echo "📦 Installing apache2-utils for htpasswd..."
    apt-get update -y
    apt-get install -y apache2-utils
  fi
  mkdir -p "$(dirname "$HTPASSWD_FILE")"
  if [[ -f "$HTPASSWD_FILE" ]]; then
    htpasswd -bB "$HTPASSWD_FILE" "$NETDATA_BASIC_USER" "$NETDATA_BASIC_PASS" >/dev/null
  else
    htpasswd -c -bB "$HTPASSWD_FILE" "$NETDATA_BASIC_USER" "$NETDATA_BASIC_PASS" >/dev/null
  fi
  AUTH_SNIPPET=$(cat <<'SNIP'
        # Basic Auth for Netdata (credentials stored in /etc/nginx/.htpasswd-netdata)
        auth_basic "Restricted - Netdata";
        auth_basic_user_file /etc/nginx/.htpasswd-netdata;
SNIP
)
else
  AUTH_SNIPPET="        # (No Basic Auth configured for /netdata/ — set NETDATA_BASIC_* or ADMIN_* in .env)"
fi

# ------------------------------------------------------------------------------
# Write minimal nginx vhost (HTTP). Certbot will add HTTPS + redirect.
#  - The trailing slash on proxy_pass causes nginx to strip the matching prefix
#    (/netdata/) and pass the remainder to Netdata automatically.
#  - No sub_filter or JSON rewriting needed.
# ------------------------------------------------------------------------------
mkdir -p "$NGINX_AVAIL" "$NGINX_ENAB"

echo "📄 Writing HTTP (port 80) vhost for $DOMAIN ..."
tee "$CONF_PATH" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN $ALT_DOMAIN;

    # Allow larger uploads to your FastAPI app (tune if needed)
    client_max_body_size 25M;

    # --- Netdata (served directly via nginx) ---
    # Redirect '/netdata' (no trailing slash) → '/netdata/'
    location = /netdata { return 301 /netdata/; }

    # Everything under /netdata/ is proxied to Netdata (prefix stripped)
    location /netdata/ {
$AUTH_SNIPPET
        # Standard proxy headers + WebSocket upgrade
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Trailing slash strips '/netdata/' prefix automatically
        proxy_pass http://127.0.0.1:19999/;
    }

    # --- Everything else → FastAPI app ---
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# ------------------------------------------------------------------------------
# Enable site, test, reload, restart, Certbot (force reinstall/renew)
# ------------------------------------------------------------------------------
ln -sf "$CONF_PATH" "$NGINX_ENAB/$DOMAIN"

echo "🔄 Testing NGINX config ..."; nginx -t

echo "🔁 Reloading NGINX ..."; systemctl reload nginx || true

echo "🔁 Restarting NGINX ..."; systemctl restart nginx

CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@$DOMAIN}"
echo "🔐 Running Certbot (force reinstall/renew) ..."
certbot --nginx \
  -d "$DOMAIN" -d "$ALT_DOMAIN" \
  --agree-tos -m "$CERTBOT_EMAIL" --non-interactive \
  --redirect --reinstall --force-renewal || {
  echo "❗ Certbot encountered an issue. You can re-run this script after fixing it.";
}

# Final restart to ensure TLS picks up
systemctl restart nginx

echo "✅ NGINX + Certbot configured for $DOMAIN (Netdata at /netdata/)"