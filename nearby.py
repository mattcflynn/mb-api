#!/usr/bin/env python3
"""
nearby.py — Find the best macro-per-dollar at Taco Bell stores near you.

Usage:
    uv run nearby.py --zip 90210 --macro protein
    uv run nearby.py --lat 34.09 --lon -118.41 --macro protein --radius 10
    uv run nearby.py --zip 10001 --macro calories --top 20
"""
from __future__ import annotations

import argparse
import math
import sys

from geopy.geocoders import Nominatim

from macrobell.config import DEFAULT_DB
from macrobell.db import connect

MACRO_COLS = {
    "protein": "n.protein",
    "calories": "n.calories",
    "fat": "n.total_fat",
    "carbs": "n.total_carb",
}

MACRO_UNITS = {
    "protein": "g",
    "calories": "kcal",
    "fat": "g",
    "carbs": "g",
}


def zip_to_latlon(zip_code: str) -> tuple[float, float]:
    geolocator = Nominatim(user_agent="macrobell-nearby/1.0")
    loc = geolocator.geocode({"postalcode": zip_code, "country": "US"}, timeout=10)
    if loc is None:
        sys.exit(f"Could not geocode ZIP code: {zip_code}")
    return loc.latitude, loc.longitude


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def lat_lon_bounds(lat: float, lon: float, radius_miles: float) -> tuple[float, float, float, float]:
    """Bounding box for SQL pre-filter."""
    deg_per_mile_lat = 1 / 69.0
    deg_per_mile_lon = 1 / (69.0 * math.cos(math.radians(lat)))
    d_lat = radius_miles * deg_per_mile_lat
    d_lon = radius_miles * deg_per_mile_lon
    return lat - d_lat, lat + d_lat, lon - d_lon, lon + d_lon


def find_nearby(lat: float, lon: float, radius: float, macro: str, top: int, db_path: str) -> list[dict]:
    macro_col = MACRO_COLS[macro]
    conn = connect(db_path)
    cur = conn.cursor()

    # Bounding box pre-filter
    min_lat, max_lat, min_lon, max_lon = lat_lon_bounds(lat, lon, radius)

    cur.execute("""
        SELECT store_id, city, state, latitude, longitude
        FROM stores
        WHERE latitude BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
          AND latitude IS NOT NULL
    """, (min_lat, max_lat, min_lon, max_lon))
    candidate_stores = cur.fetchall()

    # Exact haversine filter
    nearby_store_ids = []
    store_info: dict[str, dict] = {}
    for store_id, city, state, slat, slon in candidate_stores:
        dist = haversine_miles(lat, lon, slat, slon)
        if dist <= radius:
            nearby_store_ids.append(store_id)
            # Normalize city (hyphen → space, title case) and state (upper)
            city_fmt = (city or "").replace("-", " ").title()
            state_fmt = (state or "").upper()
            store_info[store_id] = {"city": city_fmt, "state": state_fmt, "dist": round(dist, 1)}

    if not nearby_store_ids:
        return []

    # Latest price per (store, product)
    placeholders = ",".join("?" * len(nearby_store_ids))
    cur.execute(f"""
        SELECT pr.store_id, pr.canonical_product_id, pr.price_cents
        FROM prices pr
        INNER JOIN (
            SELECT store_id, canonical_product_id, MAX(collected_at) AS max_ts
            FROM prices
            WHERE store_id IN ({placeholders})
            GROUP BY store_id, canonical_product_id
        ) latest ON pr.store_id = latest.store_id
                AND pr.canonical_product_id = latest.canonical_product_id
                AND pr.collected_at = latest.max_ts
    """, nearby_store_ids)
    price_rows = cur.fetchall()

    if not price_rows:
        return []

    # Build price lookup: (store_id, cpid) → price_cents
    # Filter out junk prices (below $1.00 — data artifacts)
    prices: dict[tuple, int] = {}
    for store_id, cpid, price_cents in price_rows:
        if price_cents >= 100:
            prices[(store_id, cpid)] = price_cents

    # Get product + nutrition data for eligible products (high-conf links only)
    cur.execute(f"""
        SELECT p.canonical_product_id, p.base_name,
               {macro_col} AS macro_val
        FROM products p
        JOIN product_nutrition_map m ON p.canonical_product_id = m.canonical_product_id
        JOIN nutrition_items n ON m.item_id = n.item_id
        WHERE p.us_active = 1
          AND p.is_drink = 0
          AND p.category NOT IN ('deals-and-combos', 'party-packs')
          AND m.match_confidence >= 0.80
          AND {macro_col} > 0
    """)
    product_rows = cur.fetchall()
    conn.close()

    prod_info: dict[str, tuple] = {row[0]: (row[1], row[2]) for row in product_rows}

    # Compute macro/dollar for each (store, product) with a price
    results = []
    for (store_id, cpid), price_cents in prices.items():
        if cpid not in prod_info:
            continue
        if price_cents <= 0:
            continue
        name, macro_val = prod_info[cpid]
        price_dollars = price_cents / 100.0
        macro_per_dollar = macro_val / price_dollars
        store = store_info[store_id]
        results.append({
            "item": name,
            "store_id": store_id,
            "city": store["city"],
            "state": store["state"],
            "dist_miles": store["dist"],
            "price_dollars": price_dollars,
            "macro_val": macro_val,
            "macro_per_dollar": macro_per_dollar,
        })

    results.sort(key=lambda x: x["macro_per_dollar"], reverse=True)
    return results[:top]


