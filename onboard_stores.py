"""
onboard_stores.py — discover and onboard NEW Taco Bell stores into the DB.

Pipeline role (runs *before* the price scrape so new stores get priced the same
run):

  sitemap_scraper.py  -> taco_bell_stores_from_sitemap.csv  (all live store URLs)
  onboard_stores.py   -> Playwright each UNKNOWN url for its store_id + address,
                         upsert into `stores`, record url->store_id
  geocode_stores.py   -> offline coords for the new rows
  api_scraper_db.py   -> scrapes prices for every store in `stores`

Known urls live in the store_urls table, so each weekly run only visits
genuinely new stores. On the first run, existing stores are matched to their
sitemap url by normalized address (no browser), so only truly-new urls are
visited — a one-time catch-up of a few hundred, not the whole ~8k list.

Usage:
  uv run python onboard_stores.py                  # headless (deploy default)
  uv run python onboard_stores.py --no-headless    # watch the browser
  uv run python onboard_stores.py --limit 50       # cap visits this run
  uv run python onboard_stores.py --max-new 600    # abort if more candidates
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from macrobell.db import connect
from macrobell.config import FULL_STORE_LIST_CSV

_ABBR = {
    "boulevard": "blvd", "street": "st", "avenue": "ave", "drive": "dr",
    "road": "rd", "highway": "hwy", "parkway": "pkwy", "lane": "ln",
    "court": "ct", "place": "pl", "square": "sq", "circle": "cir",
    "trail": "trl", "terrace": "ter", "north": "n", "south": "s",
    "east": "e", "west": "w", "suite": "", "ste": "", "unit": "",
}


def _norm(s: str | None) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    return "-".join(t for t in (_ABBR.get(w, w) for w in s.split()) if t)


def _key(state, city, street) -> tuple:
    return ((state or "").lower(), _norm(city), _norm(street))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ALLOWED_HOST = "locations.tacobell.com"


def is_allowed_url(url: str) -> bool:
    """Only ever drive the browser to real Taco Bell location pages — never let a
    tampered sitemap/CSV point Playwright at an arbitrary (internal) URL."""
    try:
        return urlparse(url).netloc.lower() == ALLOWED_HOST
    except Exception:
        return False


def is_franchise(store_id: str) -> bool:
    """Letter-prefix IDs are franchise locations that 404 on the menu API
    (api_scraper_db.py skips them), so they can never be priced."""
    return bool(re.search(r"[A-Za-z]", store_id or ""))


def mark_known(db, url: str, store_id: str) -> None:
    """Record a url as onboarded without adding it to `stores` (used for
    franchise stores) so it is never re-visited."""
    db.execute("""
        INSERT INTO store_urls (url, store_id, onboarded_at) VALUES (?,?,?)
        ON CONFLICT(url) DO UPDATE SET store_id=excluded.store_id, onboarded_at=excluded.onboarded_at
    """, (url, store_id, _now()))
    db.commit()


def ensure_store_urls(db) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS store_urls (
            url TEXT PRIMARY KEY,
            store_id TEXT,
            onboarded_at TEXT
        )""")
    db.commit()


