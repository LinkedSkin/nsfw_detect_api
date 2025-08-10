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
# Load credentials from project .env
# Prefer project root .env (parent of scripts/) and fall back to common locations
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_CANDIDATES=(
  "${SCRIPT_DIR}/../.env"   # project root (parent of scripts)
  "${SCRIPT_DIR}/.env"      # inside scripts (fallback)
  "${PWD}/.env"             # current working dir (last resort)
)
ENV_FILE=""
for f in "${ENV_CANDIDATES[@]}"; do
  if [[ -f "$f" ]]; then
    ENV_FILE="$f"; break
  fi
done

if [[ -n "$ENV_FILE" ]]; then
  echo "📄 Loading environment from $ENV_FILE"
  set -o allexport
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +o allexport
else
  echo "⚠️  No .env file found near this script or project root (looked in: ${ENV_CANDIDATES[*]})"
fi

# ------------------------------------------------------------------------------
# Optional: Basic auth for /netdata (read from .env if present)
# Supported keys in .env (first match wins):
#   NETDATA_BASIC_USER / NETDATA_BASIC_PASS
#   ADMIN_USER / ADMIN_PASS                 (fallback)
# ------------------------------------------------------------------------------
NETDATA_BASIC_USER="${NETDATA_BASIC_USER:-${ADMIN_USER:-}}"
NETDATA_BASIC_PASS="${NETDATA_BASIC_PASS:-${ADMIN_PASS:-}}"
HTPASSWD_FILE="/etc/nginx/.htpasswd-netdata"
NEED_BASIC_AUTH=0

if [[ -n "$NETDATA_BASIC_USER" && -n "$NETDATA_BASIC_PASS" ]]; then
  NEED_BASIC_AUTH=1
  echo "🔐 Enabling basic auth on /netdata (user: $NETDATA_BASIC_USER)"
  # Ensure htpasswd utility is available
  if ! command -v htpasswd >/dev/null 2>&1; then
    echo "📦 Installing apache2-utils for htpasswd..."
    sudo apt-get update -y
    sudo apt-get install -y apache2-utils
  fi
  # Create/update credentials file
  sudo mkdir -p "$(dirname "$HTPASSWD_FILE")"
  if [[ -f "$HTPASSWD_FILE" ]]; then
    sudo htpasswd -bB "$HTPASSWD_FILE" "$NETDATA_BASIC_USER" "$NETDATA_BASIC_PASS" >/dev/null
  else
    sudo htpasswd -c -bB "$HTPASSWD_FILE" "$NETDATA_BASIC_USER" "$NETDATA_BASIC_PASS" >/dev/null
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

    # Dedicated block for Netdata served DIRECTLY by nginx (bypass FastAPI)
    location /netdata/api/ {
$AUTH_SNIPPET
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_buffering off;
        proxy_pass http://127.0.0.1:19999/api/;
    }

    location /netdata/ {
$AUTH_SNIPPET
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Prefix /netdata;
        proxy_read_timeout 300s;
        proxy_buffering off;
        # Disable upstream compression so sub_filter can work
        proxy_set_header Accept-Encoding "";
        proxy_pass http://127.0.0.1:19999/;

        # Rewrite absolute-root references in HTML/JS so the SPA works under /netdata/
        sub_filter_once off;
        sub_filter_types text/html application/javascript application/json;
        sub_filter 'href="/' 'href="/netdata/';
        sub_filter 'src="/' 'src="/netdata/';
        sub_filter 'action="/' 'action="/netdata/';
        sub_filter '"/api/' '"/netdata/api/';
    }

    # Default proxy to FastAPI (app)
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