#!/bin/bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Usage: sudo scripts/setup_nginx.sh example.com
# This will:
#  - Load creds from project .env
#  - Create/Update /etc/nginx/.htpasswd-netdata using NETDATA_BASIC_* or ADMIN_*
#  - Create /etc/nginx/snippets/netdata_locations.conf with /netdata locations
#  - Write BOTH HTTP (80) and HTTPS (443) vhosts that include the snippet
#  - Obtain/renew certs with Certbot (certonly, no nginx edits)
#  - Test, reload, and restart nginx
# ------------------------------------------------------------------------------

# Require root (sudo)
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "❌ Please run as root (use: sudo $0 <domain>)" >&2
  exit 1
fi

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
SNIPPETS_DIR="/etc/nginx/snippets"
SNIPPET_PATH="$SNIPPETS_DIR/netdata_locations.conf"
HTPASSWD_FILE="/etc/nginx/.htpasswd-netdata"
LE_LIVE_DIR="/etc/letsencrypt/live/$DOMAIN"
FULLCHAIN="$LE_LIVE_DIR/fullchain.pem"
PRIVKEY="$LE_LIVE_DIR/privkey.pem"

# ------------------------------------------------------------------------------
# Load environment from project .env (prefer project root ../.env)
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
HAVE_AUTH=0
if [[ -n "${NETDATA_BASIC_USER}" && -n "${NETDATA_BASIC_PASS}" ]]; then
  HAVE_AUTH=1
  echo "🔐 Will enable Basic Auth on /netdata (user: $NETDATA_BASIC_USER)"
else
  echo "⚠️  No NETDATA_BASIC_* or ADMIN_* credentials found; /netdata/ will be PUBLIC."
fi

# ------------------------------------------------------------------------------
# Ensure htpasswd exists if we have creds
# ------------------------------------------------------------------------------
if [[ $HAVE_AUTH -eq 1 ]]; then
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
fi

# ------------------------------------------------------------------------------
# Write snippet with /netdata locations (reusable in both HTTP and HTTPS servers)
# ------------------------------------------------------------------------------
mkdir -p "$SNIPPETS_DIR"

if [[ $HAVE_AUTH -eq 1 ]]; then
  AUTH_LINES='
        # Basic Auth for Netdata (credentials stored in /etc/nginx/.htpasswd-netdata)
        auth_basic "Restricted - Netdata";
        auth_basic_user_file /etc/nginx/.htpasswd-netdata;
'
else
  AUTH_LINES='
        # (No Basic Auth configured for /netdata/ — set NETDATA_BASIC_* or ADMIN_* in .env)
'
fi

echo "🧩 Writing snippet: $SNIPPET_PATH"
tee "$SNIPPET_PATH" >/dev/null <<SNIP
# ----------------------------------------------------------------------
# Netdata locations snippet — included by both HTTP and HTTPS vhosts
# Protects /netdata/ (optional Basic Auth) and proxies directly to agent.
# The trailing slash on proxy_pass strips the /netdata/ prefix automatically.
# ----------------------------------------------------------------------

# Redirect '/netdata' (no trailing slash) → '/netdata/'
location = /netdata { return 301 /netdata/; }

# Everything under /netdata/ is proxied to Netdata (prefix stripped)
location /netdata/ {
$AUTH_LINES
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
SNIP

# ------------------------------------------------------------------------------
# Obtain/renew certs WITHOUT letting certbot edit nginx configs
# (we manage the vhost ourselves; certbot only gets/renews certs)
# ------------------------------------------------------------------------------
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@$DOMAIN}"
if [[ ! -f "$FULLCHAIN" || ! -f "$PRIVKEY" ]]; then
  echo "🔐 Obtaining certificate with Certbot (certonly) ..."
  certbot certonly --nginx \
    -d "$DOMAIN" -d "$ALT_DOMAIN" \
    --agree-tos -m "$CERTBOT_EMAIL" --non-interactive || {
    echo "❗ Certbot encountered an issue obtaining certs.";
  }
else
  echo "🔐 Renewing certificate with Certbot (force-renewal) ..."
  certbot renew --force-renewal --cert-name "$DOMAIN" || true
fi

# If certs still missing, continue with HTTP-only config (will still work)
if [[ ! -f "$FULLCHAIN" || ! -f "$PRIVKEY" ]]; then
  echo "⚠️  TLS certs not found at $LE_LIVE_DIR — continuing with HTTP; rerun after certs exist."
fi

# ------------------------------------------------------------------------------
# Write BOTH HTTP and HTTPS vhosts; include the snippet in BOTH.
# ------------------------------------------------------------------------------
mkdir -p "$NGINX_AVAIL" "$NGINX_ENAB"

echo "📄 Writing vhosts for $DOMAIN ..."
tee "$CONF_PATH" >/dev/null <<EOF
# =====================  HTTP vhost (80)  =====================
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN $ALT_DOMAIN;

    # Redirect everything to HTTPS
    return 301 https://\$host\$request_uri;
}

# =====================  HTTPS vhost (443)  =====================
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN $ALT_DOMAIN;

$(if [[ -f "$FULLCHAIN" && -f "$PRIVKEY" ]]; then cat <<SSL
    # TLS certificates
    ssl_certificate $FULLCHAIN;
    ssl_certificate_key $PRIVKEY;
SSL
fi)

    # Allow larger uploads to your FastAPI app (tune if needed)
    client_max_body_size 25M;

    # --- Netdata (served directly via nginx) ---
    # Included locations live in: $SNIPPET_PATH
    include $SNIPPET_PATH;

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

# Disable default site to prevent conflicts
if [[ -e "/etc/nginx/sites-enabled/default" ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi

# Enable site, test, reload/restart
ln -sf "$CONF_PATH" "$NGINX_ENAB/$DOMAIN"

echo "🔄 Testing NGINX config ..."; nginx -t

echo "🔁 Reloading NGINX ..."; systemctl reload nginx || true

echo "🔁 Restarting NGINX ..."; systemctl restart nginx

echo "✅ NGINX configured for $DOMAIN"
echo "ℹ️  Netdata is proxied at: https://$DOMAIN/netdata/  (and http://$DOMAIN/netdata/)"
if [[ $HAVE_AUTH -eq 1 ]]; then
  echo "🔒 Basic Auth enabled (user: $NETDATA_BASIC_USER)"
else
  echo "⚠️  Basic Auth is NOT enabled — set NETDATA_BASIC_USER/NETDATA_BASIC_PASS (or ADMIN_* fallback) in .env"
fi