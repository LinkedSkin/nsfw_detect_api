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

echo "📄 Writing HTTP (port 80) vhost for $DOMAIN ..."
sudo tee "$CONF_PATH" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN $ALT_DOMAIN;

    # allow larger uploads to the upstream
    client_max_body_size 25M;

    # basic proxy to FastAPI
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