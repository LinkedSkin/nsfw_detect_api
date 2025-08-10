#!/usr/bin/env bash
set -euo pipefail

echo "🚀 Bootstrapping environment..."

# --- Install dependencies ---
echo "📦 Installing required packages..."
sudo apt update
sudo apt install -y curl git build-essential libssl-dev zlib1g-dev \
  libbz2-dev libreadline-dev libsqlite3-dev wget llvm libncursesw5-dev \
  xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

# --- Install Netdata ---
echo "📈 Installing Netdata..."

install_netdata() {
  # Use official kickstart (non-interactive, no telemetry)
  if bash <(curl -fsSL https://my-netdata.io/kickstart.sh) \
       --disable-telemetry --non-interactive --dont-wait; then
    return 0
  else
    echo "⚠️  Netdata kickstart failed; trying apt (may be older)..." >&2
    sudo apt update && sudo apt install -y netdata || return 1
  fi
}

ensure_netdata_unit() {
  # Ensure systemd unit exists
  if systemctl list-unit-files | grep -q '^netdata\.service'; then
    return 0
  fi
  # If binary exists but no unit, create minimal one (common with /opt installs)
  if [ -x /opt/netdata/usr/sbin/netdata ]; then
    sudo tee /etc/systemd/system/netdata.service >/dev/null <<'EOF'
[Unit]
Description=Real-time performance monitoring
After=network.target

[Service]
Type=simple
ExecStart=/opt/netdata/usr/sbin/netdata -D
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    return 0
  fi
  return 1
}

start_netdata() {
  sudo systemctl enable --now netdata
}

verify_netdata() {
  sleep 2
  if curl -fsS http://127.0.0.1:19999/api/v1/info >/dev/null; then
    echo "✅ Netdata OK at http://127.0.0.1:19999"
    return 0
  else
    echo "❌ Netdata not responding on 127.0.0.1:19999" >&2
    sudo systemctl status netdata --no-pager || true
    return 1
  fi
}

if ! systemctl is-active --quiet netdata 2>/dev/null; then
  if install_netdata; then
    if ensure_netdata_unit; then
      if start_netdata; then
        verify_netdata || true
      else
        echo "❌ Failed to start netdata.service" >&2
      fi
    else
      echo "❌ Netdata installed but no systemd unit found; check installer output." >&2
    fi
  else
    echo "❌ Netdata installation failed" >&2
  fi
else
  echo "✔ Netdata already installed and running"
  verify_netdata || true
fi

# --- Install pyenv ---
echo "🐍 Installing pyenv..."
if [ ! -d "$HOME/.pyenv" ]; then
  curl https://pyenv.run | bash
  {
    echo 'export PYENV_ROOT="$HOME/.pyenv"'
    echo 'export PATH="$PYENV_ROOT/bin:$PATH"'
    echo 'eval "$(pyenv init --path)"'
    echo 'eval "$(pyenv virtualenv-init -)"'
  } >> ~/.bashrc
  export PYENV_ROOT="$HOME/.pyenv"
  export PATH="$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init --path)"
fi

# --- Install Python 3.13.0 ---
PYTHON_VERSION="3.13.0"
if ! pyenv versions --bare | grep -q "^${PYTHON_VERSION}\$"; then
  pyenv install $PYTHON_VERSION
fi
pyenv global $PYTHON_VERSION

# --- Install PDM ---
echo "📦 Installing PDM..."
if ! command -v pdm >/dev/null; then
  curl -sSL https://pdm.fming.dev/install-pdm.py | python3 -
  export PATH="$HOME/.local/bin:$PATH"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
fi

echo "✅ Bootstrap complete!"