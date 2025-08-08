#!/bin/bash
set -e

DOMAIN="$1"
if [ -z "$DOMAIN" ]; then
  echo "❌ Error: No domain name provided."
  echo "Usage: $0 example.com"
  exit 1
fi

echo "📄 Creating NGINX config for $DOMAIN..."

NGINX_CONF="/etc/nginx/sites-available/$DOMAIN"
sudo tee "$NGINX_CONF" > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:6969;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

echo "🔗 Linking config into sites-enabled..."
sudo ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/

echo "🔄 Testing and reloading NGINX..."
sudo nginx -t && sudo systemctl reload nginx

echo "🔐 Requesting TLS certificate with Certbot..."
sudo certbot --nginx -d "$DOMAIN"

echo "✅ NGINX and Certbot setup complete for $DOMAIN"