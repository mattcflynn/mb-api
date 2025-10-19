#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_setup.py
-----------
Creates/refreshes the macrobell SQLite database with a robust schema:

Inputs (defaults assume files in the working dir):
  --menu-catalog      menu_catalog.csv          # from code_mapper_all.py
  --store-products    store_products.csv        # from code_mapper_all.py
  --nutrition         nutrition_latest.csv      # from nutrition_scraper_latest.py
  --master            products_master.csv       # from product_linker.py
  --stores-csv        taco_bell_stores_or_with_coords.csv  # OPTIONAL: enriches 'stores' with address/coords

Outputs:
  SQLite DB with tables:
    stores, products, nutrition_items, product_nutrition_map,
    store_products, prices, prices_staging

Safe to re-run: tables are created if missing and data is upserted/truncated.
"""

from __future__ import annotations
import argparse
import csv
import os
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# -------------------------
# CSV helpers
# -------------------------
def read_csv_rows(path: str) -> Iterable[Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield { (k.strip().lower().replace(" ", "_")): (v if v is not None else "") for k, v in row.items() }

def file_exists(path: str | None) -> bool:
    return bool(path) and Path(path).exists()

# -------------------------
# SQL helpers
# -------------------------
def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def execmany(conn: sqlite3.Connection, sql: str, rows: Iterable[Tuple]):
    conn.executemany(sql, rows)

def truncate(conn: sqlite3.Connection, table: str):
    conn.execute(f"DELETE FROM {table};")

# -------------------------
# Schema
# -------------------------
SCHEMA_SQL = """
-- Canonical products (menu_catalog)
CREATE TABLE IF NOT EXISTS products (
  canonical_product_id TEXT PRIMARY KEY,
  product_code         TEXT NOT NULL UNIQUE,
  base_name            TEXT NOT NULL,
  size_variant         TEXT,
  category             TEXT,
  subcategory          TEXT,
  is_breakfast         INTEGER DEFAULT 0,
  is_drink             INTEGER DEFAULT 0,
  us_active            INTEGER DEFAULT 1
);

-- Nutrition catalog (nutrition_latest)
CREATE TABLE IF NOT EXISTS nutrition_items (
  item_id              TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  category_nutrition   TEXT,
  is_breakfast         INTEGER DEFAULT 0,
  is_drink             INTEGER DEFAULT 0,
  serving_weight_grams REAL,
  calories             REAL,
  total_fat            REAL,
  saturated_fat        REAL,
  trans_fat            REAL,
  cholesterol          REAL,
  sodium               REAL,
  total_carb           REAL,
  fibers               REAL,
  sugars               REAL,
  protein              REAL
);

-- Deterministic mapping from canonical products -> nutrition items (products_master)
CREATE TABLE IF NOT EXISTS product_nutrition_map (
  canonical_product_id TEXT PRIMARY KEY
    REFERENCES products(canonical_product_id) ON DELETE CASCADE,
  item_id              TEXT REFERENCES nutrition_items(item_id),
  match_confidence     REAL,
  match_method         TEXT,
  reviewed             INTEGER DEFAULT 0
);

-- Stores directory
CREATE TABLE IF NOT EXISTS stores (
  store_id    TEXT PRIMARY KEY,
  state       TEXT,
  city        TEXT,
  full_address TEXT,
  zip_code    TEXT,
  latitude    REAL,
  longitude   REAL,
  last_scraped_date TEXT
);

-- Store availability for products (from code_mapper_all)
CREATE TABLE IF NOT EXISTS store_products (
  store_id             TEXT,
  canonical_product_id TEXT,
  active               INTEGER DEFAULT 1,
  discovered_at        TEXT,
  PRIMARY KEY (store_id, canonical_product_id),
  FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE,
  FOREIGN KEY (canonical_product_id) REFERENCES products(canonical_product_id) ON DELETE CASCADE
);