def print_results(results: list[dict], macro: str) -> None:
    unit = MACRO_UNITS[macro]
    col_w = [4, 38, 22, 18, 7, 10, 14]
    header = (
        f"{'Rank':<{col_w[0]}}  "
        f"{'Item':<{col_w[1]}}"
        f"{'Store':<{col_w[2]}}"
        f"{'City, ST':<{col_w[3]}}"
        f"{'Miles':>{col_w[4]}}"
        f"{'Price':>{col_w[5]}}"
        f"{macro.title() + '/' + '$':>{col_w[6]}}"
    )
    sep = "-" * len(header)
    print(f"\n{header}")
    print(sep)
    for i, r in enumerate(results, 1):
        location = f"{r['city']}, {r['state']}"
        store_label = f"Store #{r['store_id']}"
        print(
            f"{i:<{col_w[0]}}  "
            f"{r['item'][:col_w[1]-1]:<{col_w[1]}}"
            f"{store_label:<{col_w[2]}}"
            f"{location[:col_w[3]-1]:<{col_w[3]}}"
            f"{r['dist_miles']:>{col_w[4]}.1f}"
            f"  ${r['price_dollars']:>{col_w[5]-3}.2f}"
            f"  {r['macro_per_dollar']:>{col_w[6]-2}.2f}{unit}/$"
        )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Find best macro-per-dollar at nearby Taco Bell stores.")
    ap.add_argument("--zip", metavar="ZIPCODE", help="US ZIP code (geocoded via Nominatim)")
    ap.add_argument("--lat", type=float, help="Latitude (use with --lon instead of --zip)")
    ap.add_argument("--lon", type=float, help="Longitude (use with --lat instead of --zip)")
    ap.add_argument("--macro", choices=list(MACRO_COLS), default="protein",
                    help="Macro to optimize for (default: protein)")
    ap.add_argument("--radius", type=float, default=25.0, help="Search radius in miles (default: 25)")
    ap.add_argument("--top", type=int, default=15, help="Number of results to show (default: 15)")
    ap.add_argument("--db", default=DEFAULT_DB, help="Path to macrobell.db")
    args = ap.parse_args()

    if args.zip:
        print(f"Geocoding ZIP {args.zip}...")
        lat, lon = zip_to_latlon(args.zip)
        print(f"  → {lat:.4f}, {lon:.4f}")
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        ap.error("Provide --zip OR both --lat and --lon (e.g. --lat 34.09 --lon -118.41)")

    print(f"Searching {args.radius:.0f}mi radius for best {args.macro}/$...")
    results = find_nearby(lat, lon, args.radius, args.macro, args.top, args.db)

    if not results:
        print("No results found. Try increasing --radius or checking your location.")
        sys.exit(0)

    print(f"Top {len(results)} results:")
    print_results(results, args.macro)


if __name__ == "__main__":
    main()
