# MacroBell User Manual

A data pipeline that scrapes, normalizes, and links Taco Bell menu items, store locations, pricing, and nutrition data into a unified SQLite database.

---

## Table of Contents

1. [Setup](#setup)
2. [Pipeline Overview](#pipeline-overview)
3. [Phase 1: Store Discovery](#phase-1-store-discovery)
4. [Phase 2: Store Enrichment](#phase-2-store-enrichment)
5. [Phase 3: Nutrition Data](#phase-3-nutrition-data)
6. [Phase 4: Menu Collection](#phase-4-menu-collection)
7. [Phase 5: Nutrition Linking](#phase-5-nutrition-linking)
8. [Phase 6: Database Setup](#phase-6-database-setup)
9. [Phase 7: Price Collection](#phase-7-price-collection)
10. [Phase 8: Promote Staged Prices](#phase-8-promote-staged-prices)
11. [Query & Analysis Tools](#query-tools)
12. [Shared Package: macrobell/](#shared-package-macrobell)
13. [Database Schema](#database-schema)
14. [Configuration Reference](#configuration-reference)
15. [Troubleshooting](#troubleshooting)

---

## Setup

### Dependencies

```bash
pip install -r requirements.txt
```

Required packages: `geopy`, `playwright`, `requests`, `pandas`, `python-dotenv`, `googlemaps`

If using Playwright (for store ID scraping):

```bash
playwright install chromium
```

### Environment Variables

Create a `.env` file in the project root:

```
GOOGLE_MAPS_API_KEY=your_key_here
```

The Google Maps API key is optional. It is used as a fallback geocoder when Nominatim fails.

---

## Pipeline Overview

The pipeline runs in order. Each phase produces files consumed by later phases.

```
sitemap_scraper.py          ─→  Store URLs
chunk_stores.py             ─→  Chunked store files
store-id-sitemap.py         ─→  Store IDs + addresses
store_geocoder.py           ─→  Geocoded coordinates
combine_chunks.py           ─→  Master store list
scrape_nutrition.py         ─→  Nutrition data (from Nutritionix)
code_mapper_all.py          ─→  Menu catalog + store-product inventory
product_linker.py           ─→  Menu ↔ nutrition linkage (initial)
db_setup.py                 ─→  SQLite database
relink.py                   ─→  Improved nutrition linking (cleanup pass)
api_scraper_db.py           ─→  Prices in database
promote_staging.py          ─→  Finalized prices
nearby.py                   ─→  Query: cheapest macro/$ near a location
analyze_col.py              ─→  Analysis: pricing vs. cost-of-living metrics
```

---

## Phase 1: Store Discovery

### sitemap_scraper.py

Discovers all Taco Bell store URLs from the locations sitemap.

```bash
python sitemap_scraper.py
```

**Output:** `taco_bell_stores_from_sitemap.csv`
Columns: `url`, `state`, `city`, `address_slug`

No arguments. Fetches from `https://locations.tacobell.com/sitemap.xml`.

### chunk_stores.py

Splits the store list into state-grouped chunks of ~500 stores each for parallel processing.

```bash
python chunk_stores.py
```

**Input:** `taco_bell_stores_from_sitemap.csv`
**Output:** `store_chunks/chunk_01.csv`, `chunk_02.csv`, ...

No arguments. Target chunk size is 500 stores.

---

## Phase 2: Store Enrichment

### store-id-sitemap.py

Uses a headless browser to visit each store page and extract the store ID, full address, and zip code.

```bash
python store-id-sitemap.py --chunk 1
```

| Flag | Required | Description |
|------|----------|-------------|
| `--chunk` | Yes | Chunk number to process (e.g., 1 for `chunk_01.csv`) |

**Input:** `store_chunks/chunk_NN.csv`
**Output:** `store_chunks/chunk_NN_with_ids.csv`

Run multiple chunks in parallel:

```bash
for i in {1..10}; do
  python store-id-sitemap.py --chunk $i &
done
wait
```

### store_geocoder.py

Adds latitude/longitude coordinates to each store using Nominatim, with Google Maps as fallback.

```bash
python store_geocoder.py --chunk 1
```

| Flag | Required | Description |
|------|----------|-------------|
| `--chunk` | Yes | Chunk number to geocode |

**Input:** `store_chunks/chunk_NN_with_ids.csv`
**Output:** `store_chunks/chunk_NN_with_coords.csv`

Geocoding strategy (in order):
1. Check `manual_addresses.csv` for overrides
2. Try Nominatim (1s rate limit between requests)
3. Try cleaned address variants (remove suite numbers, expand abbreviations)
4. Fall back to Google Maps API (requires `GOOGLE_MAPS_API_KEY` in `.env`)

**Manual address fixes:** Create `manual_addresses.csv` with columns `original_address,corrected_address` to handle problem addresses.

### combine_chunks.py

Merges all geocoded chunks into a single master store file.

```bash
python combine_chunks.py
```

**Input:** All `store_chunks/chunk_*_with_coords.csv` files
**Output:** `taco_bell_stores_final_with_coords.csv`

---

## Phase 3: Nutrition Data

### scrape_nutrition.py

Scrapes Taco Bell nutrition data from the Nutritionix menu page and writes `nutrition-extract.csv`. This is the canonical source of nutrition data — cleaner than the old CloudFront JSON source (no Test Items, Hidden Items, or beer/wine noise).

```bash
# Just produce the CSV
uv run python scrape_nutrition.py

# Produce CSV and load directly into DB
uv run python scrape_nutrition.py --load-db --db macrobell.db
```

| Flag | Default | Description |
|------|---------|-------------|
| `--out` | `nutrition-extract.csv` | Output CSV path |
| `--load-db` | Off | Load into DB via `db_setup.py` after writing CSV |
| `--db` | `macrobell.db` | DB path (used with `--load-db`) |

**Source:** `https://www.nutritionix.com/taco-bell/menu/premium` (server-rendered HTML table, no JS required)

**Output:** 159 items across 13 categories. Excluded at scrape time: Drinks, Cantina Beer/Wine/Spirits, Fountain Beverages.

**Output columns:** `item_id`, `name`, `category_nutrition`, `is_breakfast`, `is_drink`, `calories`, `total_fat`, `saturated_fat`, `trans_fat`, `cholesterol`, `sodium`, `total_carb`, `fibers`, `sugars`, `protein`

`item_id` is generated as `sha256(name.lower())[:16]` — deterministic, safe to re-run.

**Edge cases:** `"< 1"` → `0.5`, `"< 5"` → `2.5` (conservative midpoint).

### nutrition_scraper_patched.py

Legacy nutrition scraper that fetched from Taco Bell's CloudFront-hosted calculator JSON. **Superseded by `scrape_nutrition.py`** — the Nutritionix page is a cleaner source with no junk categories.

---

## Phase 4: Menu Collection

### code_mapper_all.py

The primary menu discovery script. Fetches menus from the Taco Bell API for every store, normalizes product names, and builds a canonical product catalog with store-level availability.

```bash
python code_mapper_all.py --stores taco_bell_stores_final_with_coords.csv
python code_mapper_all.py --stores taco_bell_stores_final_with_coords.csv --max-stores 100
```

| Flag | Default | Description |
|------|---------|-------------|
| `--stores` | *(required)* | CSV file with store list (must include `url` and `store_id` columns) |
| `--out-catalog` | `menu_catalog.csv` | Output path for canonical product catalog |
| `--out-store-products` | `store_products.csv` | Output path for store-product mappings |
| `--max-stores` | None | Cap on stores to process (useful for testing) |

**Outputs:**

- `menu_catalog.csv` — Canonical products. Columns: `canonical_product_id`, `product_code`, `base_name`, `size_variant`, `category`, `subcategory`, `is_breakfast`, `is_drink`, `us_active`
- `store_products.csv` — Store availability. Columns: `store_id`, `product_code`, `canonical_product_id`, `active`, `discovered_at`
- `store_fetch_failures.csv` — Failed stores (if any)

Menu responses are cached in `.cache/menus/` for 24 hours. Delete that directory to force fresh fetches.

### merge-menus.py

Combines output from multiple parallel runs of `code_mapper_all.py` into single deduplicated files.

```bash
python merge-menus.py
python merge-menus.py --append
```

| Flag | Default | Description |
|------|---------|-------------|
| `--append` | Off | Append to existing files instead of overwriting |

Looks for `menu_catalog_*.csv` and `store_products_*.csv` in the current directory.

---

## Phase 5: Nutrition Linking

### relink.py

Improved nutrition linker that runs directly against `macrobell.db`. Supersedes `product_linker.py` for cleanup runs after the initial pipeline load.

```bash
uv run relink.py
```

No arguments. Reads `macrobell.db` and `link_overrides.csv` from the current directory.

**What it does:**

1. Selects candidate products: `us_active=1, is_drink=0`, excludes `deals-and-combos` and `party-packs`, skips items already linked with `match_confidence >= 0.80`
2. Uses all 159 nutrition items (junk categories were excluded at scrape time by `scrape_nutrition.py`)
3. Applies category-aware pre-filtering (e.g. breakfast products only scored against breakfast nutrition items)
4. Scores with improved Jaccard: strips HTML entities, single-char tokens, and quantity/unit words; adds +0.10 substring boost and +0.15 product-recall boost
5. Upserts results into `product_nutrition_map` via `ON CONFLICT DO UPDATE`

**Thresholds:**
- `>= 0.80` — auto-accept (multiple candidates in filtered set)
- `>= 0.70` — auto-accept (only one candidate in filtered set)
- `< 0.50` — sent to review file

**Outputs:**
- Updated rows in `product_nutrition_map`
- `link_review_needed_v2.csv` — items that couldn't be confidently linked

**When to run:** After `db_setup.py`, or any time you want to close the nutrition linking gap without re-running the full pipeline.

### product_linker.py

Matches menu items to nutrition data using tokenized Jaccard similarity with domain-aware boosters.

```bash
python product_linker.py
python product_linker.py --catalog menu_catalog.csv --nutrition nutrition_latest.csv
```

| Flag | Default | Description |
|------|---------|-------------|
| `--catalog` | `menu_catalog.csv` | Menu catalog from code_mapper_all.py |
| `--nutrition` | `nutrition_latest.csv` | Nutrition data from nutrition_scraper_patched.py |
| `--overrides` | `link_overrides.csv` | Manual override file (skipped if not found) |
| `--out-master` | `products_master.csv` | Output for successfully linked items |
| `--out-review` | `link_review_needed.csv` | Output for items needing manual review |

**Matching algorithm:**

1. Manual overrides checked first (always win)
2. Candidates filtered by breakfast/drink flags
3. Jaccard similarity computed on core tokens (stopwords and size words removed)
4. Substring boost: +0.10 if one name contains the other
5. Acceptance thresholds:
   - Jaccard >= 0.80: auto-accept
   - Jaccard >= 0.65 with key token alignment: auto-accept
   - Below thresholds: sent to review file

**Override file format** (`link_overrides.csv`):

```csv
product_code,item_id
BF0001,item_12345
```

### interactive_link_review.py

Interactive CLI for manually reviewing and approving low-confidence matches.

```bash
python interactive_link_review.py
```

No arguments. Uses hardcoded paths:
- Input: `link_review_needed_sorted_by_category.csv`
- Output: appends to `link_overrides.csv`

For each unmatched product, prompts:
- `[y]es` — Accept the suggested match
- `[m]anual ID` — Enter a custom item_id
- `[s]kip` — Skip this product
- `[q]uit` — Save and exit

After reviewing, re-run `product_linker.py` to incorporate the new overrides.

### sort_nutrition_data.py

Sorts a CSV by its `category` column. Useful for preparing data for interactive review.

```bash
python sort_nutrition_data.py --input-file link_review_needed.csv --output-file link_review_needed_sorted_by_category.csv
```

| Flag | Required | Description |
|------|----------|-------------|
| `--input-file` | Yes | Input CSV path |
| `--output-file` | Yes | Output CSV path |

---

## Phase 6: Database Setup

### db_setup.py

Creates the SQLite database schema and loads data from CSV files. Idempotent — safe to re-run.

```bash
python db_setup.py
python db_setup.py --db macrobell.db --stores-csv taco_bell_stores_final_with_coords.csv
```

| Flag | Default | Description |
|------|---------|-------------|
| `--db` | `macrobell.db` | Database file path |
| `--menu-catalog` | `menu_catalog.csv` | Menu catalog CSV |
| `--store-products` | `store_products.csv` | Store-products CSV |
| `--nutrition` | `nutrition_latest.csv` | Nutrition CSV |
| `--master` | `products_master.csv` | Products master CSV |
| `--stores-csv` | None | Stores CSV with coordinates (optional, enriches store records) |

All inserts use upsert logic (`INSERT OR IGNORE` / `ON CONFLICT DO UPDATE`). Includes automatic schema migration for older databases.

---

## Phase 7: Price Collection

### api_scraper_db.py

Production price scraper. Fetches menus from the Taco Bell API and writes prices to the database.

```bash
# Scrape all stores
python -u api_scraper_db.py --db macrobell.db --verbose

# Scrape a single store
python -u api_scraper_db.py --db macrobell.db --store 041070 --verbose

# Scrape by region
python -u api_scraper_db.py --db macrobell.db --regions "West_Coast,Mountain" --verbose

# Exclude regions
python -u api_scraper_db.py --db macrobell.db --exclude-regions "Pacific" --verbose

# List available regions and store counts
python api_scraper_db.py --db macrobell.db --list-regions
```

| Flag | Default | Description |
|------|---------|-------------|
| `--db` | `macrobell.db` | Database file path |
| `--store` | None | Scrape only this store_id |
| `--regions` | None | Comma-separated region names to include |
| `--exclude-regions` | None | Comma-separated region names to exclude |
| `--list-regions` | Off | List regions and store counts, then exit |
| `--sleep-min` | `0.10` | Minimum delay between requests (seconds) |
| `--sleep-max` | `0.25` | Maximum delay between requests (seconds) |
| `--verbose` | Off | Detailed logging |
| `--dump-store-json` | None | Store ID to dump raw JSON for inspection |
| `--peek` | None | Store ID to print first 8 product entries |
| `--log-misses` | Off | Print first 20 product codes that failed price extraction |

**Available regions:** West_Coast, Pacific, Mountain, Southwest, South_Central, Southeast, Great_Lakes, Midwest_Plains, Mid_Atlantic, New_England

**How prices are stored:**
- Known product codes (in the `products` table) go to the `prices` table
- Unknown product codes go to `prices_staging` for later promotion

**Error recovery:**
- 403 errors trigger a cookie warmup and retry
- 404 errors fall through from API v5 to v4, and try alternate store ID formats
- 20-second hard deadline per request

---

## Phase 8: Promote Staged Prices

### promote_staging.py

Moves prices from the staging table to the final prices table after validating that product codes map to known products.

```bash
# Preview what would be promoted
python promote_staging.py --db macrobell.db --dry-run --verbose

# Promote all staged prices
python promote_staging.py --db macrobell.db

# Promote with auto-creation of missing store-product pairs
python promote_staging.py --db macrobell.db --autocreate-store-product --verbose

# Promote for a specific store
python promote_staging.py --db macrobell.db --store 041070

# Promote a specific date range
python promote_staging.py --db macrobell.db --since 2025-01-20 --until 2025-01-27
```

| Flag | Default | Description |
|------|---------|-------------|
| `--db` | `macrobell.db` | Database file path |
| `--store` | None | Only promote rows for this store_id |
| `--since` | None | Only promote rows collected on or after this date (`YYYY-MM-DD` or full ISO) |
| `--until` | None | Only promote rows collected on or before this date |
| `--dry-run` | Off | Preview without writing |
| `--keep-staging` | Off | Don't delete staged rows after promotion |
| `--autocreate-store-product` | Off | Auto-create missing store-product pairs before promoting |
| `--verbose` | Off | Detailed logging |

Date values: `YYYY-MM-DD` is interpreted as start-of-day for `--since` and end-of-day for `--until`.

---

## Query & Analysis Tools

### nearby.py

Finds the cheapest macro per dollar at Taco Bell stores near a given location.

```bash
# By ZIP code
uv run nearby.py --zip 90210 --macro protein

# By coordinates
uv run nearby.py --lat 34.09 --lon -118.41 --macro protein

# Other macros
uv run nearby.py --zip 90210 --macro calories
uv run nearby.py --zip 90210 --macro fat
uv run nearby.py --zip 90210 --macro carbs

# Adjust radius and result count
uv run nearby.py --zip 90210 --macro protein --radius 10 --top 20
```

| Flag | Default | Description |
|------|---------|-------------|
| `--zip` | — | US ZIP code (geocoded via Nominatim). Use this or `--lat`/`--lon` |
| `--lat` | — | Latitude (use with `--lon`) |
| `--lon` | — | Longitude (use with `--lat`) |
| `--macro` | `protein` | Macro to optimize: `protein`, `calories`, `fat`, `carbs` |
| `--radius` | `25` | Search radius in miles |
| `--top` | `15` | Number of results to show |
| `--db` | `macrobell.db` | Path to database |

**How it works:**

1. Geocodes ZIP to lat/lon via Nominatim (skipped if `--lat`/`--lon` provided)
2. Bounding-box SQL pre-filter, then exact haversine distance filter
3. Fetches latest price per store/product (most recent `collected_at`)
4. Filters: `price_cents >= 100` (drops data artifacts), `match_confidence >= 0.80` (high-confidence nutrition links only), excludes drinks and combo meals
5. Computes `macro / (price_cents / 100.0)` and ranks descending

**Note:** Price data is available nationally — all 50 states, ~7,584 stores.

### analyze_col.py

Correlates state-level Taco Bell pricing against cost-of-living metrics (Census ACS 2022 median household income and median gross rent, plus state minimum wages). Prints Pearson and Spearman correlations and a full state-by-state table.

```bash
uv run python analyze_col.py
```

No arguments. Fetches Census data live from `api.census.gov` (no API key required). Requires `scipy` and `pandas` in the venv.

**Output:** Pearson/Spearman correlations with significance stars, plus a ranked state table with avg price, Crunchy Taco price, income, rent, and min wage.

**Key findings:** Median gross rent is the strongest predictor of TB price variation across states (r=+0.64, ρ=+0.65, p<0.001). Minimum wage is the strongest Spearman predictor for single-item pricing.

---

## Shared Package: `macrobell/`

Common utilities extracted from the pipeline scripts into a reusable package. All pipeline scripts import from here instead of duplicating code.

### Module summary

| Module | Contents |
|--------|----------|
| `macrobell.config` | All centralized constants: API URLs, HTTP settings, rate limits, cache settings, file paths, matching thresholds, region mappings, NLP word sets |
| `macrobell.normalize` | `normalize_name()`, `normalize_columns()`, `flag_category()`, `split_base_and_size()` |
| `macrobell.http` | `make_session()`, `warm_cookies()`, `BROWSER_HEADERS` |
| `macrobell.store_ids` | `sanitize_store_id()`, `store_id_candidates()`, `parse_store_id_from_url()`, `extract_store_id_from_html()`, `build_id_candidates()` |
| `macrobell.db` | `connect()` — SQLite connection with WAL mode and foreign key pragmas |

### Quick test

```bash
python -c "from macrobell.normalize import normalize_name; print(normalize_name('Cheesy Gordita Crunch®'))"
# cheesy gordita crunch
```

---

## Database Schema

The database (`macrobell.db`) contains these tables:

### products

| Column | Type | Description |
|--------|------|-------------|
| `canonical_product_id` | TEXT PK | Unique product identifier |
| `product_code` | TEXT UNIQUE | API product code |
| `base_name` | TEXT | Normalized product name |
| `size_variant` | TEXT | Size info (if applicable) |
| `category` | TEXT | Menu category |
| `subcategory` | TEXT | Menu subcategory |
| `is_breakfast` | INTEGER | 1 if breakfast item |
| `is_drink` | INTEGER | 1 if drink item |
| `us_active` | INTEGER | 1 if currently on US menu |

### stores

| Column | Type | Description |
|--------|------|-------------|
| `store_id` | TEXT PK | Store identifier |
| `state` | TEXT | Two-letter state code |
| `city` | TEXT | City name |
| `full_address` | TEXT | Full street address |
| `zip_code` | TEXT | ZIP code |
| `latitude` | REAL | Latitude |
| `longitude` | REAL | Longitude |
| `last_scraped_date` | TEXT | Date of last price scrape |

### nutrition_items

| Column | Type | Description |
|--------|------|-------------|
| `item_id` | TEXT PK | Nutrition item identifier |
| `name` | TEXT | Item name |
| `category_nutrition` | TEXT | Nutrition category |
| `is_breakfast` | INTEGER | 1 if breakfast item |
| `is_drink` | INTEGER | 1 if drink item |
| `serving_weight_grams` | REAL | Serving weight |
| `calories` | REAL | Calories |
| `total_fat` | REAL | Total fat (g) |
| `saturated_fat` | REAL | Saturated fat (g) |
| `trans_fat` | REAL | Trans fat (g) |
| `cholesterol` | REAL | Cholesterol (mg) |
| `sodium` | REAL | Sodium (mg) |
| `total_carb` | REAL | Total carbs (g) |
| `fibers` | REAL | Fiber (g) |
| `sugars` | REAL | Sugars (g) |
| `protein` | REAL | Protein (g) |

### product_nutrition_map

Links products to nutrition items.

| Column | Type | Description |
|--------|------|-------------|
| `canonical_product_id` | TEXT FK | References products |
| `item_id` | TEXT FK | References nutrition_items |
| `match_confidence` | REAL | Match confidence score |
| `match_method` | TEXT | How the match was determined |
| `reviewed` | INTEGER | 1 if manually reviewed |

### store_products

Store-level product availability.

| Column | Type | Description |
|--------|------|-------------|
| `store_id` | TEXT FK | References stores |
| `canonical_product_id` | TEXT FK | References products |
| `active` | INTEGER | 1 if currently available |
| `discovered_at` | TEXT | ISO timestamp of first discovery |

### prices

Finalized price records.

| Column | Type | Description |
|--------|------|-------------|
| `store_id` | TEXT | Store identifier |
| `canonical_product_id` | TEXT | Product identifier |
| `price_cents` | INTEGER | Price in cents |
| `currency` | TEXT | Currency code (default: USD) |
| `collected_at` | TEXT | ISO timestamp of collection |

Primary key: `(store_id, canonical_product_id, collected_at)`

### prices_staging

Temporary holding table for prices with unmapped product codes. Same schema as `prices` but keyed on `product_code` instead of `canonical_product_id`.

---

## Configuration Reference

### Caching

Menu API responses are cached in `.cache/menus/` with a 24-hour TTL. Delete this directory to force fresh data.

### Rate Limiting

| Context | Delay |
|---------|-------|
| API requests (code_mapper_all.py) | 0.12–0.28s random jitter |
| API requests (api_scraper_db.py) | 0.10–0.25s (configurable via `--sleep-min`/`--sleep-max`) |
| Nominatim geocoding | 1s minimum between requests |

### HTTP Timeouts

| Setting | Value |
|---------|-------|
| Connect timeout | 5s |
| Read timeout | 12s |
| Hard deadline per call | 20s |
| Retries | 2 (backoff factor 0.3) |
| Retry on status codes | 429, 500, 502, 503, 504 |

### API Endpoints

The scraper tries these endpoints in order:

1. `https://www.tacobell.com/tacobellwebservices/v5/tacobell/products/menu/{store_id}?channel=WEB&lang=en&curr=USD`
2. `https://www.tacobell.com/tacobellwebservices/v4/tacobell/products/menu/{store_id}`

### Matching Thresholds

| Threshold | Value | Behavior |
|-----------|-------|----------|
| HIGH_CONF | 0.80 | Auto-accept match |
| MID_CONF | 0.65 | Accept if key tokens align |
| Substring boost | +0.10 | Applied when one name contains the other |

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `FileNotFoundError: menu_catalog.csv` | Pipeline phase skipped | Run `code_mapper_all.py` first |
| `No viable store_id candidates` | Bad URL or missing store_id in CSV | Check input CSV format |
| 403 errors during scraping | Rate limiting or IP block | Increase `--sleep-min`/`--sleep-max`, wait and retry |
| 404 for all ID candidates | Store closed or ID format changed | Use `--peek` to inspect JSON structure |
| `GOOGLE_MAPS_API_KEY not set` | `.env` missing or not loaded | Create `.env` in project root |
| `No processed chunk files found` | Geocoding incomplete | Run `store_geocoder.py` for all chunks |
| Low match rate in product_linker | Nutrition data stale | Re-run `scrape_nutrition.py`, reload via `db_setup.py`, review `link_review_needed_v2.csv`, update `link_overrides.csv`, then re-run `relink.py` |
| `nearby.py` returns no results | No price data scraped for that area yet | Check `api_scraper_db.py` coverage for the target state |
| Staged prices not promoting | Product codes not in products table | Use `--autocreate-store-product` or update menu catalog |

### Debugging a Single Store

```bash
# Dump raw API response
python -u api_scraper_db.py --db macrobell.db --dump-store-json 041070

# Inspect product structure
python -u api_scraper_db.py --db macrobell.db --peek 041070

# See which product codes fail price extraction
python -u api_scraper_db.py --db macrobell.db --store 041070 --log-misses --verbose
```

### Refreshing Data

To do a full refresh:

1. Delete `.cache/menus/` to clear cached API responses
2. Re-run `scrape_nutrition.py` for updated nutrition data
3. Re-run `code_mapper_all.py` for updated menus
4. Re-run `db_setup.py --nutrition nutrition-extract.csv` to reload the database
5. Re-run `relink.py` to re-link products to nutrition items
6. Review `link_review_needed_v2.csv` and update `link_overrides.csv` as needed, then re-run `relink.py`
7. Re-run `api_scraper_db.py` for fresh prices

**Nutrition-only refresh** (no menu changes):

```bash
uv run python scrape_nutrition.py
uv run python db_setup.py --db macrobell.db --nutrition nutrition-extract.csv
uv run python relink.py --db macrobell.db
```

## Phase 9: Static Website (MacroBell.site)

The `site/` directory is a static website deployed to GitHub Pages at
**https://mattcflynn.github.io/mb-api/** via `.github/workflows/pages.yml`
(runs automatically on any push touching `site/**`).

### Rebuild site data

```bash
uv run python build_site_data.py          # macrobell.db -> site/data/*.json
```

Outputs: `items.json` (42 items: 6 categories, protein>0, conf>=0.80),
`national.json` (overall hi/lo 5 stores), `history/{slug}.json` (monthly
forward-filled averages), `stores.json` (latest price per store per item),
`zip_latlon.json` (Census ZCTA centroids; source cached in `zcta_cache.csv`).

Representative product per nutrition item is picked automatically (name
overlap + price coverage); override via `site_item_overrides.csv`
(`item_id,canonical_product_id`).

### Weekly automated deploy

`deploy.sh` runs: price scrape → nutrition scrape → relink → rebuild site
data → commit & push (which triggers the Pages workflow). Monthly (first run
of the month) it also runs `wal_checkpoint` + `VACUUM`.

Scheduled via `~/Library/LaunchAgents/com.macrobell.weekly-deploy.plist`
(Mondays 3:00 AM):

```bash
launchctl load ~/Library/LaunchAgents/com.macrobell.weekly-deploy.plist
launchctl start com.macrobell.weekly-deploy    # manual trigger
tail -f logs/deploy_$(date +%Y%m%d).log
```

### Monthly store refresh (manual)

Run the Phase 1–2 pipeline (sitemap → chunk → ids → geocode → combine →
db_setup) roughly monthly; store churn is low.

### Local preview

```bash
uv run python -m http.server 8742 -d site
```
