#!/usr/bin/env bash
# One-shot provisioner for Ubuntu 24.04 on EC2 t3.micro. Run as ubuntu user.
# Usage: DOMAIN=agentrx.duckdns.org REPO=<public-git-url> bash setup.sh
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN, e.g. agentrx.duckdns.org}"
REPO="${REPO:?set REPO to the public agentRx git URL}"
APP_DIR=/opt/agentrx

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git debian-keyring debian-archive-keyring apt-transport-https curl

# Caddy (official repo, auto-TLS)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -y
sudo apt-get install -y caddy

# Service user + app
sudo useradd -r -m -d /opt/agentrx-home agentrx 2>/dev/null || true
sudo mkdir -p "$APP_DIR"
sudo chown agentrx:agentrx "$APP_DIR"
sudo -u agentrx git clone "$REPO" "$APP_DIR" 2>/dev/null || (cd "$APP_DIR" && sudo -u agentrx git pull --ff-only)
sudo -u agentrx python3 -m venv "$APP_DIR/.venv"
sudo -u agentrx "$APP_DIR/.venv/bin/pip install -r $APP_DIR/requirements.txt"

# Secrets: copy your local .env up separately (never commit it):
#   scp .env ubuntu@<host>:/tmp/agentrx.env && ssh ubuntu@<host> "sudo install -o agentrx -g agentrx -m 600 /tmp/agentrx.env /opt/agentrx/.env"
# Ensure AGENTRX_DB_PATH is absolute, e.g. AGENTRX_DB_PATH=/opt/agentrx/agentrx.db

# systemd + Caddy (bake the domain in; Caddy env placeholders don't see our shell)
sudo cp "$APP_DIR/infra/ec2/agentrx.service" /etc/systemd/system/agentrx.service
sudo sed "s/agentrx\.duckdns\.org/$DOMAIN/" "$APP_DIR/infra/ec2/Caddyfile" > /tmp/Caddyfile
sudo install -o root -g root -m 644 /tmp/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now agentrx caddy

# Daily sweeper (local cron; logs to app dir)
( sudo -u agentrx crontab -l 2>/dev/null; echo "0 9 * * * /opt/agentrx/.venv/bin/python /opt/agentrx/scripts/sweeper.py >> /opt/agentrx/sweeper.log 2>&1" ) | sudo -u agentrx crontab -

echo "--- status ---"
systemctl is-active agentrx caddy
curl -sf http://127.0.0.1:8000/api/config && echo " (local API OK)"
echo "Public URL (after DNS propagates): https://$DOMAIN/"
