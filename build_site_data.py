"""
Build static site data (site/data/*.json) from macrobell.db.

Outputs:
  items.json           — 44 menu items w/ nutrition + national price stats + hi/lo 5
  national.json        — store count + overall hi/lo 5 stores
  history/{slug}.json  — monthly avg price per item (forward-filled)
  stores.json          — every store w/ latest price per item (parallel arrays)
  zip_latlon.json      — ZCTA centroid lookup from Census gazetteer

Usage: uv run python build_site_data.py [--db macrobell.db] [--out site/data]
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import re
import statistics
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from macrobell.db import connect
from macrobell.normalize import normalize_name

NUTRITION_CATEGORIES = (
    "Cantina Chicken Menu", "Tacos", "Burritos",
    "Nachos", "Quesadillas", "Specialties",
)
CATEGORY_SLUG = {
    "Cantina Chicken Menu": "cantina-chicken-menu",
    "Tacos": "tacos",
    "Burritos": "burritos",
    "Nachos": "nachos",
    "Quesadillas": "quesadillas",
    "Specialties": "specialties",
}
FALLBACK_SLUGS = ("best-sellers", "new")
EXCLUDED_PRODUCT_CATEGORIES = {
    "party-packs", "deals-and-combos", "tacoloverspass", "member-exclusive",
    "passes", "nacho-fries-pass", "online-exclusives",
}
EXCLUDED_NAME_RE = re.compile(r"\b(meal|party pack|combo|box)\b")
OVERRIDES_CSV = "site_item_overrides.csv"
ZCTA_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_zcta_national.zip"
ZCTA_CACHE = "zcta_cache.csv"
MIN_PRICE_CENTS = 100
OVERALL_MIN_ITEMS = 10


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def pretty_city(city: str) -> str:
    return (city or "").replace("-", " ").title()


def street_part(full_address: str) -> str:
    return (full_address or "").split(",")[0].strip()


def zip_from_address(full_address: str) -> str:
    m = re.search(r"(\d{5})(?:-\d{4})?\s*$", full_address or "")
    return m.group(1) if m else ""


def load_overrides(root: Path) -> dict[str, int]:
    path = root / OVERRIDES_CSV
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {r["item_id"]: int(r["canonical_product_id"]) for r in csv.DictReader(f)}


def fetch_candidate_rows(db) -> list[dict]:
    ph = ",".join("?" * len(NUTRITION_CATEGORIES))
    cur = db.execute(f"""
        SELECT n.item_id, n.name, n.category_nutrition,
               n.calories, n.protein, n.total_fat, n.total_carb,
               n.saturated_fat, n.sodium, n.fibers, n.sugars,
               n.cholesterol, n.trans_fat,
               p.canonical_product_id, p.base_name, p.category
        FROM nutrition_items n
        JOIN product_nutrition_map pnm ON pnm.item_id = n.item_id
        JOIN products p ON p.canonical_product_id = pnm.canonical_product_id
        WHERE n.category_nutrition IN ({ph})
          AND n.protein > 0 AND p.us_active = 1
          AND pnm.match_confidence >= 0.80
    """, NUTRITION_CATEGORIES)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_latest_table(db) -> None:
    db.execute("""
        CREATE TEMP TABLE latest AS
        SELECT pr.store_id, pr.canonical_product_id AS cid,
               MIN(pr.price_cents) AS price_cents
        FROM prices pr
        JOIN (
            SELECT store_id, canonical_product_id, MAX(collected_at) AS max_ts
            FROM prices
            WHERE price_cents >= ?
            GROUP BY store_id, canonical_product_id
        ) m ON pr.store_id = m.store_id
           AND pr.canonical_product_id = m.canonical_product_id
           AND pr.collected_at = m.max_ts
        WHERE pr.price_cents >= ?
        GROUP BY pr.store_id, pr.canonical_product_id
    """, (MIN_PRICE_CENTS, MIN_PRICE_CENTS))
    db.execute("CREATE INDEX idx_latest_cid ON latest(cid)")


def price_coverage(db, cid: int) -> int:
    return db.execute("SELECT COUNT(*) FROM latest WHERE cid = ?", (cid,)).fetchone()[0]


def pick_representative(db, item_rows: list[dict], overrides: dict[str, int]) -> dict | None:
    """One product per nutrition item: overrides, then category match + name overlap + coverage."""
    item_id = item_rows[0]["item_id"]
    if item_id in overrides:
        forced = overrides[item_id]
        for r in item_rows:
            if r["canonical_product_id"] == forced:
                return r
        print(f"  WARN override cid={forced} not in candidates for {item_rows[0]['name']}", file=sys.stderr)

    target_slug = CATEGORY_SLUG[item_rows[0]["category_nutrition"]]
    nutri_tokens = set(normalize_name(item_rows[0]["name"]).split())

    def eligible(r):
        return (r["category"] not in EXCLUDED_PRODUCT_CATEGORIES
                and not EXCLUDED_NAME_RE.search(normalize_name(r["base_name"])))

    candidates = [r for r in item_rows if eligible(r)] or item_rows
    # prefer products that actually have price data
    covered = [r for r in candidates if price_coverage(db, r["canonical_product_id"]) >= 10]
    if covered:
        candidates = covered

    def score(r):
        toks = set(normalize_name(r["base_name"]).split())
        overlap = len(toks & nutri_tokens) / max(len(toks | nutri_tokens), 1)
        cat = 2 if r["category"] == target_slug else (1 if r["category"] in FALLBACK_SLUGS else 0)
        return (round(overlap, 2), price_coverage(db, r["canonical_product_id"]), cat)

    return max(candidates, key=score)


def item_price_stats(db, cid: int) -> dict | None:
    rows = db.execute("""
        SELECT l.price_cents, l.store_id, s.city, s.state, s.latitude, s.longitude,
               s.full_address
        FROM latest l JOIN stores s ON s.store_id = l.store_id
        WHERE l.cid = ? AND s.latitude IS NOT NULL
        ORDER BY l.price_cents
    """, (cid,)).fetchall()
    if len(rows) < 10:
        return None
    prices = [r[0] for r in rows]
    avg = statistics.fmean(prices)
    cv = statistics.pstdev(prices) / avg if avg else 0.0

    def store_dict(r):
        return {"store_id": r[1], "city": pretty_city(r[2]), "state": (r[3] or "").upper(),
                "addr": street_part(r[6]),
                "price_cents": r[0], "lat": round(r[4], 4), "lon": round(r[5], 4)}

    return {
        "store_count": len(rows),
        "national_avg_cents": round(avg),
        "national_min_cents": prices[0],
        "national_max_cents": prices[-1],
        "cv": round(cv, 4),
        "lo5_stores": [store_dict(r) for r in rows[:5]],
        "hi5_stores": [store_dict(r) for r in rows[-5:][::-1]],
    }


def month_ends(db) -> list[str]:
    """Last day of each month in the price data range (clamped to max date)."""
    lo, hi = db.execute("SELECT MIN(collected_at), MAX(collected_at) FROM prices").fetchone()
    cur = date.fromisoformat(lo[:10]).replace(day=1)
    end = date.fromisoformat(hi[:10])
    ends = []
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        ends.append(min(nxt - timedelta(days=1), end).isoformat())
        cur = nxt
    return ends


def item_history(db, cid: int, ends: list[str]) -> list[dict]:
    monthly = []
    for d in ends:
        avg, n = db.execute("""
            SELECT AVG(price_cents), COUNT(*) FROM (
                SELECT pr.price_cents
                FROM prices pr
                JOIN (
                    SELECT store_id, MAX(collected_at) AS max_ts
                    FROM prices
                    WHERE canonical_product_id = ? AND collected_at < date(?, '+1 day')
                      AND price_cents >= ?
                    GROUP BY store_id
                ) m ON pr.store_id = m.store_id AND pr.collected_at = m.max_ts
                WHERE pr.canonical_product_id = ? AND pr.price_cents >= ?
                GROUP BY pr.store_id
            )
        """, (cid, d, MIN_PRICE_CENTS, cid, MIN_PRICE_CENTS)).fetchone()
        if n:
            monthly.append({"ym": d[:7], "avg_cents": round(avg), "store_count": n})
    return monthly


def compute_overall_hilo(db, cids: list[int]) -> tuple[list, list, int]:
    ph = ",".join("?" * len(cids))
    rows = db.execute(f"""
        SELECT l.store_id, s.city, s.state, s.latitude, s.longitude,
               AVG(l.price_cents) AS avg_cents, COUNT(*) AS n, s.full_address
        FROM latest l JOIN stores s ON s.store_id = l.store_id
        WHERE l.cid IN ({ph}) AND s.latitude IS NOT NULL
        GROUP BY l.store_id
        HAVING n >= ?
        ORDER BY avg_cents
    """, (*cids, OVERALL_MIN_ITEMS)).fetchall()

    def d(r):
        return {"store_id": r[0], "city": pretty_city(r[1]), "state": (r[2] or "").upper(),
                "addr": street_part(r[7]),
                "lat": round(r[3], 4), "lon": round(r[4], 4),
                "avg_price_cents": round(r[5]), "item_count": r[6]}

    return [d(r) for r in rows[-5:][::-1]], [d(r) for r in rows[:5]], len(rows)


def compute_stores_json(db, cids: list[int], generated_at: str) -> dict:
    ph = ",".join("?" * len(cids))
    idx = {cid: i for i, cid in enumerate(cids)}
    stores: dict[str, dict] = {}
    for sid, city, state, addr, lat, lon, cid, cents in db.execute(f"""
        SELECT s.store_id, s.city, s.state, s.full_address, s.latitude, s.longitude,
               l.cid, l.price_cents
        FROM latest l JOIN stores s ON s.store_id = l.store_id
        WHERE l.cid IN ({ph}) AND s.latitude IS NOT NULL
    """, cids):
        st = stores.get(sid)
        if st is None:
            st = stores[sid] = {
                "sid": sid, "city": pretty_city(city), "state": (state or "").upper(),
                "addr": street_part(addr), "zip": zip_from_address(addr),
                "lat": round(lat, 4), "lon": round(lon, 4),
                "prices": [0] * len(cids),
            }
        st["prices"][idx[cid]] = cents
    return {"generated_at": generated_at, "item_cids": cids,
            "stores": list(stores.values())}


def load_or_fetch_zcta(root: Path) -> dict[str, list[float]]:
    cache = root / ZCTA_CACHE
    if not cache.exists():
        print(f"Downloading ZCTA gazetteer from Census...")
        with urllib.request.urlopen(ZCTA_URL, timeout=120) as resp:
            zf = zipfile.ZipFile(io.BytesIO(resp.read()))
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        cache.write_bytes(zf.read(name))
    out = {}
    with open(cache, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fields = {k.strip(): k for k in reader.fieldnames}
        for row in reader:
            zc = row[fields["GEOID"]].strip()
            lat = float(row[fields["INTPTLAT"]].strip())
            lon = float(row[fields["INTPTLONG"]].strip())
            out[zc] = [round(lat, 3), round(lon, 3)]
    return out


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"  wrote {path} ({path.stat().st_size:,} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--out", default="site/data")
    args = ap.parse_args()

    root = Path(__file__).parent
    out = root / args.out
    db = connect(str(root / args.db))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("Building latest-price temp table...")
    build_latest_table(db)

    rows = fetch_candidate_rows(db)
    by_item: dict[str, list[dict]] = {}
    for r in rows:
        by_item.setdefault(r["item_id"], []).append(r)
    print(f"{len(rows)} candidate products -> {len(by_item)} nutrition items")

    overrides = load_overrides(root)
    items, skipped = [], []
    seen_slugs: set[str] = set()
    print("\nRepresentative product per item:")
    for item_id, group in sorted(by_item.items(), key=lambda kv: (kv[1][0]["category_nutrition"], kv[1][0]["name"])):
        rep = pick_representative(db, group, overrides)
        stats = item_price_stats(db, rep["canonical_product_id"])
        if stats is None:
            skipped.append(rep["name"])
            continue
        slug = slugify(rep["name"])
        while slug in seen_slugs:
            slug += "-2"
        seen_slugs.add(slug)
        print(f"  {rep['name']:<45} -> {rep['base_name']} [{rep['category']}] ({stats['store_count']} stores)")
        items.append({
            "id": item_id,
            "cid": rep["canonical_product_id"],
            "name": rep["name"],
            "slug": slug,
            "category": rep["category_nutrition"],
            "calories": rep["calories"], "protein": rep["protein"],
            "fat": rep["total_fat"], "carb": rep["total_carb"],
            "sat_fat": rep["saturated_fat"], "sodium": rep["sodium"],
            "fiber": rep["fibers"], "sugar": rep["sugars"],
            "cholesterol": rep["cholesterol"], "trans_fat": rep["trans_fat"],
            "protein_per_dollar": round(rep["protein"] / (stats["national_avg_cents"] / 100), 2),
            **stats,
        })
    if skipped:
        print(f"\nSkipped (insufficient price data): {', '.join(skipped)}")

    ranked = sorted(items, key=lambda i: i["cv"])
    for rank, it in enumerate(ranked):
        it["cv_percentile"] = round(100 * rank / max(len(ranked) - 1, 1))

    cids = [it["cid"] for it in items]
    hi5, lo5, qualified = compute_overall_hilo(db, cids)

    print("\nWriting JSON files:")
    write_json(out / "items.json", {"generated_at": generated_at, "items": items})
    write_json(out / "national.json", {
        "generated_at": generated_at,
        "store_count": qualified,
        "hi5_overall": hi5, "lo5_overall": lo5,
    })

    print("Computing price history (forward-fill per month)...")
    ends = month_ends(db)
    for it in items:
        monthly = item_history(db, it["cid"], ends)
        write_json(out / "history" / f"{it['slug']}.json",
                   {"cid": it["cid"], "name": it["name"], "monthly": monthly})

    write_json(out / "stores.json", compute_stores_json(db, cids, generated_at))
    write_json(out / "zip_latlon.json", load_or_fetch_zcta(root))

    print(f"\nDone. {len(items)} items, {qualified} qualified stores.")
    db.close()


if __name__ == "__main__":
    main()
