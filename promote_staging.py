#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
promote_staging.py
------------------
Promote mappable rows from prices_staging -> prices.

Rules
- A staged row is promotable if its product_code maps to a canonical_product_id (via products),
  and the (store_id, canonical_product_id) pair exists in store_products.
- Optionally, --autocreate-store-product will upsert missing pairs into store_products first.

Safety
- Idempotent: INSERT OR IGNORE into prices on PK (store_id, canonical_product_id, collected_at).
- By default deletes only the staged rows that were successfully promoted.
- Use --dry-run to preview without writing.

Filters
- --store STORE_ID
- --since YYYY-MM-DD
- --until YYYY-MM-DD  (inclusive, compares against collected_at in UTC ISO8601)

Usage
  python promote_staging.py --db macrobell.db
"""

from __future__ import annotations
import argparse
import sqlite3
from datetime import datetime
from typing import Optional, Tuple, List

ISO_TS = "%Y-%m-%dT%H:%M:%SZ"  # expected format in collected_at

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def parse_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    # Accept YYYY-MM-DD or full ISO; normalize to ISO midnight bounds
    s = s.strip()
    if "T" in s:
        # trust it's ISO already
        return s
    # make it inclusive day bounds by returning ISO strings
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        raise SystemExit(f"Invalid date: {s}. Use YYYY-MM-DD or full ISO.")

def compose_filters(store: Optional[str], since_iso: Optional[str], until_iso: Optional[str]) -> Tuple[str, list]:
    where = []
    params: List[str] = []
    if store:
        where.append("ps.store_id = ?")
        params.append(store)
    if since_iso:
        where.append("ps.collected_at >= ?")
        params.append(since_iso)
    if until_iso:
        where.append("ps.collected_at <= ?")
        params.append(until_iso)
    sql = " AND ".join(where) if where else "1=1"
    return sql, params

def ensure_store_product_pairs(conn: sqlite3.Connection, store: Optional[str], since_iso: Optional[str], until_iso: Optional[str], verbose: bool):
    # Find staged rows whose mapping exists, but store_products pair is missing; insert it.
    filt_sql, params = compose_filters(store, since_iso, until_iso)
    missing = conn.execute(f"""
        SELECT DISTINCT ps.store_id, p.canonical_product_id
        FROM prices_staging ps
        JOIN products p ON p.product_code = ps.product_code
        LEFT JOIN store_products sp
               ON sp.store_id = ps.store_id
              AND sp.canonical_product_id = p.canonical_product_id
        WHERE {filt_sql}
          AND sp.store_id IS NULL
    """, params).fetchall()

    if not missing:
        if verbose:
            print("[auto] no missing store_products pairs to create")
        return 0

    if verbose:
        print(f"[auto] creating {len(missing)} store_products pairs")

    conn.executemany("""
        INSERT OR IGNORE INTO store_products (store_id, canonical_product_id, active, discovered_at)
        VALUES (?, ?, 1, COALESCE(datetime('now'), strftime('%Y-%m-%dT%H:%M:%SZ','now')))
    """, missing)
    return len(missing)

def promote(conn: sqlite3.Connection, store: Optional[str], since: Optional[str], until: Optional[str],
            dry_run: bool, keep_staging: bool, autocreate_pairs: bool, verbose: bool):
    since_iso = parse_date(since)
    # bump until end-of-day if user provided YYYY-MM-DD
    if until and "T" not in until:
        until_iso = parse_date(until)[:-1] + "T23:59:59Z"
    else:
        until_iso = until

    if autocreate_pairs:
        created = ensure_store_product_pairs(conn, store, since_iso, until_iso, verbose)
        if created and verbose:
            print(f"[auto] upserted {created} store_products pairs")

    # Find promotable rows
    filt_sql, params = compose_filters(store, since_iso, until_iso)
    promotable = conn.execute(f"""
        SELECT ps.store_id,
               p.canonical_product_id,
               ps.price_cents,
               COALESCE(ps.currency, 'USD') AS currency,
               ps.collected_at,
               ps.product_code
        FROM prices_staging ps
        JOIN products p
          ON p.product_code = ps.product_code
        JOIN store_products sp
          ON sp.store_id = ps.store_id
         AND sp.canonical_product_id = p.canonical_product_id
        WHERE {filt_sql}
    """, params).fetchall()

    if not promotable:
        print("[done] nothing to promote (0 rows matched).")
        return

    if verbose:
        preview = promotable[:5]
        print(f"[plan] promotable rows: {len(promotable)}")
        for r in preview:
            print(f"  store={r[0]} cpid={r[1]} cents={r[2]} at={r[4]} (code={r[5]})")

    if dry_run:
        print("[dry-run] skipping writes.")
        return

    # Insert into prices (ignore duplicates on PK), then delete those staged rows (if not keeping)
    conn.executemany("""
        INSERT OR IGNORE INTO prices (store_id, canonical_product_id, price_cents, currency, collected_at)
        VALUES (?, ?, ?, ?, ?)
    """, [r[:5] for r in promotable])

    conn.commit()

    # Count actually inserted
    inserted = conn.execute("""
        SELECT COUNT(*) FROM prices pr
        WHERE (pr.store_id, pr.canonical_product_id, pr.collected_at) IN (
            SELECT ps.store_id, p.canonical_product_id, ps.collected_at
            FROM prices_staging ps
            JOIN products p ON p.product_code = ps.product_code
            JOIN store_products sp ON sp.store_id = ps.store_id
                                  AND sp.canonical_product_id = p.canonical_product_id
            WHERE {where}
        )
    """.format(where=filt_sql), params).fetchone()[0]

    if not keep_staging:
        conn.executemany("""
            DELETE FROM prices_staging
            WHERE store_id = ? AND product_code = ? AND collected_at = ?
        """, [(r[0], r[5], r[4]) for r in promotable])
        conn.commit()

    print(f"[done] inserted into prices: {inserted} | staged kept: {('yes' if keep_staging else 'no')}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--store", help="Only promote rows for this store_id")
    ap.add_argument("--since", help="Only promote rows with collected_at >= YYYY-MM-DD (or ISO)")
    ap.add_argument("--until", help="Only promote rows with collected_at <= YYYY-MM-DD (or ISO)")
    ap.add_argument("--dry-run", action="store_true", help="Preview actions without writing")
    ap.add_argument("--keep-staging", action="store_true", help="Do not delete staged rows after promotion")
    ap.add_argument("--autocreate-store-product", action="store_true",
                    help="Upsert missing (store_id, canonical_product_id) into store_products before promotion")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    conn = connect(args.db)
    try:
        promote(conn,
                store=args.store,
                since=args.since,
                until=args.until,
                dry_run=args.dry_run,
                keep_staging=args.keep_staging,
                autocreate_pairs=args.autocreate_store_product,
                verbose=args.verbose)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
