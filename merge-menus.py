#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_menus.py
---------------
Merge all per-chunk menu_catalog_*.csv and store_products_*.csv files
into single, deduplicated CSVs for downstream linking and DB setup.

Features:
  - supports incremental updates via --append
  - drops duplicates on the right keys
  - prints summary stats so you can tell if a chunk "took"

Outputs:
  - menu_catalog.csv
  - store_products.csv
"""

import argparse
from pathlib import Path

import pandas as pd


def merge_csvs(pattern: str, drop_dupes_on=None, output_name="merged.csv", append=False):
    files = sorted(Path(".").glob(pattern))
    if not files:
        print(f"[warn] No files found matching {pattern}")
        return None

    print(f"[info] Merging {len(files)} files matching {pattern}")
    dfs = [pd.read_csv(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)

    # If appending, also pull in the already-combined file
    if append and Path(output_name).exists():
        existing = pd.read_csv(output_name)
        print(f"[append] Including existing {output_name} with {len(existing)} rows")
        df = pd.concat([existing, df], ignore_index=True)

    if drop_dupes_on:
        before = len(df)
        df = df.drop_duplicates(subset=drop_dupes_on)
        after = len(df)
        print(f"[dedupe] Dropped {before - after} duplicate rows on {drop_dupes_on}")

    df.to_csv(output_name, index=False)
    print(f"[done] Wrote {len(df)} rows → {output_name}")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", action="store_true", help="Append to existing combined files")
    args = ap.parse_args()

    # 1) Merge menus
    menu_df = merge_csvs(
        pattern="menu_catalog_*.csv",
        drop_dupes_on=["product_code"],
        output_name="menu_catalog.csv",
        append=args.append,
    )

    # 2) Merge store-product availability
    sp_df = merge_csvs(
        pattern="store_products_*.csv",
        drop_dupes_on=["store_id", "product_code"],
        output_name="store_products.csv",
        append=args.append,
    )

    # 3) Sanity stats
    print("\n=== Sanity check ===")
    if menu_df is not None:
        print(f"[menu] unique menu items (by product_code): {menu_df['product_code'].nunique()}")
    else:
        print("[menu] no menu files merged.")

    if sp_df is not None:
        # unique stores, unique products, total links
        uniq_stores = sp_df["store_id"].nunique()
        uniq_products = sp_df["product_code"].nunique()
        total_links = len(sp_df)
        print(f"[store_products] unique stores: {uniq_stores}")
        print(f"[store_products] unique products (seen in stores): {uniq_products}")
        print(f"[store_products] total store-product links: {total_links}")
    else:
        print("[store_products] no store-products files merged.")


if __name__ == "__main__":
    main()