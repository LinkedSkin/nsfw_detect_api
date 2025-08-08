#!/bin/bash
set -e

# Update system and install dependencies
echo "🔧 Installing system packages..."
sudo apt update
sudo apt install -y build-essential curl git libssl-dev zlib1g-dev \
  libbz2-dev libreadline-dev libsqlite3-dev wget llvm \
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev \
  liblzma-dev nginx certbot python3-certbot-nginx

#
# Install pyenv
if [ ! -d "$HOME/.pyenv" ]; then
  echo "📦 Installing pyenv..."
  curl https://pyenv.run | bash
else
  echo "✔ pyenv already installed"
fi

# Add pyenv init to all relevant shell profiles if not already present
PYENV_INIT_LINES='
# Pyenv initialization
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv 1>/dev/null 2>&1; then
  # For login shells
  if [[ -n "$BASH_VERSION" || -n "$ZSH_VERSION" ]]; then
    eval "$(pyenv init --path)"
  fi
  # For interactive shells
  if [[ $- == *i* ]]; then
    eval "$(pyenv init -)"
    if command -v pyenv-virtualenv-init 1>/dev/null 2>&1; then
      eval "$(pyenv virtualenv-init -)"
    fi
  fi
fi
'
for profile in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile" "$HOME/.bash_profile"; do
  if [ -f "$profile" ]; then
    if ! grep -q 'pyenv initialization' "$profile"; then
      printf "\n%s\n" "$PYENV_INIT_LINES" >> "$profile"
    fi
  else
    printf "\n%s\n" "$PYENV_INIT_LINES" >> "$profile"
  fi
done

# Setup pyenv environment (for current script)
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"
if command -v pyenv-virtualenv-init 1>/dev/null 2>&1; then
  eval "$(pyenv virtualenv-init -)"
fi

# Install Python 3.13.0
PYTHON_VERSION="3.13.0"
if ! pyenv versions --bare | grep -q "^$PYTHON_VERSION\$"; then
  echo "🐍 Installing Python $PYTHON_VERSION..."
  pyenv install "$PYTHON_VERSION"
fi

pyenv global "$PYTHON_VERSION"

# Install PDM
echo "📥 Installing PDM..."
curl -sSL https://pdm-project.org/install-pdm.py | python3 -

# Ensure ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "✅ Bootstrap complete. Restart your shell or run: source ~/.bashrc"