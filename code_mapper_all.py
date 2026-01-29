#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
code_mapper_all.py (polished)
-----------------------------
Build a US menu catalog from a list of Taco Bell stores.

Expected CSV headers:
  url,state,city,address_slug,store_id,full_address,zip_code,latitude,longitude

Outputs:
  - menu_catalog.csv
  - store_products.csv
  - store_fetch_failures.csv (only if any failures)
"""

from __future__ import annotations
import argparse
import csv
import hashlib
import json
import random
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import requests

from macrobell.config import CACHE_DIR, CACHE_TTL_SEC, JITTER_MIN, JITTER_MAX, MENU_API_BASE
from macrobell.http import make_session, warm_cookies as _warm_cookies
from macrobell.normalize import normalize_name, normalize_columns, flag_category, split_base_and_size
from macrobell.store_ids import sanitize_store_id, build_id_candidates

# --------------------------
# HTTP session & caching
# --------------------------
SESSION = make_session(retries=0, connection_mode="keep-alive")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def warm_cookies() -> None:
    _warm_cookies(SESSION)


def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_columns(df)
    if "url" not in df.columns:
        raise ValueError("CSV missing required column 'url'.")
    return df


def maybe_sleep():
    time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))


# --------------------------
# API fetch & caching
# --------------------------
def fetch_menu_api(store_id: str) -> dict:
    """
    Fetch menu JSON for a given store. Handles 403 via cookie warm-up & retry.
    Raises for non-200, so caller can handle 404 retries with alternates.
    """
    api_url = f"{MENU_API_BASE}/v4/tacobell/products/menu/{store_id}"

    def do_get():
        SESSION.headers["Referer"] = f"https://www.tacobell.com/locations/{random.randint(1000,9999)}"
        r = SESSION.get(api_url, timeout=30)
        if r.status_code == 403:
            raise requests.HTTPError("403", response=r)
        r.raise_for_status()
        return r.json()

    try:
        return do_get()
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        if status == 403:
            warm_cookies()
            time.sleep(0.6 + random.random() * 0.4)
            return do_get()
        raise


def fetch_menu_with_candidates(candidates: List[str]) -> Tuple[dict, str] | Tuple[None, None]:
    """
    Try each candidate ID in order. Cache successes by ID.
    Returns (payload, used_id) or (None, None).
    """
    if not candidates:
        return None, None

    print(f"Trying store {candidates[0]} (candidates: {', '.join(candidates)})")
    for idx, sid in enumerate(candidates):
        cache_path = CACHE_DIR / f"{sid}.json"

        # Cache hit
        if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL_SEC:
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                return data, sid
            except Exception:
                pass

        # Live fetch
        try:
            data = fetch_menu_api(sid)
            cache_path.write_text(json.dumps(data), encoding="utf-8")
            return data, sid
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status == 404 and idx < len(candidates) - 1:
                print(f"  -> {sid} returned 404, trying next")
                continue
            raise
        except Exception:
            # Non-HTTP error: stop trying this store
            raise

    return None, None


# --------------------------
# Main
# --------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", required=True, help="Path to your CSV with url,state,city,address_slug,store_id,...")
    ap.add_argument("--out-catalog", default="menu_catalog.csv")
    ap.add_argument("--out-store-products", default="store_products.csv")
    ap.add_argument("--max-stores", type=int, default=None, help="Optional cap on stores to process")
    args = ap.parse_args()

    warm_cookies()

    df = pd.read_csv(args.stores)
    df = normalize_cols(df)

    records = []
    name_stats = defaultdict(Counter)  # product_code -> Counter(base_name)
    failures = []
    seen_used_ids = set()  # avoid duplicate processing for the same resolved API id

    processed = 0
    for _, row in df.iterrows():
        if args.max_stores and processed >= args.max_stores:
            break

        url = str(row["url"])
        csv_sid = (str(row["store_id"]).strip()
                   if "store_id" in row and pd.notna(row["store_id"])
                   else None)

        candidates = build_id_candidates(url, csv_sid, SESSION)
        if not candidates:
            print(f"[warn] no viable store_id candidates for URL: {url}")
            failures.append({"url": url, "csv_store_id": csv_sid, "reason": "no_id_candidates"})
            continue

        try:
            payload, used_id = fetch_menu_with_candidates(candidates)
        except Exception as e:
            print(f"[warn] store fetch error for candidates {candidates}: {e}")
            failures.append({"url": url, "csv_store_id": csv_sid, "reason": f"error:{e}"})
            continue

        if not payload or not used_id:
            print(f"[warn] all candidates 404 for URL {url} (cands: {candidates})")
            failures.append({"url": url, "csv_store_id": csv_sid, "reason": "404_all"})
            continue

        # Skip if we’ve already processed this resolved ID (prevents dupes)
        if used_id in seen_used_ids:
            continue
        seen_used_ids.add(used_id)

        # Unpack categories/products using Taco Bell structure
        categories = payload.get("menuProductCategories", []) or []
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

        for cat in categories:
            cat_code = cat.get("code") or ""
            cat_name = cat.get("displayName") or cat.get("name") or ""
            is_b, is_d = flag_category(cat_code or cat_name)

            for p in cat.get("products", []) or []:
                product_code = str(p.get("code") or "").strip()
                name = p.get("name") or ""
                if not product_code or not name:
                    continue

                base, size = split_base_and_size(name)
                name_stats[product_code][base] += 1

                records.append({
                    "store_id": used_id,             # resolved API id used
                    "product_code": product_code,
                    "name": name,
                    "base_name": base,
                    "size_variant": size,
                    "category": cat_code,
                    "subcategory": cat_name,
                    "is_breakfast": is_b,
                    "is_drink": is_d,
                    "discovered_at": now_iso,
                    "active": 1,
                })

        processed += 1
        maybe_sleep()

    # Build canonical product catalog
    canonical = []
    for code, counter in name_stats.items():
        base_name, _ = counter.most_common(1)[0]
        # derive representative category/flags from any occurrence
        row_match = next((r for r in records if r["product_code"] == code), None)
        cat = row_match["category"] if row_match else ""
        subcat = row_match["subcategory"] if row_match else ""
        is_b = row_match["is_breakfast"] if row_match else 0
        is_d = row_match["is_drink"] if row_match else 0

        # stable canonical id
        canonical_product_id = hashlib.sha1(f"{code}|{base_name}".encode("utf-8")).hexdigest()[:16]
        canonical.append({
            "canonical_product_id": canonical_product_id,
            "product_code": code,
            "base_name": base_name,
            "size_variant": "",      # keep per-store size_variants at store level
            "category": cat,
            "subcategory": subcat,
            "is_breakfast": is_b,
            "is_drink": is_d,
            "us_active": 1,
        })

    # Map store_products to canonical ids
    code_to_canon = {c["product_code"]: c["canonical_product_id"] for c in canonical}
    store_products = []
    for r in records:
        store_products.append({
            "store_id": r["store_id"],
            "product_code": r["product_code"],
            "canonical_product_id": code_to_canon.get(r["product_code"]),
            "active": r["active"],
            "discovered_at": r["discovered_at"],
        })

    # Write outputs (use CLI paths!)
    with open(args.out_catalog, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "canonical_product_id", "product_code", "base_name", "size_variant",
            "category", "subcategory", "is_breakfast", "is_drink", "us_active"
        ])
        w.writeheader()
        w.writerows(canonical)

    with open(args.out_store_products, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "store_id", "product_code", "canonical_product_id", "active", "discovered_at"
        ])
        w.writeheader()
        w.writerows(store_products)

    # Failures (if any)
    if failures:
        pd.DataFrame(failures).to_csv("store_fetch_failures.csv", index=False)
        print(f"[note] wrote {len(failures)} failures → store_fetch_failures.csv")

    print(f"[done] Wrote {len(canonical)} products → menu_catalog.csv")
    print(f"[done] Wrote {len(store_products)} store links → store_products.csv")


if __name__ == "__main__":
    main()
