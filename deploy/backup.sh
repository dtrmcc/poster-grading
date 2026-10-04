#!/usr/bin/env bash
# Encrypted backup of all grading data to Backblaze B2.
#   - consistent copy of the SQLite database (taken by SQLite itself while the app runs)
#   - posters, rendered pages and exported feedback PDFs
#   - encrypted with AES-256 (passphrase BACKUP_PASSPHRASE in .env) BEFORE upload
# Keeps 7 local copies and 60 days on B2.
#
# Restore:  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE \
#             -in pg-YYYYmmdd-HHMM.tar.gz.enc | tar xz        (gives data/ ; grading.sqlite3 inside)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE missing in .env}"

STAMP=$(date +%Y%m%d-%H%M)
LOCAL=/var/backups/poster-grading
mkdir -p "$LOCAL" && chmod 700 "$LOCAL"
SNAP=data/backup-snapshot.sqlite3

docker compose -f docker-compose.prod.yml exec -T backend python - <<'PY'
import sqlite3
src = sqlite3.connect("/data/grading.sqlite3")
dst = sqlite3.connect("/data/backup-snapshot.sqlite3")
src.backup(dst); dst.close(); src.close()
PY

OUT="$LOCAL/pg-$STAMP.tar.gz.enc"
tar czf - --exclude='data/grading.sqlite3*' --transform 's|data/backup-snapshot.sqlite3|data/grading.sqlite3|' data \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE -out "$OUT"
rm -f "$SNAP"
echo "$(date -Is) local backup: $OUT ($(du -h "$OUT" | cut -f1))"

ls -1t "$LOCAL"/pg-*.tar.gz.enc | tail -n +8 | xargs -r rm -f

if [[ "${B2_KEY_ID:-}" != "" && "${B2_KEY_ID}" != __REPLACE* ]]; then
  export RCLONE_CONFIG_B2_TYPE=b2 RCLONE_CONFIG_B2_ACCOUNT="$B2_KEY_ID" RCLONE_CONFIG_B2_KEY="$B2_APPLICATION_KEY"
  rclone copy "$OUT" "b2:$B2_BUCKET/poster-grading/" --b2-hard-delete
  rclone delete "b2:$B2_BUCKET/poster-grading/" --min-age 60d --b2-hard-delete || true
  echo "$(date -Is) uploaded to b2:$B2_BUCKET/poster-grading/"
else
  echo "$(date -Is) B2 not configured; kept local copy only"
fi
