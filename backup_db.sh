#!/usr/bin/env bash
# Daily backup of macrobell.db — run by launchd or manually
DB="/Users/mattf/Developer/api-mb/macrobell.db"
DEST="/Users/mattf/Documents/macrobell-backups"
KEEP_DAYS=7

if [ ! -f "$DB" ]; then
    echo "[backup] macrobell.db not found, skipping" >&2
    exit 1
fi

mkdir -p "$DEST"
STAMP=$(date +%Y%m%d_%H%M%S)
cp "$DB" "$DEST/macrobell_${STAMP}.db"
echo "[backup] saved → $DEST/macrobell_${STAMP}.db"

# Prune backups older than KEEP_DAYS
find "$DEST" -name "macrobell_*.db" -mtime +${KEEP_DAYS} -delete
echo "[backup] pruned copies older than ${KEEP_DAYS} days"