def load_sitemap(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def seed_known_from_db(db, sitemap_rows: list[dict]) -> int:
    """Match sitemap urls to existing stores by normalized address and record
    them as known *without* visiting. Returns count newly seeded."""
    index = {}
    for sid, st, city, addr in db.execute(
            "SELECT store_id, state, city, full_address FROM stores "
            "WHERE store_id IS NOT NULL AND store_id != '' AND full_address IS NOT NULL"):
        index[_key(st, city, addr.split(",")[0])] = sid
    known = {u for (u,) in db.execute("SELECT url FROM store_urls")}
    now = _now()
    seeded = []
    for r in sitemap_rows:
        if r["url"] in known:
            continue
        sid = index.get(_key(r["state"], r["city"], r["address_slug"]))
        if sid:
            seeded.append((r["url"], sid, now))
    db.executemany(
        "INSERT OR IGNORE INTO store_urls (url, store_id, onboarded_at) VALUES (?,?,?)",
        seeded)
    db.commit()
    return len(seeded)


def _ld_restaurant(page) -> dict:
    """Return the FastFoodRestaurant JSON-LD node (address + geo), or {}."""
    for blk in page.eval_on_selector_all(
            "script[type='application/ld+json']", "els => els.map(e => e.textContent)"):
        try:
            data = json.loads(blk)
        except Exception:
            continue
        for node in (data.get("@graph", []) if isinstance(data, dict) else []):
            if isinstance(node, dict) and node.get("@type") == "FastFoodRestaurant":
                return node
    return {}


def visit_store(page, url: str) -> dict | None:
    """Return {store_id, full_address, zip_code, lat, lon} for a store page, or None.

    The store_id lives in the order links' href (?store=XXXX). Address and
    rooftop coordinates come from the page's JSON-LD, so no click-through is
    needed.
    """
    page.goto(url, timeout=60000)
    try:
        btn = page.get_by_role("button", name=re.compile("accept|agree", re.IGNORECASE))
        btn.wait_for(state="visible", timeout=4000)
        btn.click()
    except Exception:
        pass  # no cookie banner

    store_id = None
    for href in page.eval_on_selector_all("a[href*='store=']", "els => els.map(e => e.href)"):
        m = re.search(r"store=(\w+)", href)
        if m:
            store_id = m.group(1)
            break
    if not store_id:
        return None

    node = _ld_restaurant(page)
    a = node.get("address") or {}
    street, city = a.get("streetAddress"), a.get("addressLocality")
    region, zip_code = a.get("addressRegion"), a.get("postalCode") or ""
    full_address = ", ".join(x for x in
                             [street, city, f"{region or ''} {zip_code}".strip()] if x) or None
    geo = node.get("geo") or {}
    lat = lon = None
    if geo.get("latitude") is not None and geo.get("longitude") is not None:
        lat, lon = float(geo["latitude"]), float(geo["longitude"])
    return {"store_id": store_id, "full_address": full_address,
            "zip_code": zip_code, "lat": lat, "lon": lon}


def upsert_store(db, rec: dict, state, city, url) -> None:
    """Upsert a store row. Rooftop coords from JSON-LD are recorded as 'rooftop';
    stores without geo are left for geocode_stores.py (zip_centroid fallback)."""
    has_geo = rec["lat"] is not None
    db.execute("""
        INSERT INTO stores (store_id, state, city, full_address, zip_code, latitude, longitude, coord_source)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(store_id) DO UPDATE SET
          state        = COALESCE(excluded.state, stores.state),
          city         = COALESCE(excluded.city, stores.city),
          full_address = COALESCE(excluded.full_address, stores.full_address),
          zip_code     = COALESCE(excluded.zip_code, stores.zip_code),
          latitude     = COALESCE(excluded.latitude, stores.latitude),
          longitude    = COALESCE(excluded.longitude, stores.longitude),
          coord_source = COALESCE(excluded.coord_source, stores.coord_source)
    """, (rec["store_id"], (state or "").lower(), city, rec["full_address"], rec["zip_code"],
          rec["lat"], rec["lon"], "rooftop" if has_geo else None))
    db.execute("""
        INSERT INTO store_urls (url, store_id, onboarded_at) VALUES (?,?,?)
        ON CONFLICT(url) DO UPDATE SET store_id=excluded.store_id, onboarded_at=excluded.onboarded_at
    """, (url, rec["store_id"], _now()))
    db.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--no-headless", dest="headless", action="store_false",
                    help="Show the browser window")
    ap.add_argument("--limit", type=int, default=0, help="Max stores to visit this run (0 = all)")
    ap.add_argument("--max-new", type=int, default=600,
                    help="Abort if more candidates than this (sitemap anomaly guard)")
    args = ap.parse_args()

    root = Path(__file__).parent
    db = connect(str(root / args.db))
    ensure_store_urls(db)
    if "coord_source" not in {c[1] for c in db.execute("PRAGMA table_info(stores)")}:
        db.execute("ALTER TABLE stores ADD COLUMN coord_source TEXT")
        db.commit()

    sitemap = load_sitemap(root / FULL_STORE_LIST_CSV)
    if not sitemap:
        print("[onboard] no sitemap CSV — run sitemap_scraper.py first; nothing to do")
        return

    seeded = seed_known_from_db(db, sitemap)
    known = {u for (u,) in db.execute("SELECT url FROM store_urls")}
    todo = [r for r in sitemap if r["url"] not in known]

    rejected = [r for r in todo if not is_allowed_url(r["url"])]
    if rejected:
        print(f"[onboard] rejecting {len(rejected)} non-{ALLOWED_HOST} URL(s) (will not visit)")
        todo = [r for r in todo if is_allowed_url(r["url"])]
    print(f"[onboard] sitemap={len(sitemap)} known={len(known)} seeded_this_run={seeded} to_visit={len(todo)}")

    if len(todo) > args.max_new:
        raise SystemExit(
            f"[onboard] {len(todo)} candidates exceeds --max-new {args.max_new}; "
            f"aborting (likely a sitemap/match anomaly). Re-run manually to catch up.")
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("[onboard] no new stores")
        return

    added = skipped = failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        ctx = browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        for i, r in enumerate(todo, 1):
            url = r["url"]
            print(f"[onboard] {i}/{len(todo)} {url}")
            try:
                rec = visit_store(page, url)
                if not rec:
                    print("          -> no store_id found")
                    failed += 1
                elif is_franchise(rec["store_id"]):
                    # Franchise location — can't be priced; mark known so we never re-visit.
                    mark_known(db, url, rec["store_id"])
                    print(f"          -> store_id={rec['store_id']}  [franchise, skipped]")
                    skipped += 1
                else:
                    upsert_store(db, rec, r["state"], r["city"], url)
                    tag = "rooftop" if rec["lat"] is not None else "no-geo"
                    print(f"          -> store_id={rec['store_id']}  {rec['full_address']}  [{tag}]")
                    added += 1
            except Exception as e:
                print(f"          -> FAILED: {e}")
                failed += 1
        browser.close()

    print(f"[onboard] done: added={added} skipped_franchise={skipped} failed={failed}")
    db.close()


if __name__ == "__main__":
    main()
