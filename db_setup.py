#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_setup.py (migration-aware, idempotent, upsert-only)
------------------------------------------------------
Creates/updates the macrobell SQLite database schema and upserts data from CSVs.

Safe to re-run at any time; performs in-place migrations (ADD COLUMN) if older tables exist.

Inputs:
  --db               macrobell.db
  --menu-catalog     menu_catalog.csv
  --store-products   store_products.csv
  --nutrition        nutrition_latest.csv
  --master           products_master.csv
  --stores-csv       taco_bell_stores_or_with_coords.csv (optional)

Tables:
  stores, products, nutrition_items, product_nutrition_map,
  store_products, prices, prices_staging
"""

from __future__ import annotations
import argparse
import csv
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Tuple

from macrobell.db import connect

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
def execmany(conn: sqlite3.Connection, sql: str, rows):
    conn.executemany(sql, rows)

def table_columns(conn: sqlite3.Connection, table: str) -> dict:
    cols = {}
    for cid, name, ctype, notnull, dflt, pk in conn.execute(f"PRAGMA table_info({table})"):
        cols[name] = {"type": ctype, "notnull": notnull, "default": dflt, "pk": pk}
    return cols

def ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str):
    cols = table_columns(conn, table)
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")

# -------------------------
# Base schema (for brand-new DBs)
# -------------------------
CREATE_TABLES_SQL = """
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

