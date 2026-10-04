#!/usr/bin/env bash
# One-time setup of the grading app on a fresh Ubuntu VPS. Safe to re-run.
#
#   curl -fsSL https://raw.githubusercontent.com/... | bash      (not possible: private repo)
#   → copy this file to the server and run:  bash setup-vps.sh grading.qhchen.ca
#
# What it does: system updates, 2 GB swap, firewall (SSH/80/443 only), fail2ban,
# automatic security updates, Docker, read-only GitHub deploy key, clone the repo,
# create the login, start the app with HTTPS, nightly encrypted backups (if B2 set).
set -euo pipefail

DOMAIN="${1:-grading.qhchen.ca}"
REPO="git@github-poster:dtrmcc/poster-grading.git"
REPO_WEB="https://github.com/dtrmcc/poster-grading"
APP_DIR="/opt/poster-grading"
COMPOSE="docker compose -f docker-compose.prod.yml"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
ask()  { local p="$1" d="${2:-}" v; read -r -p "$p${d:+ [$d]}: " v </dev/tty; echo "${v:-$d}"; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 1; }
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || [ "${ID:-}" = "debian" ] || warn "Tested on Ubuntu/Debian; you have ${PRETTY_NAME:-unknown}."

# ------------------------------------------------------------------ system
say "Updating the system (this can take a few minutes)"
export DEBIAN_FRONTEND=noninteractive
timedatectl set-timezone Asia/Singapore || true
apt-get update -q
apt-get -yq -o Dpkg::Options::=--force-confold upgrade
apt-get -yq install ca-certificates curl git ufw fail2ban unattended-upgrades sqlite3 rclone openssl

say "Swap (2 GB)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q vm.swappiness=20; echo 'vm.swappiness=20' > /etc/sysctl.d/99-swappiness.conf
fi
swapon --show

say "Firewall: allow only SSH, HTTP, HTTPS"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null
ufw status | sed -n '1,12p'

say "fail2ban (blocks repeated SSH login failures) and automatic security updates"
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban >/dev/null && systemctl restart fail2ban
echo 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' > /etc/apt/apt.conf.d/20auto-upgrades

say "Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
docker --version && docker compose version

# ------------------------------------------------------------------ code
say "GitHub access (read-only deploy key)"
KEY=/root/.ssh/poster_grading_deploy
mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f "$KEY" ] || ssh-keygen -t ed25519 -N "" -C "poster-grading@$(hostname)" -f "$KEY" >/dev/null
grep -q 'Host github-poster' /root/.ssh/config 2>/dev/null || cat >> /root/.ssh/config <<EOF
Host github-poster
  HostName github.com
  User git
  IdentityFile $KEY
  IdentitiesOnly yes
EOF
ssh-keyscan -t ed25519 github.com 2>/dev/null >> /root/.ssh/known_hosts
until git ls-remote "$REPO" >/dev/null 2>&1; do
  echo
  echo "Add this key on GitHub → $REPO_WEB/settings/keys/new"
  echo "(Title: VPS, leave 'Allow write access' UNticked)"
  echo
  cat "$KEY.pub"
  echo
  read -r -p "Press Enter after adding the key… " _ </dev/tty
done
echo "GitHub access OK"

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  # the folder may already hold a copied .env or data/: clone beside it, then merge in
  rm -rf /tmp/pg-clone && git clone "$REPO" /tmp/pg-clone
  mkdir -p "$APP_DIR" && cp -a /tmp/pg-clone/. "$APP_DIR"/ && rm -rf /tmp/pg-clone
fi
cd "$APP_DIR"

# ------------------------------------------------------------------ settings
say "App settings (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Paste your Together API key (input hidden):"
  read -r -s TKEY </dev/tty; echo
  sed -i "s|^TOGETHER_API_KEY=.*|TOGETHER_API_KEY=$TKEY|" .env
  sed -i "s|^TOGETHER_MODEL_TEXT=.*|TOGETHER_MODEL_TEXT=deepseek-ai/DeepSeek-V4-Pro-0813|" .env
  sed -i "s|^TOGETHER_MODEL_VISION=.*|TOGETHER_MODEL_VISION=MiniMaxAI/MiniMax-M3|" .env
  unset TKEY
