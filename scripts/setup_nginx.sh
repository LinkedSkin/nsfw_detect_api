#!/bin/bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Usage: sudo scripts/setup_nginx.sh example.com
# This will:
#  - Load creds from project .env
#  - Create/Update /etc/nginx/.htpasswd-netdata using NETDATA_BASIC_* or ADMIN_*
#  - Create /etc/nginx/snippets/netdata_locations.conf with /netdata locations
#  - Write HTTP vhost that includes the snippet
#  - Run Certbot (force reinstall/renew) to create/update the HTTPS vhost
#  - Patch the HTTPS vhost to also include the snippet
#  - Test, reload, and restart nginx
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
SNIPPETS_DIR="/etc/nginx/snippets"
SNIPPET_PATH="$SNIPPETS_DIR/netdata_locations.conf"
HTPASSWD_FILE="/etc/nginx/.htpasswd-netdata"

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
# Write minimal HTTP vhost that includes the snippet
# Certbot will add HTTPS; we will then ensure the snippet is included there too.
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

# ------------------------------------------------------------------------------
# Enable site, test, reload/restart
# ------------------------------------------------------------------------------
ln -sf "$CONF_PATH" "$NGINX_ENAB/$DOMAIN"

echo "🔄 Testing NGINX config ..."; nginx -t
echo "🔁 Reloading NGINX ..."; systemctl reload nginx || true
echo "🔁 Restarting NGINX ..."; systemctl restart nginx

# ------------------------------------------------------------------------------
# Certbot: always (re)install + renew; then ensure HTTPS vhost includes snippet
# ------------------------------------------------------------------------------
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@$DOMAIN}"
echo "🔐 Running Certbot (force reinstall/renew) ..."
certbot --nginx \
  -d "$DOMAIN" -d "$ALT_DOMAIN" \
  --agree-tos -m "$CERTBOT_EMAIL" --non-interactive \
  --redirect --reinstall --force-renewal || {
  echo "❗ Certbot encountered an issue. You can re-run this script after fixing it.";
}

# After Certbot has created/updated the 443 server block in $CONF_PATH,
# ensure it also includes our Netdata snippet (idempotent patch).
if ! awk '
  $0 ~ /server_name/ && $0 ~ /'"$DOMAIN"'/ { in_server=1 }
  in_server && $0 ~ /listen[^;]*443/ { in_tls=1 }
  in_server && $0 ~ /include[[:space:]]+'"$SNIPPET_PATH"'/ { has_include=1 }
  in_server && $0 ~ /}/ { if (in_tls && !has_include) exit 2; in_server=0; in_tls=0; has_include=0 }
  END { if (in_tls && !has_include) exit 2 }
' "$CONF_PATH"; then
  echo "🧩 Injecting snippet into 443 server block in $CONF_PATH"
  # Insert the include line right after the server_name line inside the 443 block
  # This sed is careful to only modify the 443 server that matches our domain.
  sed -i -E '
    /server[[:space:]]*\\{/{
      :srv
      N
      /\\}/!b srv
    }
  ' "$CONF_PATH"  # (no-op grouping to keep sed portable)

  # Simpler targeted insert: add include after the first occurrence of server_name within a 443 block
  awk -v domain="$DOMAIN" -v snippet="$SNIPPET_PATH" '
    BEGIN{in=0;tls=0;done=0}
    /server[[:space:]]*\\{/ {blk=blk $0 ORS; in=1; tls=0; next}
    in && /listen[^;]*443/ {tls=1}
    in && tls && !done && $0 ~ ("server_name[[:space:]].*" domain) {
      print blk $0 ORS "    include " snippet ";"
      blk=""; in=0; tls=0; done=1; next
    }
    in {blk=blk $0 ORS; if ($0 ~ /}/) {print blk; blk=""; in=0; tls=0; next}}
    !in {print}
  ' "$CONF_PATH" > "$CONF_PATH.tmp" && mv "$CONF_PATH.tmp" "$CONF_PATH"
fi

echo "🔄 Final NGINX test ..."; nginx -t
echo "🔁 Final NGINX restart ..."; systemctl restart nginx

echo "✅ NGINX + Certbot configured for $DOMAIN"
echo "ℹ️  Netdata is proxied at: https://$DOMAIN/netdata/  (and http://$DOMAIN/netdata/)"
if [[ $HAVE_AUTH -eq 1 ]]; then
  echo "🔒 Basic Auth enabled (user: $NETDATA_BASIC_USER)"
else
  echo "⚠️  Basic Auth is NOT enabled — set NETDATA_BASIC_USER/NETDATA_BASIC_PASS (or ADMIN_* fallback) in .env"
fi