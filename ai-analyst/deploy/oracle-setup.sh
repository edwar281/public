#!/usr/bin/env bash
# One-time setup on a fresh Oracle Cloud Always Free Ubuntu VM (Ampere A1, arm64).
# Usage: curl -fsSL https://raw.githubusercontent.com/edwar281/public/main/ai-analyst/deploy/oracle-setup.sh | bash
set -euo pipefail

echo "== Installing Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

echo "== Opening ports 80 and 443 in the VM firewall"
# Oracle's Ubuntu images ship iptables rules that reject everything except SSH.
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$port" -j ACCEPT
  fi
done
sudo apt-get install -y -q iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null 2>&1 || true

echo "== Getting the code"
if [ ! -d "$HOME/public" ]; then
  git clone --depth 1 https://github.com/edwar281/public "$HOME/public"
else
  git -C "$HOME/public" pull --ff-only
fi
cd "$HOME/public/ai-analyst"
[ -f .env ] || cp .env.example .env

cat <<'EOF'

== Almost done
1. Edit the settings:      nano ~/public/ai-analyst/.env
   Set DOMAIN and GEMINI_API_KEY.
2. Log out and back in once (so Docker works without sudo), then start it:
   cd ~/public/ai-analyst && docker compose up -d --build
3. Save the example answers:
   docker compose exec api python -m api.app.warm
EOF