CREATE TABLE IF NOT EXISTS product_nutrition_map (
  canonical_product_id TEXT PRIMARY KEY
    REFERENCES products(canonical_product_id) ON DELETE CASCADE,
  item_id              TEXT REFERENCES nutrition_items(item_id),
  match_confidence     REAL,
  match_method         TEXT,
  reviewed             INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stores (
  store_id     TEXT PRIMARY KEY,
  state        TEXT,
  city         TEXT,
  full_address TEXT,
  zip_code     TEXT,
  latitude     REAL,
  longitude    REAL,
  last_scraped_date TEXT
);

CREATE TABLE IF NOT EXISTS store_products (
  store_id             TEXT,
  canonical_product_id TEXT,
  active               INTEGER DEFAULT 1,
  discovered_at        TEXT,
  PRIMARY KEY (store_id, canonical_product_id),
  FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE,
  FOREIGN KEY (canonical_product_id) REFERENCES products(canonical_product_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS prices (
  store_id             TEXT,
  canonical_product_id TEXT,
  price_cents          INTEGER,
  currency             TEXT DEFAULT 'USD',
  collected_at         TEXT,
  PRIMARY KEY (store_id, canonical_product_id, collected_at)
  -- FK created after migration to avoid failures if parent not present yet
);

CREATE TABLE IF NOT EXISTS prices_staging (
  store_id     TEXT,
  product_code TEXT,
  price_cents  INTEGER,
  currency     TEXT DEFAULT 'USD',
  collected_at TEXT,
  PRIMARY KEY (store_id, product_code, collected_at)
);
"""

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_products_code         ON products(product_code);
CREATE INDEX IF NOT EXISTS idx_store_products_store  ON store_products(store_id);
CREATE INDEX IF NOT EXISTS idx_store_products_prod   ON store_products(canonical_product_id);
CREATE INDEX IF NOT EXISTS idx_prices_store_time     ON prices(store_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_prices_prod_time      ON prices(canonical_product_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_staging_store_time    ON prices_staging(store_id, collected_at);
"""

# -------------------------
# Migrations (adds missing columns/FKs non-destructively)
# -------------------------
def migrate_schema(conn: sqlite3.Connection):
    # Ensure base tables exist (for new DBs)
    conn.executescript(CREATE_TABLES_SQL)

    # === Prices table migrations ===
    # Some older DBs had prices without collected_at/currency.
    if "prices" in existing_tables(conn):
        ensure_column(conn, "prices", "price_cents",  "INTEGER")
        ensure_column(conn, "prices", "currency",     "TEXT")
        ensure_column(conn, "prices", "collected_at", "TEXT")

    # === Prices staging migrations ===
    if "prices_staging" in existing_tables(conn):
        ensure_column(conn, "prices_staging", "price_cents",  "INTEGER")
        ensure_column(conn, "prices_staging", "currency",     "TEXT")
        ensure_column(conn, "prices_staging", "collected_at", "TEXT")

    # === Store_products migrations ===
    if "store_products" in existing_tables(conn):
        ensure_column(conn, "store_products", "active",        "INTEGER DEFAULT 1")
        ensure_column(conn, "store_products", "discovered_at", "TEXT")

    # === products mapping table migrations ===
    if "product_nutrition_map" in existing_tables(conn):
        ensure_column(conn, "product_nutrition_map", "match_confidence", "REAL")
        ensure_column(conn, "product_nutrition_map", "match_method",     "TEXT")
        ensure_column(conn, "product_nutrition_map", "reviewed",         "INTEGER DEFAULT 0")

    # Add missing FKs that depend on columns existing
    # (SQLite doesn't support ADD CONSTRAINT easily; we rely on app-layer integrity and indexes.)

    # Finally, create indexes (now that referenced columns exist)
    conn.executescript(CREATE_INDEXES_SQL)

def existing_tables(conn: sqlite3.Connection) -> set:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}

# -------------------------
# Utilities
# -------------------------
def _to_float(x: str | None):
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
# Loaders (upsert-only; safe to re-run)
# -------------------------
def upsert_products(conn: sqlite3.Connection, menu_catalog_csv: str):
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
        ON CONFLICT(canonical_product_id) DO UPDATE SET
          product_code = excluded.product_code,
          base_name    = excluded.base_name,
          size_variant = excluded.size_variant,
          category     = excluded.category,
          subcategory  = excluded.subcategory,
          is_breakfast = excluded.is_breakfast,
          is_drink     = excluded.is_drink,
          us_active    = excluded.us_active
    """, rows)

def upsert_stores_minimal(conn: sqlite3.Connection, store_products_csv: str):
    store_ids = set()
    for r in read_csv_rows(store_products_csv):
        sid = r.get("store_id","").strip()
        if sid:
            store_ids.add(sid)
    if store_ids:
        execmany(conn, "INSERT OR IGNORE INTO stores (store_id) VALUES (?)", [(sid,) for sid in store_ids])

def upsert_store_products(conn: sqlite3.Connection, store_products_csv: str):
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
        ON CONFLICT(store_id, canonical_product_id) DO UPDATE SET
          active        = excluded.active,
          discovered_at = COALESCE(excluded.discovered_at, store_products.discovered_at)
    """, rows)

def upsert_stores_details(conn: sqlite3.Connection, stores_csv: str):
    rows = []
    for r in read_csv_rows(stores_csv):
        rows.append((
            r.get("store_id",""),
            r.get("state",""),
            r.get("city",""),
            r.get("full_address","") or r.get("address",""),
            r.get("zip_code","") or r.get("zipcode",""),
            _to_float(r.get("latitude")),
            _to_float(r.get("longitude")),
        ))
    execmany(conn, """
        INSERT INTO stores (store_id, state, city, full_address, zip_code, latitude, longitude)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(store_id) DO UPDATE SET
          state        = COALESCE(excluded.state, stores.state),
          city         = COALESCE(excluded.city, stores.city),
          full_address = COALESCE(excluded.full_address, stores.full_address),
          zip_code     = COALESCE(excluded.zip_code, stores.zip_code),
          latitude     = COALESCE(excluded.latitude, stores.latitude),
          longitude    = COALESCE(excluded.longitude, stores.longitude)
    """, rows)

def upsert_nutrition(conn: sqlite3.Connection, nutrition_csv: str):
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
        ON CONFLICT(item_id) DO UPDATE SET
          name                 = excluded.name,
          category_nutrition   = excluded.category_nutrition,
          is_breakfast         = excluded.is_breakfast,
          is_drink             = excluded.is_drink,
          serving_weight_grams = excluded.serving_weight_grams,
          calories             = excluded.calories,
          total_fat            = excluded.total_fat,
          saturated_fat        = excluded.saturated_fat,
          trans_fat            = excluded.trans_fat,
          cholesterol          = excluded.cholesterol,
          sodium               = excluded.sodium,
          total_carb           = excluded.total_carb,
          fibers               = excluded.fibers,
          sugars               = excluded.sugars,
          protein              = excluded.protein
    """, rows)

def upsert_master_map(conn: sqlite3.Connection, master_csv: str):
    rows = []
    cur = conn.cursor()
    for r in read_csv_rows(master_csv):
        cpid = r.get("canonical_product_id","")
        item = r.get("item_id","")
        if not cpid:
            code = r.get("product_code","")
            if code:
                hit = cur.execute("SELECT canonical_product_id FROM products WHERE product_code=?", (code,)).fetchone()
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
        ON CONFLICT(canonical_product_id) DO UPDATE SET
          item_id          = excluded.item_id,
          match_confidence = excluded.match_confidence,
          match_method     = excluded.match_method,
          reviewed         = COALESCE(excluded.reviewed, product_nutrition_map.reviewed)
    """, rows)

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
        # 1) migrate (creates tables if needed; adds missing columns; then indexes)
        migrate_schema(conn)

        # 2) Upserts (no truncation; safe to re-run)
        upsert_products(conn, args.menu_catalog)
        upsert_stores_minimal(conn, args.store_products)
        upsert_store_products(conn, args.store_products)
        if file_exists(args.stores_csv):
            upsert_stores_details(conn, args.stores_csv)
        upsert_nutrition(conn, args.nutrition)
        upsert_master_map(conn, args.master)

        conn.commit()
        print("[done] Database setup complete (migrated & upserted).")

        # Summary
        for name in ("products","stores","store_products","nutrition_items","product_nutrition_map","prices","prices_staging"):
            cnt = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            print(f"  - {name:22s}: {cnt}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
