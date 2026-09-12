# agentRx on GCP (always-free e2-micro) — delta vs EC2

Same app, same `setup.sh` (Ubuntu + systemd + Caddy). Only the box and firewall differ.
See `../ec2/ec2-deploy.md` for the shared steps (DNS, secrets, verify, operate).

## 1. VM

- Console → Compute Engine → Create: region `us-east1`, zone `us-east1-b`,
  machine `e2-micro` (1 vCPU, 1 GB — the always-free shape), boot disk
  `20 GB balanced` (stays under the 30 GB free cap; no snapshots — those bill),
  image Ubuntu 24.04 LTS, allow HTTP/HTTPS traffic ticked.
- Reserve + attach a static external IP (free while the VM runs).

Or via gcloud:

```bash
gcloud compute instances create agentrx \
  --zone=us-east1-b --machine-type=e2-micro \
  --image-family=ubuntu-2404-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB --boot-disk-type=pd-balanced \
  --tags=agentrx-web
gcloud compute addresses create agentrx-ip --region=us-east1
```

## 2. Firewall (VPC, not a security group)

```bash
gcloud compute firewall-rules create agentrx-web \
  --allow=tcp:80,tcp:443 --target-tags=agentrx-web
# SSH: default-allow-ssh already covers port 22; optionally narrow --source-ranges to your IP.
```

## 3. Provision + secrets + verify

Identical to EC2 doc steps 3–5, with `<elastic-ip>` → your static IP:

```bash
DOMAIN=agentrx.duckdns.org REPO=<your-public-agentrx-git-url> \
  ssh ubuntu@<static-ip> 'bash -s' < ../ec2/setup.sh
```

Then `https://agentrx.duckdns.org/` is the Devpost live-demo link.

## Free-tier guardrails

- One e2-micro, ≤30 GB disk, ≤1 GB egress/mo — plenty for a demo link.
- Billing → Budgets & alerts → $1 actual-spend alert. Egress overage is the only realistic leak.