else
  echo ".env already present — kept as is."
fi
grep -q '^BACKUP_PASSPHRASE=' .env || echo "BACKUP_PASSPHRASE=$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-40)" >> .env
chmod 600 .env

say "Login for the website"
if [ ! -f deploy/web.env ]; then
  EMAIL=$(ask "E-mail for Let's Encrypt certificate notices")
  USERNAME=$(ask "Username for the grading site" "qinghua")
  while true; do
    read -r -s -p "Password for the grading site (min 12 characters): " PW1 </dev/tty; echo
    read -r -s -p "Repeat password: " PW2 </dev/tty; echo
    [ "$PW1" = "$PW2" ] && [ ${#PW1} -ge 12 ] && break
    warn "Passwords differ or are shorter than 12 characters. Try again."
  done
  HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$PW1")
  umask 077
  cat > deploy/web.env <<EOF
DOMAIN=$DOMAIN
ACME_EMAIL=$EMAIL
APP_USER=$USERNAME
APP_PASSWORD_HASH='$HASH'
EOF
  unset PW1 PW2
else
  echo "deploy/web.env already present — login kept as is. (Delete it and re-run to change.)"
fi

# ------------------------------------------------------------------ DNS check
say "Checking DNS for $DOMAIN"
MYIP=$(curl -4 -fsS https://api.ipify.org || true)
DNSIP=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')
echo "This server: ${MYIP:-unknown}    $DOMAIN → ${DNSIP:-not resolving yet}"
if [ -n "$MYIP" ] && [ "$DNSIP" != "$MYIP" ]; then
  warn "$DOMAIN does not point to this server yet (or a Cloudflare proxy is on)."
  warn "HTTPS certificates need it. In Cloudflare set the record to 'DNS only' (grey cloud)."
  [ "$(ask "Continue anyway? (y/N)" "N")" = "y" ] || exit 1
fi

# ------------------------------------------------------------------ start
say "Building and starting the app (first build takes 5–10 minutes on a small server)"
$COMPOSE up -d --build
docker image prune -f >/dev/null

say "Waiting for the app"
for _ in $(seq 1 60); do
  if $COMPOSE exec -T backend python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" 2>/dev/null; then
    echo "Backend is up."; break; fi
  sleep 3
done
sleep 5
code=$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/" || true)
echo "https://$DOMAIN/ answered HTTP $code (401 = asks for the login: good)"

# ------------------------------------------------------------------ backups
say "Nightly encrypted backups"
if grep -Eq '^B2_KEY_ID=__REPLACE|^B2_KEY_ID=$' .env; then
  warn "B2 is not configured in .env — backups are OFF. Fill in B2_* and re-run this script."
else
  cat > /etc/cron.d/poster-grading-backup <<EOF
# 03:15 every night (Singapore time)
15 3 * * * root $APP_DIR/deploy/backup.sh >> /var/log/poster-grading-backup.log 2>&1
EOF
  echo "Scheduled: deploy/backup.sh every night at 03:15."
fi

# ------------------------------------------------------------------ ssh hardening
say "SSH"
if [ -s /root/.ssh/authorized_keys ]; then
  if [ "$(ask "An SSH key is installed. Disable password login for SSH? (y/N)" "N")" = "y" ]; then
    printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
      > /etc/ssh/sshd_config.d/99-hardening.conf
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    echo "Password login disabled; use your SSH key."
  fi
else
  warn "No SSH key installed. Recommended: from your Mac run  ssh-copy-id root@$MYIP  then re-run this script."
fi

say "Done"
echo "Open https://$DOMAIN  and log in with the username and password you chose."
echo "Update later with:  $APP_DIR/deploy/update.sh"
