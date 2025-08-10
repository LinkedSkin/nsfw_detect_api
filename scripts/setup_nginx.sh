#!/bin/bash
set -euo pipefail

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

# ------------------------------------------------------------------------------
# Load credentials from project .env (same file your app uses)
# We resolve it relative to this script's directory to avoid PWD issues.
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "$ENV_FILE" ]]; then
  echo "📄 Loading environment from $ENV_FILE"
  set -o allexport
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +o allexport
else
  echo "⚠️  No .env file found at $ENV_FILE (continuing without env-based auth vars)"
fi

# ------------------------------------------------------------------------------
# Optional: Basic auth for /netdata (read from .env if present)
# Expected keys in .env:
#   NETDATA_BASIC_USER=monitor
#   NETDATA_BASIC_PASS=supersecret
# ------------------------------------------------------------------------------
ADMIN_USER="${ADMIN_USER:-}"
ADMIN_PASS="${ADMIN_PASS:-}"
HTPASSWD_FILE="/etc/nginx/.htpasswd-netdata"
NEED_BASIC_AUTH=0

if [[ -n "$ADMIN_USER" && -n "$ADMIN_PASS" ]]; then
  NEED_BASIC_AUTH=1
  echo "🔐 Enabling basic auth on /netdata (user: $ADMIN_USER)"
  # Ensure htpasswd utility is available
  if ! command -v htpasswd >/dev/null 2>&1; then
    echo "📦 Installing apache2-utils for htpasswd..."
    sudo apt-get update -y
    sudo apt-get install -y apache2-utils
  fi
  # Create/update credentials file
  sudo mkdir -p "$(dirname "$HTPASSWD_FILE")"
  if [[ -f "$HTPASSWD_FILE" ]]; then
    sudo htpasswd -bB "$HTPASSWD_FILE" "$ADMIN_USER" "$ADMIN_PASS" >/dev/null
  else
    sudo htpasswd -c -bB "$HTPASSWD_FILE" "$ADMIN_USER" "$ADMIN_PASS" >/dev/null
  fi
fi

# Build auth snippet if requested
AUTH_SNIPPET=""
if [[ "$NEED_BASIC_AUTH" -eq 1 ]]; then
  AUTH_SNIPPET=$(cat <<'SNIP'
        auth_basic "Restricted - Netdata";
        auth_basic_user_file /etc/nginx/.htpasswd-netdata;
SNIP
)
fi

echo "📄 Writing HTTP (port 80) vhost for $DOMAIN ..."
sudo tee "$CONF_PATH" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN $ALT_DOMAIN;

    # allow larger uploads to the upstream
    client_max_body_size 25M;

    # Dedicated block for Netdata (FastAPI-proxied path)
    location /netdata/ {
$AUTH_SNIPPET
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_pass http://127.0.0.1:$PORT/netdata/;
    }

    # Default proxy to FastAPI
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Clean up bad symlink situations and disable default
if [[ -L "$NGINX_ENAB/sites-available" ]]; then
  echo "⚠️  Removing erroneous directory symlink: $NGINX_ENAB/sites-available"
  sudo rm -f "$NGINX_ENAB/sites-available"
fi

if [[ -e "$NGINX_ENAB/default" ]]; then
  echo "🔧 Disabling default site"
  sudo rm -f "$NGINX_ENAB/default"
fi

# Link only this site's config
echo "🔗 Linking $CONF_PATH into sites-enabled ..."
sudo ln -sf "$CONF_PATH" "$NGINX_ENAB/$DOMAIN"

# Test & reload HTTP config first
echo "🔄 Testing and reloading NGINX (HTTP only) ..."
sudo nginx -t && sudo systemctl reload nginx

# Obtain/Install certificate and enable redirect to HTTPS
# (Certbot will create the 443 server block and add the redirect.)
echo "🔐 Requesting/Installing TLS certificate with Certbot ..."
sudo certbot --nginx -d "$DOMAIN" -d "$ALT_DOMAIN" --redirect || {
  echo "❗ Certbot encountered an issue. Check the output above; you can re-run this script after fixing it.";
}

# Final test & reload
echo "🔁 Final NGINX test and reload ..."
sudo nginx -t && sudo systemctl reload nginx

echo "✅ NGINX and Certbot setup complete for $DOMAIN"