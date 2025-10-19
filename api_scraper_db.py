#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_scraper_db.py
-----------------
Scrape Taco Bell per-store menu pricing and write into the SQLite DB.

Behavior
- Reads stores from `stores` table (or a specific store via --store).
- Calls: https://www.tacobell.com/tacobellwebservices/v4/tacobell/products/menu/{store_id}
- Extracts price per product_code.
- Writes to:
    prices(store_id, canonical_product_id, price_cents, currency, collected_at)
  or prices_staging(store_id, product_code, price_cents, currency, collected_at)
  when a mapping isn't available yet.
- Updates stores.last_scraped_date.

Safe to run repeatedly. Uses robust timeouts, retries, and a hard watchdog to avoid hangs.

Usage
  python -u api_scraper_db.py --db macrobell.db
  python -u api_scraper_db.py --db macrobell.db --store 032352 --max-stores 25 --verbose
"""

from __future__ import annotations
import argparse
import contextlib
import json
import random
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------
# Networking / timeouts
# --------------------------
REQUEST_TIMEOUT = (5, 12)  # (connect, read) seconds
HARD_DEADLINE_SEC = 20     # per-request watchdog
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
    "Connection": "close",
}

ALPHA_PREFIX = re.compile(r"^[A-Za-z](\d{5,7})$")  # e.g., G135807 -> 135807
DIGITS = re.compile(r"^\d{4,7}$")

class TimeoutError(Exception): ...
@contextlib.contextmanager
def hard_deadline(seconds: int):
    def _handler(signum, frame):
        raise TimeoutError(f"hard deadline {seconds}s reached")
    prev = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(seconds)
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev)

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

SESSION = make_session()

def warm_cookies():
    try:
        SESSION.get("https://www.tacobell.com/locations", timeout=REQUEST_TIMEOUT)
    except Exception:
        pass

# --------------------------
# DB helpers
# --------------------------
def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def fetch_store_list(conn: sqlite3.Connection, only_store: Optional[str], max_stores: Optional[int]) -> List[str]:
    q = "SELECT store_id FROM stores WHERE store_id IS NOT NULL AND store_id != ''"
    params: Tuple = ()
    if only_store:
        q += " AND store_id = ?"
        params = (only_store,)
    q += " ORDER BY store_id"
    if max_stores:
        q += f" LIMIT {int(max_stores)}"
    return [r[0] for r in conn.execute(q, params).fetchall()]

def load_code_to_canonical(conn: sqlite3.Connection) -> Dict[str, str]:
    # Map product_code -> canonical_product_id
    return {code: cpid for (code, cpid) in conn.execute(
        "SELECT product_code, canonical_product_id FROM products WHERE product_code IS NOT NULL"
    ).fetchall()}

def load_store_product_pairs(conn: sqlite3.Connection) -> set:
    # Set of (store_id, cpid) that exists in store_products (foreign-key parent)
    return {(sid, cpid) for (sid, cpid) in conn.execute(
        "SELECT store_id, canonical_product_id FROM store_products"
    ).fetchall()}

def upsert_store_last_scraped(conn: sqlite3.Connection, store_id: str, when_iso: str):
    conn.execute(
        "UPDATE stores SET last_scraped_date = ? WHERE store_id = ?",
        (when_iso, store_id),
    )

def insert_price(conn: sqlite3.Connection, store_id: str, cpid: str, cents: int, when_iso: str, currency: str="USD"):
    conn.execute("""
        INSERT OR IGNORE INTO prices (store_id, canonical_product_id, price_cents, currency, collected_at)
        VALUES (?, ?, ?, ?, ?)
    """, (store_id, cpid, cents, currency, when_iso))

def insert_price_staging(conn: sqlite3.Connection, store_id: str, product_code: str, cents: int, when_iso: str, currency: str="USD"):
    conn.execute("""
        INSERT OR IGNORE INTO prices_staging (store_id, product_code, price_cents, currency, collected_at)
        VALUES (?, ?, ?, ?, ?)
    """, (store_id, product_code, cents, currency, when_iso))

# --------------------------
# ID normalization
# --------------------------
def sanitize_store_id(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    sid = str(raw_id).strip()
    m = ALPHA_PREFIX.match(sid)
    if m:
        return m.group(1)
    if DIGITS.fullmatch(sid):
        return sid
    return None

# --------------------------
# Price extraction
# --------------------------
def price_to_cents(value) -> Optional[int]:
    """
    Convert various price representations to integer cents.
    Supports:
      - numeric dollars (e.g., 2.99)
      - strings like "$2.99" or "2.99"
      - integer cents (already)
    """
    if value is None:
        return None
    # already an int and plausibly cents
    if isinstance(value, int):
        # Heuristic: if value >= 10000 it might be cents already; accept as-is
        return int(value)
    # numeric float dollars
    if isinstance(value, float):
        return int(round(value * 100))
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("$", "").replace(",", "")
    try:
        if "." in s:
            return int(round(float(s) * 100))
        return int(s)
    except Exception:
        return None

def extract_price_from_product(p: dict) -> Optional[int]:
    """
    Try multiple common shapes seen in Taco Bell payloads.
    We check keys in order and return first non-null cents value.
    """
    candidates = [
        p.get("price"),                       # could be dollars or cents
        p.get("displayPrice"),                # string like "$2.99"
        p.get("priceValue"),                  # dollars
        p.get("basePrice"),                   # dollars
        (p.get("pricing") or {}).get("price"),
        (p.get("pricing") or {}).get("displayPrice"),
        (p.get("pricing") or {}).get("priceValue"),
        (p.get("pricing") or {}).get("basePrice"),
    ]
    for v in candidates:
        cents = price_to_cents(v)
        if cents is not None:
            return cents
    return None

# --------------------------
# HTTP fetch
# --------------------------
def fetch_menu_for_store(store_id: str) -> dict:
    url = f"https://www.tacobell.com/tacobellwebservices/v4/tacobell/products/menu/{store_id}"
    SESSION.headers["Referer"] = f"https://www.tacobell.com/locations/{random.randint(1000,9999)}"
    try:
        with hard_deadline(HARD_DEADLINE_SEC):
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 403:
            # try cookie warm-up once
            warm_cookies()
            time.sleep(0.6 + random.random()*0.4)
            with hard_deadline(HARD_DEADLINE_SEC):
                r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except TimeoutError:
        raise
    except requests.HTTPError as e:
        # Surface status code for logging
        code = getattr(e.response, "status_code", None)
        raise RuntimeError(f"HTTP {code} for store {store_id}") from e

# --------------------------
# Main scraping logic
# --------------------------
def scrape_store(conn: sqlite3.Connection, store_id: str, code_to_cpid: Dict[str, str], valid_pairs: set, verbose: bool=False) -> Tuple[int,int]:
    """
    Returns (n_final, n_staging)
    """
    sid = sanitize_store_id(store_id)
    if not sid:
        if verbose: print(f"[warn] invalid store_id: {store_id}", flush=True)
        return (0, 0)

    try:
        payload = fetch_menu_for_store(sid)
    except TimeoutError as te:
        if verbose: print(f"[timeout] store {sid}: {te}", flush=True)
        return (0, 0)
    except Exception as e:
        if verbose: print(f"[error] store {sid}: {e}", flush=True)
        return (0, 0)

    categories = payload.get("menuProductCategories", []) or []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wrote_final = 0
    wrote_stage = 0

    for cat in categories:
        for p in (cat.get("products") or []):
            code = (p.get("code") or "").strip()
            if not code:
                continue
            cents = extract_price_from_product(p)
            if cents is None:
                # skip if we truly have no price at all
                continue

            cpid = code_to_cpid.get(code)
            if cpid and (sid, cpid) in valid_pairs:
                insert_price(conn, sid, cpid, cents, now_iso)
                wrote_final += 1
            else:
                insert_price_staging(conn, sid, code, cents, now_iso)
                wrote_stage += 1

    # mark store as scraped
    upsert_store_last_scraped(conn, sid, now_iso)
    return (wrote_final, wrote_stage)

# --------------------------
# CLI
# --------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--store", help="Scrape only this store_id")
    ap.add_argument("--max-stores", type=int, help="Limit number of stores to process")
    ap.add_argument("--sleep-min", type=float, default=0.10, help="Min sleep between stores")
    ap.add_argument("--sleep-max", type=float, default=0.25, help="Max sleep between stores")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # connect DB and preload mappings
    conn = connect(args.db)
    try:
        code_to_cpid = load_code_to_canonical(conn)
        valid_pairs = load_store_product_pairs(conn)

        if args.verbose:
            print(f"[info] loaded {len(code_to_cpid)} product_code→canonical mappings", flush=True)
            print(f"[info] loaded {len(valid_pairs)} store↔product availability pairs", flush=True)

        stores = fetch_store_list(conn, args.store, args.max_stores)
        if not stores:
            print("[warn] no stores to scrape (stores table empty?)", file=sys.stderr)
            return

        warm_cookies()

        total_final = total_stage = 0
        for i, store_id in enumerate(stores, 1):
            if args.verbose:
                print(f"[{i}/{len(stores)}] Scraping store {store_id}", flush=True)

            n_final, n_stage = scrape_store(conn, store_id, code_to_cpid, valid_pairs, verbose=args.verbose)
            conn.commit()  # commit after each store to keep progress durable

            total_final += n_final
            total_stage += n_stage

            if args.verbose:
                print(f"   -> wrote {n_final} prices, {n_stage} staged", flush=True)

            # polite jitter between stores
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

        print(f"[done] {len(stores)} stores scraped | prices: {total_final} | staged: {total_stage}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
