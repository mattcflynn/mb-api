#!/usr/bin/env bash
# Weekly MacroBell pipeline: scrape prices + nutrition, rebuild site data, push.
# Run by launchd (com.macrobell.weekly-deploy) or manually.
set -euo pipefail

ROOT="/Users/mattf/Developer/api-mb"
LOGDIR="$ROOT/logs"
mkdir -p "$LOGDIR"
exec >> "$LOGDIR/deploy_$(date +%Y%m%d).log" 2>&1

cd "$ROOT"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

echo "=== MacroBell deploy start: $(date) ==="

echo "--- price scrape (national) ---"
"$UV" run python api_scraper_db.py

echo "--- nutrition scrape ---"
"$UV" run python scrape_nutrition.py --load-db

echo "--- relink nutrition ---"
"$UV" run python relink.py

echo "--- rebuild site data ---"
"$UV" run python build_site_data.py

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
    git commit -m "chore: weekly site data rebuild $(date +%Y-%m-%d)"
    git push origin main
fi

echo "=== MacroBell deploy done: $(date) ==="
