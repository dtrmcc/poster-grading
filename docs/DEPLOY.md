# Deploying on a VPS

Runs the whole app on one small Ubuntu server (1 vCPU / 1 GB RAM / 25 GB is enough).
AI still runs at Together; only de-identified content leaves the server.

```
Internet ──HTTPS──▶ Caddy (web)  ── login (basic auth) ──▶ built frontend
                         └── /api/* ──▶ backend (FastAPI, SQLite, ./data)   [not exposed]
```

## First-time setup

1. DNS: an **A record** for the domain (e.g. `grading.qhchen.ca`) → the server IP.
   If the domain is on Cloudflare, set it to **DNS only** (grey cloud) so traffic is
   encrypted end-to-end to your server and the certificate can be issued.
2. From the Mac, copy the script (and, optionally, your `.env` with the API key):
   ```bash
   cd ~/"Poster Grading App"
   ssh root@SERVER_IP 'mkdir -p /opt/poster-grading'
   scp deploy/setup-vps.sh root@SERVER_IP:/root/
   scp .env root@SERVER_IP:/opt/poster-grading/.env        # optional
   ```
3. Run it:
   ```bash
   ssh -t root@SERVER_IP 'bash /root/setup-vps.sh grading.qhchen.ca'
   ```
   It asks you to add a read-only **deploy key** on GitHub, then for a Let's Encrypt
   e-mail and the **username/password** of the website.

## Everyday

- Open `https://grading.qhchen.ca`, log in.
- Update after pushing new code to GitHub: `ssh root@SERVER_IP /opt/poster-grading/deploy/update.sh`
- Logs: `cd /opt/poster-grading && docker compose -f docker-compose.prod.yml logs -f --tail 100`
- Change the website password: delete `deploy/web.env` on the server and re-run `setup-vps.sh`.

## Moving existing data from the Mac (optional)

Stop the app on the Mac first, then:
```bash
ssh root@SERVER_IP 'cd /opt/poster-grading && docker compose -f docker-compose.prod.yml stop backend'
scp -r ~/"Poster Grading App"/data/* root@SERVER_IP:/opt/poster-grading/data/
ssh root@SERVER_IP 'cd /opt/poster-grading && docker compose -f docker-compose.prod.yml start backend'
```

## Backups

`deploy/backup.sh` runs nightly at 03:15 (Singapore time) once `B2_*` is filled in `.env`:
a consistent SQLite copy plus all files, AES-256 encrypted with `BACKUP_PASSPHRASE`
**before** upload; 7 local copies, 60 days on B2. Keep the passphrase in your password
manager. Restore instructions are at the top of `deploy/backup.sh`.

## Security measures

- HTTPS only (Let's Encrypt, HSTS); every page, API call and file needs the login.
- Backend not reachable from outside; firewall allows only SSH, 80, 443.
- fail2ban on SSH; automatic security updates; optional SSH-key-only login.
- Secrets (`.env`, `deploy/web.env`) are readable by root only and never committed.
