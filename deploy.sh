#!/usr/bin/env bash
# Weekly MacroBell pipeline: scrape prices + nutrition, rebuild site data, push.
# Run by launchd (com.macrobell.weekly-deploy) or manually.
set -uo pipefail

ROOT="/Users/mattf/Developer/api-mb"
LOGDIR="$ROOT/logs"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/deploy_$(date +%Y%m%d).log"
exec >> "$LOGFILE" 2>&1

cd "$ROOT"
UV="/opt/homebrew/bin/uv"

echo "=== MacroBell deploy start: $(date) ==="

FAILED=""

run_step() {
    local name=$1; shift
    echo "--- $name ---"
    if "$@"; then
        echo "[ok] $name"
    else
        echo "[FAILED] $name (exit $?)"
        FAILED="$FAILED $name"
    fi
}

run_step prices    "$UV" run python api_scraper_db.py
run_step nutrition "$UV" run python scrape_nutrition.py --load-db
run_step relink    "$UV" run python relink.py
run_step build     "$UV" run python build_site_data.py

# Monthly DB maintenance on the 1st-7th (first weekly run of the month)
if [ "$(date +%d)" -le 07 ]; then
    echo "--- monthly VACUUM ---"
    "$UV" run python -c "
from macrobell.db import connect
db = connect('macrobell.db')
db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
db.execute('VACUUM')
db.close()
print('vacuum done')"
fi

echo "--- commit + push ---"
git add site/data
if git diff --cached --quiet; then
    echo "no data changes to commit"
else
    if git commit -m "chore: weekly site data rebuild $(date +%Y-%m-%d)" && git push origin main; then
        echo "[ok] push"
    else
        echo "[FAILED] push"
        FAILED="$FAILED push"
    fi
fi

echo "--- report ---"
"$UV" run python report.py \
    --log "$LOGFILE" \
    --out "$LOGDIR/report_$(date +%Y%m%d).md" \
    --failed "${FAILED# }"

echo "=== MacroBell deploy done: $(date) ==="

# Exit non-zero if any step failed (after report is written)
if [ -n "$FAILED" ]; then
    echo "[deploy] FAILED steps:$FAILED"
    exit 1
fi
