#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_scraper_db.py — hardened scraper
- Robust HTTP (headers, retries, cookie warm-up, watchdog)
- Tries store-id candidates (zero-padded and stripped)
- v5 -> v4 endpoint fallback
- Deep price extraction (variants, lists, recursive scan)
- Writes to prices or prices_staging (no UPSERT; INSERT OR IGNORE only)
- Region filters retained; debug flags available

Usage examples:
  python -u api_scraper_db.py --db macrobell.db --store 041070 --verbose --peek 041070 --dump-store-json 041070
  python -u api_scraper_db.py --db macrobell.db --regions West_Coast --verbose
"""

from __future__ import annotations
import argparse, contextlib, json, random, re, signal, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from macrobell.config import (
    MENU_API_BASE, REQUEST_TIMEOUT, HARD_DEADLINE_SEC,
    REGIONS, US_ABBR, NAME_TO_ABBR,
)
from macrobell.db import connect
from macrobell.http import make_session, warm_cookies as _warm_cookies
from macrobell.store_ids import sanitize_store_id, store_id_candidates

# ---------------- Networking ----------------
SESSION = make_session(connection_mode="close")

def warm_cookies():
    _warm_cookies(SESSION)

@contextlib.contextmanager
def hard_deadline(seconds: int):
    def _handler(signum, frame): raise TimeoutError(f"hard deadline {seconds}s reached")
    prev = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _handler); signal.alarm(seconds); yield
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, prev)

class TimeoutError(Exception): ...


def normalize_state(val: Optional[str]) -> Optional[str]:
    if not val: return None
    s = str(val).strip()
    if not s: return None
    u = s.upper()
    if u in US_ABBR: return u
    return NAME_TO_ABBR.get(s.lower())

def parse_regions_arg(s: Optional[str]) -> Set[str]:
    if not s: return set()
    out = set()
    for part in s.split(","):
        key = part.strip().replace(" ", "_")
        if key and key in REGIONS: out.add(key)
        elif key: raise SystemExit(f"Unknown region '{part}'. Use --list-regions.")
    return out

# ---------------- DB helpers ----------------
def fetch_store_rows(conn: sqlite3.Connection) -> List[tuple]:
    # Expect stores table with (store_id, state)
    return conn.execute("SELECT store_id, state FROM stores WHERE store_id IS NOT NULL AND store_id != ''").fetchall()

def list_regions_with_counts(rows: List[tuple]) -> List[tuple]:
    counts = {name:0 for name in REGIONS}
    for _, st in rows:
        ab = normalize_state(st)
        if not ab: continue
        for name, states in REGIONS.items():
            if ab in states:
                counts[name]+=1; break
    return sorted(counts.items(), key=lambda x: x[0])

def filter_stores_by_regions(rows: List[tuple], include: Set[str], exclude: Set[str]) -> List[str]:
    if not include and not exclude:
        return [r[0] for r in rows]
    include_states: Set[str] = set()
    for r in include: include_states |= REGIONS[r]
    exclude_states: Set[str] = set()
    for r in exclude: exclude_states |= REGIONS[r]
    out = []
    for sid, st in rows:
        ab = normalize_state(st)
        if not ab: continue
        if include and ab not in include_states: continue
        if exclude and ab in exclude_states: continue
        out.append(sid)
    return out

def load_code_to_canonical(conn: sqlite3.Connection) -> Dict[str, str]:
    # product_code -> canonical_product_id
    m = {}
    try:
        for code, cpid in conn.execute("SELECT product_code, canonical_product_id FROM products WHERE product_code IS NOT NULL"):
            if code: m[str(code)] = str(cpid)
    except sqlite3.OperationalError:
        pass
    return m

def load_store_product_pairs(conn: sqlite3.Connection) -> set:
    # (store_id, canonical_product_id)
    s = set()
    try:
        for sid, cpid in conn.execute("SELECT store_id, canonical_product_id FROM store_products"):
            s.add((str(sid), str(cpid)))
    except sqlite3.OperationalError:
        pass
    return s

def upsert_store_last_scraped(conn: sqlite3.Connection, store_id: str, when_iso: str):
    conn.execute("UPDATE stores SET last_scraped_date = ? WHERE store_id = ?", (when_iso, store_id))

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

# ---------------- Price extraction ----------------
def price_to_cents(value) -> Optional[int]:
    if value is None: return None
    if isinstance(value, int): return int(value)
    if isinstance(value, float): return int(round(value * 100))
    s = str(value).strip()
    if not s: return None
    s = s.replace("$","").replace(",","")
    try:
        if "." in s: return int(round(float(s)*100))
        return int(s)
    except Exception:
        return None

def pick_first(values: List[Any]) -> Optional[int]:
    for v in values:
        cents = price_to_cents(v)
        if cents is not None:
            return cents
    return None

def extract_price_from_product(p: dict) -> Optional[int]:
    """
    Robustly extract a price in cents from a product dict.
    Handles:
      - price as number/string
      - price as dict with .value
      - priceRange.{minPrice|min|value}
      - pricing.* mirrors
      - variantOptions[].priceData.value
      - lists like priceList/prices/priceOptions
      - recursive fallback
    """
    def from_price_node(node) -> Optional[int]:
        # node could be number/string/dict
        c = price_to_cents(node)
        if c is not None:
            return c
        if isinstance(node, dict):
            # common shapes
            for k in ("value", "amount", "minPrice", "min", "lowest", "price"):
                if k in node:
                    c = price_to_cents(node[k])
                    if c is not None:
                        return c
        return None

    # direct price / priceRange / pricing.*
    for key in ("price", "priceRange"):
        v = p.get(key)
        c = from_price_node(v)
        if c is not None and c > 0:
            return c
    pr = p.get("pricing") or {}
    for key in ("price", "displayPrice", "priceValue", "basePrice"):
        c = from_price_node(pr.get(key))
        if c is not None and c > 0:
            return c

    # variants / sizes / skus / productVariants (drinks, etc.)
    for key in ("variantOptions", "variants", "sizes", "skus", "productSizes", "productVariants"):
        arr = p.get(key) or []
        for node in arr:
            # direct values on the variant
            for k in ("price", "displayPrice", "priceValue", "basePrice"):
                c = from_price_node(node.get(k))
                if c is not None and c > 0:
                    return c
            # nested priceData/pricing
            c = from_price_node((node.get("priceData") or {}).get("value"))
            if c is not None and c > 0:
                return c
            c = from_price_node((node.get("pricing") or {}).get("price"))
            if c is not None and c > 0:
                return c

    # price lists
    for key in ("priceList", "prices", "priceOptions"):
        pl = p.get(key)
        if isinstance(pl, list):
            for item in pl:
                c = from_price_node(item.get("price") or item.get("displayPrice") or item.get("value"))
                if c is not None and c > 0:
                    return c
        elif isinstance(pl, dict):
            c = from_price_node(pl.get("price") or pl.get("displayPrice") or pl.get("value"))
            if c is not None and c > 0:
                return c

    # last-resort recursive scan for any '*price*' key
    def deep_scan(node, depth=0) -> Optional[int]:
        if depth > 6:
            return None
        if isinstance(node, dict):
            for k, v in node.items():
                if "price" in str(k).lower():
                    c = from_price_node(v)
                    if c is not None and c > 0:
                        return c
                r = deep_scan(v, depth + 1)
                if r is not None and r > 0:
                    return r
        elif isinstance(node, list):
            for it in node:
                r = deep_scan(it, depth + 1)
                if r is not None and r > 0:
                    return r
        return None

    return deep_scan(p)


# ---------------- HTTP fetch (v5 -> v4, candidates) ----------------
def fetch_menu_for_store_any(store_id_raw: str) -> dict:
    base = MENU_API_BASE
    candidates = store_id_candidates(store_id_raw)
    endpoints = [
        lambda sid: f"{base}/v5/tacobell/products/menu/{sid}?channel=WEB&lang=en&curr=USD",
        lambda sid: f"{base}/v4/tacobell/products/menu/{sid}",
    ]
    for sid in candidates:
        for mk in endpoints:
            url = mk(sid)
            SESSION.headers["Referer"] = f"https://www.tacobell.com/locations/{random.randint(1000,9999)}"
            try:
                with hard_deadline(HARD_DEADLINE_SEC):
                    r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code == 403:
                    warm_cookies()
                    time.sleep(0.6 + random.random()*0.4)
                    with hard_deadline(HARD_DEADLINE_SEC):
                        r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                return r.json()
            except Exception:
                continue
    raise RuntimeError(f"HTTP 404/403 for store {store_id_raw} (all candidates tried)")

# ---------------- Scrape one store ----------------
def scrape_store(conn: sqlite3.Connection, store_id: str, code_to_cpid: Dict[str, str],
                 valid_pairs: set, verbose: bool=False, log_misses: bool=False,
                 dump_for: Optional[str]=None, peek_for: Optional[str]=None) -> Tuple[int,int]:
    sid = sanitize_store_id(store_id)
    if not sid:
        if verbose: print(f"[warn] invalid store_id: {store_id}", flush=True)
        return (0, 0)

    try:
        payload = fetch_menu_for_store_any(sid)
    except Exception as e:
        if verbose: print(f"[error] store {sid}: {e}", flush=True)
        return (0, 0)

    if dump_for and dump_for == sid:
        Path(f"dump_{sid}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if verbose: print(f"[debug] wrote dump_{sid}.json", flush=True)

    cats = payload.get("menuProductCategories", []) or []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wrote_final = wrote_stage = 0
    miss_logged = 0

    # quick peek
    if peek_for and peek_for == sid and cats:
        printed = 0
        for cat in cats:
            for p in (cat.get("products") or []):
                code = (p.get("code") or "")[:12]
                name = (p.get("name") or "")[:70]
                print(f"[peek] code={code} name={name} keys={list(p.keys())[:8]}", flush=True)
                printed += 1
                if printed >= 8: break
            if printed >= 8: break

    for cat in cats:
        for p in (cat.get("products") or []):
            code = (p.get("code") or "").strip()
            if not code:
                continue
            cents = extract_price_from_product(p)
            if cents is None:
                if log_misses and miss_logged < 20:
                    print(f"[miss] {sid} code={code} keys={list(p.keys())}", flush=True)
                    miss_logged += 1
                continue

            cpid = code_to_cpid.get(code)
            if cpid and (sid, cpid) in valid_pairs:
                insert_price(conn, sid, cpid, cents, now_iso)
                wrote_final += 1
            else:
                insert_price_staging(conn, sid, code, cents, now_iso)
                wrote_stage += 1

    upsert_store_last_scraped(conn, sid, now_iso)
    return (wrote_final, wrote_stage)

# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--store", help="Scrape only this store_id")
    ap.add_argument("--regions", help="Comma-separated region names (use --list-regions)")
    ap.add_argument("--exclude-regions", help="Comma-separated region names to exclude")
    ap.add_argument("--list-regions", action="store_true", help="List regions and store counts, then exit")
    ap.add_argument("--sleep-min", type=float, default=0.10)
    ap.add_argument("--sleep-max", type=float, default=0.25)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--dump-store-json", help="Store ID to dump raw JSON to dump_<id>.json")
    ap.add_argument("--peek", help="Store ID to print first few product shapes for")
    ap.add_argument("--log-misses", action="store_true")
    args = ap.parse_args()

    include_regions = parse_regions_arg(args.regions)
    exclude_regions = parse_regions_arg(args.exclude_regions)

    conn = connect(args.db)
    try:
        code_to_cpid = load_code_to_canonical(conn)
        valid_pairs = load_store_product_pairs(conn)
        rows = fetch_store_rows(conn)

        if args.list_regions:
            summary = list_regions_with_counts(rows)
            print("Regions and store counts (from current DB):")
            for name, cnt in summary:
                print(f"  {name:15s} {cnt:5d}")
            return

        # Build store list
        stores = [args.store] if args.store else filter_stores_by_regions(rows, include_regions, exclude_regions)
        if not stores:
            print("[warn] no stores to scrape (check regions or stores table).", file=sys.stderr)
            return

        if args.verbose:
            print(f"[info] loaded {len(code_to_cpid)} product_code→canonical mappings", flush=True)
            print(f"[info] loaded {len(valid_pairs)} store↔product pairs", flush=True)
            print(f"[info] scraping {len(stores)} stores", flush=True)
            if include_regions: print(f"[info] include regions: {', '.join(sorted(include_regions))}", flush=True)
            if exclude_regions: print(f"[info] exclude regions: {', '.join(sorted(exclude_regions))}", flush=True)

        warm_cookies()

        total_final = total_stage = 0
        for i, store_id in enumerate(stores, 1):
            if args.verbose:
                print(f"[{i}/{len(stores)}] Scraping store {store_id}", flush=True)
            n_final, n_stage = scrape_store(conn, store_id, code_to_cpid, valid_pairs,
                                            verbose=args.verbose,
                                            log_misses=args.log_misses,
                                            dump_for=args.dump_store_json,
                                            peek_for=args.peek)
            conn.commit()
            total_final += n_final; total_stage += n_stage
            if args.verbose:
                print(f"   -> wrote {n_final} prices, {n_stage} staged", flush=True)
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

        print(f"[done] {len(stores)} stores scraped | prices: {total_final} | staged: {total_stage}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
