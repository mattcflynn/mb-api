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

# --------------------------
# HTTP session & headers
# --------------------------
SESSION = requests.Session()
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tacobell.com/",
    "Origin": "https://www.tacobell.com",
    "Connection": "keep-alive",
}
SESSION.headers.update(BROWSER_HEADERS)

CACHE_DIR = Path(".cache/menus")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TTL_SEC = 24 * 3600
JITTER_MIN, JITTER_MAX = 0.12, 0.28  # polite delay between stores


# --------------------------
# Helpers: CSV columns, IDs
# --------------------------
def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    if "url" not in df.columns:
        raise ValueError("CSV missing required column 'url'.")
    # Keep given store_id if present; otherwise derive from URL later
    return df


def parse_store_id_from_url(u: str) -> str | None:
    """
    Typical pattern: https://locations.tacobell.com/<state>/<city>/<STOREID>.html
    Accepts alpha-prefix too (e.g., G135807).
    """
    m = re.search(r"/([A-Za-z]?\d{4,7})\.html?$", str(u))
    return m.group(1) if m else None


ALPHA_PREFIX = re.compile(r"^[A-Za-z](\d{5,7})$")  # G135807 -> 135807
DIGITS = re.compile(r"^\d{4,7}$")
STORE_ID_PATTERN = re.compile(r"^[A-Za-z]?\d{4,7}$")


def sanitize_store_id(raw_id: str | None) -> str | None:
    """
    Normalize store_id to a 'best guess':
      - 'G135807' -> '135807'
      - pure digits -> as-is (keeps leading zeros)
      - else None
    """
    if not raw_id:
        return None
    sid = str(raw_id).strip()
    m = ALPHA_PREFIX.match(sid)
    if m:
        return m.group(1)
    if DIGITS.fullmatch(sid):
        return sid
    return None


def warm_cookies() -> None:
    """Pre-warm cookies (Akamai/site) to reduce 403s."""
    try:
        SESSION.get("https://www.tacobell.com/locations", timeout=20)
    except Exception:
        pass


def maybe_sleep():
    time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))


# --------------------------
# Store page fallback (if no id / retry alt)
# --------------------------
def extract_store_id_from_html(url: str) -> str | None:
    """
    Fallback: fetch the store page and look for a numeric storeNumber.
    """
    try:
        r = SESSION.get(url, timeout=25)
        r.raise_for_status()
        html = r.text

        # Look for a clear "storeNumber":"123456"
        m = re.search(r'"storeNumber"\s*:\s*"(\d{4,7})"', html)
        if m:
            return m.group(1)

        # Sometimes embedded as "storeId":"123456"
        m2 = re.search(r'"storeId"\s*:\s*"(\d{4,7})"', html)
        if m2:
            return m2.group(1)

        # Last resort: digits in the path
        m3 = re.search(r"/(\d{4,7})\.html", url)
        if m3:
            return m3.group(1)
    except Exception:
        return None
    return None


def build_id_candidates(url: str, csv_sid: str | None) -> List[str]:
    """
    Build a prioritized list of candidate IDs to try for a store:
      1) raw CSV store_id (keeps alpha prefix)
      2) sanitized CSV store_id (digits only)
      3) sanitized ID parsed from URL
      4) digits-only for alpha-prefixed
      5) zero-stripped variant ONLY if base starts with '0' (e.g., 019301 -> 19301)
      6) HTML-extracted storeNumber
    All unique, digits only, in priority order.
    """
    cands: List[str] = []

    def add(x: str | None):
        if x and x not in cands:
            cands.append(x)

    raw_csv = (csv_sid or "").strip() if csv_sid else ""
    if raw_csv and STORE_ID_PATTERN.fullmatch(raw_csv):
        add(raw_csv)

    # 2) CSV sanitized
    primary = sanitize_store_id(csv_sid)
    add(primary)

    # 3) URL
    from_url = sanitize_store_id(parse_store_id_from_url(url))
    add(from_url)

    # 4) alpha-prefixed in CSV -> digits tail
    if csv_sid:
        m = ALPHA_PREFIX.match(csv_sid.strip())
        if m:
            add(m.group(1))

    # 5) zero-stripped variant only if leading zero present
    for base in (primary, from_url):
        if base and base.startswith("0"):
            add(base.lstrip("0"))

    # 6) store page HTML
    html_id = sanitize_store_id(extract_store_id_from_html(url))
    add(html_id)

    # Allow digits or leading alpha
    cands = [c for c in cands if c and STORE_ID_PATTERN.fullmatch(c)]
    return cands


# --------------------------
# API fetch & caching
# --------------------------
def fetch_menu_api(store_id: str) -> dict:
    """
    Fetch menu JSON for a given store. Handles 403 via cookie warm-up & retry.
    Raises for non-200, so caller can handle 404 retries with alternates.
    """
    api_url = f"https://www.tacobell.com/tacobellwebservices/v4/tacobell/products/menu/{store_id}"

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
        if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < TTL_SEC:
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
# Name normalization
# --------------------------
STOPWORDS = {"the", "a", "and", "with", "of", "for"}
SIZE_WORDS = {"large", "medium", "small", "grande", "mini", "double", "triple", "party", "pack", "box", "combo"}
MARKS = r"[®™()]"


def normalize_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(MARKS, "", s)
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_base_and_size(s: str) -> tuple[str, str]:
    tokens = normalize_name(s).split()
    base, size = [], []
    for t in tokens:
        if t in SIZE_WORDS or t in {"party", "pack", "box", "combo"}:
            size.append(t)
        else:
            base.append(t)
    return (" ".join(base).strip(), " ".join(size).strip())


def flag_category(name_or_cat: str) -> tuple[int, int]:
    nm = (name_or_cat or "").lower()
    is_breakfast = 1 if "breakfast" in nm else 0
    is_drink = 1 if any(tok in nm for tok in ("drink", "beverage", "freeze", "soda", "tea", "coffee", "lemonade")) else 0
    return is_breakfast, is_drink


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

        candidates = build_id_candidates(url, csv_sid)
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