-- Prices fact (api_scraper_db writes here when mapped)
CREATE TABLE IF NOT EXISTS prices (
  store_id             TEXT,
  canonical_product_id TEXT,
  price_cents          INTEGER,
  currency             TEXT DEFAULT 'USD',
  collected_at         TEXT,
  PRIMARY KEY (store_id, canonical_product_id, collected_at),
  FOREIGN KEY (store_id, canonical_product_id) REFERENCES store_products(store_id, canonical_product_id)
);

-- Staging for prices with unknown mapping (keeps everything!)
CREATE TABLE IF NOT EXISTS prices_staging (
  store_id     TEXT,
  product_code TEXT,
  price_cents  INTEGER,
  currency     TEXT DEFAULT 'USD',
  collected_at TEXT,
  PRIMARY KEY (store_id, product_code, collected_at)
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_products_code ON products(product_code);
CREATE INDEX IF NOT EXISTS idx_store_products_store ON store_products(store_id);
CREATE INDEX IF NOT EXISTS idx_store_products_prod  ON store_products(canonical_product_id);
CREATE INDEX IF NOT EXISTS idx_prices_store_time    ON prices(store_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_prices_prod_time     ON prices(canonical_product_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_staging_store_time   ON prices_staging(store_id, collected_at);
"""

# -------------------------
# Loaders
# -------------------------
def load_products(conn: sqlite3.Connection, menu_catalog_csv: str):
    truncate(conn, "products")
    rows = []
    for r in read_csv_rows(menu_catalog_csv):
        rows.append((
            r.get("canonical_product_id",""),
            r.get("product_code",""),
            r.get("base_name",""),
            r.get("size_variant",""),
            r.get("category",""),
            r.get("subcategory",""),
            int(r.get("is_breakfast") or 0),
            int(r.get("is_drink") or 0),
            int(r.get("us_active") or 1),
        ))
    execmany(conn, """
        INSERT INTO products (
          canonical_product_id, product_code, base_name, size_variant,
          category, subcategory, is_breakfast, is_drink, us_active
        ) VALUES (?,?,?,?,?,?,?,?,?)
    """, rows)

def load_store_products(conn: sqlite3.Connection, store_products_csv: str):
    # Ensure stores exist first (bare minimum)
    store_ids = set()
    for r in read_csv_rows(store_products_csv):
        store_ids.add(r.get("store_id","").strip())
    # Insert missing stores with minimal info if not present
    if store_ids:
        rows = [(sid,) for sid in store_ids if sid]
        execmany(conn, "INSERT OR IGNORE INTO stores (store_id) VALUES (?)", rows)

    truncate(conn, "store_products")
    rows = []
    for r in read_csv_rows(store_products_csv):
        rows.append((
            r.get("store_id",""),
            r.get("canonical_product_id",""),
            int(r.get("active") or 1),
            r.get("discovered_at",""),
        ))
    execmany(conn, """
        INSERT INTO store_products (store_id, canonical_product_id, active, discovered_at)
        VALUES (?,?,?,?)
    """, rows)

def load_stores_details(conn: sqlite3.Connection, stores_csv: str):
    # Upsert store metadata (won’t remove existing stores)
    rows = []
    for r in read_csv_rows(stores_csv):
        # Expect at least these columns if present
        rows.append((
            r.get("store_id",""),
            r.get("state",""),
            r.get("city",""),
            r.get("full_address","") or r.get("address",""),
            r.get("zip_code","") or r.get("zipcode",""),
            float(r.get("latitude") or 0) if (r.get("latitude") or "").strip() != "" else None,
            float(r.get("longitude") or 0) if (r.get("longitude") or "").strip() != "" else None,
        ))
    execmany(conn, """
        INSERT INTO stores (store_id, state, city, full_address, zip_code, latitude, longitude)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(store_id) DO UPDATE SET
          state=excluded.state,
          city=excluded.city,
          full_address=excluded.full_address,
          zip_code=excluded.zip_code,
          latitude=COALESCE(excluded.latitude, stores.latitude),
          longitude=COALESCE(excluded.longitude, stores.longitude)
    """, rows)

def load_nutrition(conn: sqlite3.Connection, nutrition_csv: str):
    truncate(conn, "nutrition_items")
    rows = []
    for r in read_csv_rows(nutrition_csv):
        rows.append((
            r.get("item_id",""),
            r.get("name",""),
            r.get("category","") or r.get("category_nutrition",""),
            int(r.get("is_breakfast") or 0),
            int(r.get("is_drink") or 0),
            _to_float(r.get("serving_weight_grams")),
            _to_float(r.get("calories")),
            _to_float(r.get("total_fat")),
            _to_float(r.get("saturated_fat")),
            _to_float(r.get("trans_fat")),
            _to_float(r.get("cholesterol")),
            _to_float(r.get("sodium")),
            _to_float(r.get("total_carb") or r.get("total_carbs") or r.get("carbohydrates")),
            _to_float(r.get("fibers") or r.get("fiber")),
            _to_float(r.get("sugars") or r.get("sugar")),
            _to_float(r.get("protein")),
        ))
    execmany(conn, """
        INSERT INTO nutrition_items (
          item_id, name, category_nutrition, is_breakfast, is_drink,
          serving_weight_grams, calories, total_fat, saturated_fat, trans_fat,
          cholesterol, sodium, total_carb, fibers, sugars, protein
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)

def load_master_map(conn: sqlite3.Connection, master_csv: str):
    truncate(conn, "product_nutrition_map")
    rows = []
    for r in read_csv_rows(master_csv):
        # Accept either schema from product_linker:
        # - columns from menu_catalog plus: item_id, name_nutrition, match_confidence, match_method
        cpid = r.get("canonical_product_id","") or r.get("canonical_product_id".lower(),"")
        item = r.get("item_id","")
        if not cpid:
            # allow fallback via product_code -> products lookup
            code = r.get("product_code","")
            if code:
                cur = conn.execute("SELECT canonical_product_id FROM products WHERE product_code=?", (code,))
                hit = cur.fetchone()
                cpid = hit[0] if hit else ""
        rows.append((
            cpid, item,
            _to_float(r.get("match_confidence")),
            r.get("match_method","rule"),
            int(r.get("reviewed") or 0),
        ))
    execmany(conn, """
        INSERT INTO product_nutrition_map (
          canonical_product_id, item_id, match_confidence, match_method, reviewed
        ) VALUES (?,?,?,?,?)
    """, rows)

# -------------------------
# Utilities
# -------------------------
def _to_float(x: str | None) -> float | None:
    if x is None: return None
    s = str(x).strip()
    if not s: return None
    try:
        return float(s)
    except ValueError:
        try:
            return float(s.replace(",", ""))
        except Exception:
            return None

# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--menu-catalog", default="menu_catalog.csv")
    ap.add_argument("--store-products", default="store_products.csv")
    ap.add_argument("--nutrition", default="nutrition_latest.csv")
    ap.add_argument("--master", default="products_master.csv")
    ap.add_argument("--stores-csv", default=None, help="Optional: enrich stores with address/coords")
    args = ap.parse_args()

    # sanity checks
    required_files = {
        "menu_catalog": args.menu_catalog,
        "store_products": args.store_products,
        "nutrition": args.nutrition,
        "master": args.master,
    }
    missing = [k for k,p in required_files.items() if not file_exists(p)]
    if missing:
        raise SystemExit(f"Missing required input file(s): {missing}")

    conn = connect(args.db)
    try:
        conn.executescript(SCHEMA_SQL)

        # products first (gives canonical ids)
        load_products(conn, args.menu_catalog)

        # store_products (also ensures minimal stores)
        load_store_products(conn, args.store_products)

        # optional store details enrichment
        if file_exists(args.stores_csv):
            load_stores_details(conn, args.stores_csv)

        # nutrition catalog
        load_nutrition(conn, args.nutrition)

        # deterministic mapping
        load_master_map(conn, args.master)

        conn.commit()
        print("[done] Database setup complete.")

        # Small summary
        for name in ("products","stores","store_products","nutrition_items","product_nutrition_map"):
            cur = conn.execute(f"SELECT COUNT(*) FROM {name}")
            cnt = cur.fetchone()[0]
            print(f"  - {name:22s}: {cnt}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
