# agentRx on EC2 (free tier) — step by step

Target: one `t3.micro`, Ubuntu 24.04, `us-east-1`. $0 for 12 months, then ~$7–9/mo.
Deploys `server.py` + `web/` only (Streamlit stays local). SQLite just works — single box, EBS disk.

## 1. Launch

- AMI: Ubuntu Server 24.04 LTS, instance type `t3.micro`, same key pair you use elsewhere.
- Security group inbound: `22` (your IP only), `80` + `443` (everyone — Caddy needs 80 for ACME).
- Allocate an **Elastic IP** and associate it (free while attached to a running instance).

## 2. DNS (required — GPS needs https, browsers block geolocation on `http://<ip>`)

- Free: DuckDNS → create e.g. `agentrx.duckdns.org` → point A record at the Elastic IP.
- Wait ~2 min, verify: `dig +short agentrx.duckdns.org` returns the Elastic IP.

## 3. Provision (one command)

From this repo root on your laptop:

```bash
DOMAIN=agentrx.duckdns.org REPO=<your-public-agentrx-git-url> \
  ssh ubuntu@<elastic-ip> 'bash -s' < infra/ec2/setup.sh
```

What it does: installs Python/Caddy/git, creates `agentrx` service user, clones to
`/opt/agentrx`, builds `.venv`, installs `requirements.txt`, installs the systemd
unit + Caddyfile, enables services, adds the 09:00 daily sweeper cron.

## 4. Secrets (separate step, never in git)

```bash
scp .env ubuntu@<elastic-ip>:/tmp/agentrx.env
ssh ubuntu@<elastic-ip> "sudo install -o agentrx -g agentrx -m 600 /tmp/agentrx.env /opt/agentrx/.env && sudo systemctl restart agentrx"
```

Check `.env` has `AGENTRX_DB_PATH=/opt/agentrx/agentrx.db` (absolute path — systemd has no repo-relative cwd surprises).

## 5. Verify

```bash
ssh ubuntu@<elastic-ip> "systemctl is-active agentrx caddy && curl -sf http://127.0.0.1:8000/api/config"
curl -sk https://agentrx.duckdns.org/api/config   # keyed CARTO config, no watermark
```

Then open `https://agentrx.duckdns.org/` on your phone: grant Location, legend should read
`you · GPS`. That URL is your Devpost **live demo link**.

## 6. Operate

- Logs: `journalctl -u agentrx -f`; sweeper log: `/opt/agentrx/sweeper.log`
- Update: `cd /opt/agentrx && sudo -u agentrx git pull --ff-only && sudo systemctl restart agentrx`
- UFW (optional belt-and-braces): `sudo ufw allow 22,80,443/tcp && sudo ufw enable`
- Billing alarm: AWS Console → Budgets → $5 actual-spend alarm (Bedrock/Places are pennies, this is just hygiene).

## What we deliberately skipped

API Gateway, ALB, Secrets Manager ($0.40/secret/mo), multi-AZ, Streamlit on the box.
If traffic ever outgrows one micro, the documented next step is Lambda + DynamoDB + Mangum.
