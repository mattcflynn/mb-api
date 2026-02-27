#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
product_linker.py — MENU-FIRST linking
--------------------------------------
Given:
  - menu_catalog.csv (from code_mapper_all.py)  [canonical_product_id, product_code, base_name, category, is_breakfast, is_drink, ...]
  - nutrition_latest.csv (from nutrition_scraper_latest.py)  [item_id, name, category, is_breakfast, is_drink, ...]
Optionally:
  - link_overrides.csv  [product_code,item_id]  # manual fixes win outright

Outputs:
  - products_master.csv         # ONLY menu items that we linked to a nutrition item
  - link_review_needed.csv      # menu items without a confident match, with best candidate suggestion

Notes:
  - Menu is treated as the source of truth. Orphan nutrition rows are ignored by default.
  - Matching uses normalized tokens + Jaccard with a few domain boosts (e.g., “supreme” alignment).
"""

from __future__ import annotations
import argparse, csv, re
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from macrobell.config import HIGH_CONF, MID_CONF, STOPWORDS, SIZE_WORDS
from macrobell.normalize import normalize_name, normalize_columns

# -------------------------
# Config / thresholds
# -------------------------
KEY_TOKENS  = {"supreme","spicy","fresco","crunchwrap","chalupa","gordita","quesadilla","nacho","nachos",
               "cheesy","veggy","veggie","beef","chicken","steak","bean","black","fiesta","potato","volcano",
               "cantina","freeze","baja","blast","mtn","mountain","dew","cinnamon","twist","cinnabon","delight",
               "cinna","tostada","mexican","pizza","power","bowl","soft","hard","crunchy","doritos","locos",
               "ranch","chipotle","avocado","spicy","mild","fire"}

def core_tokens(s: str) -> List[str]:
    toks = [t for t in normalize_name(s).split()
            if t not in STOPWORDS and t not in SIZE_WORDS]
    return toks

def tokset(s: str) -> set:
    return set(core_tokens(s))

def jaccard(a: set, b: set) -> float:
    if not a and not b: return 1.0
    if not a or not b:  return 0.0
    inter = len(a & b); union = len(a | b)
    return inter / union if union else 0.0

def key_alignment(a: set, b: set) -> bool:
    ka = a & KEY_TOKENS
    kb = b & KEY_TOKENS
    # Either share a key token or neither mentions any key token
    return (ka == kb) or (len(ka & kb) > 0) or (not ka and not kb)

def soft_filter_by_flags(df: pd.DataFrame, is_bf: int, is_dr: int) -> pd.DataFrame:
    # Keep nutrition rows whose flags match menu flags (or are unspecified)
    if "is_breakfast" in df.columns:
        df = df[(df["is_breakfast"].fillna(0).astype(int) == int(is_bf))]
    if "is_drink" in df.columns:
        df = df[(df["is_drink"].fillna(0).astype(int) == int(is_dr))]
    return df

# -------------------------
# IO helpers
# -------------------------
def read_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    return normalize_columns(df)

def load_overrides(path: str | None) -> Dict[str, str]:
    if not path or not Path(path).exists():
        return {}
    o: Dict[str,str] = {}
    df = read_csv(path)
    for _, r in df.iterrows():
        pc = str(r.get("product_code","")).strip()
        it = str(r.get("item_id","")).strip()
        if pc and it:
            o[pc] = it
    return o

# -------------------------
# Main linking
# -------------------------
def link_menu_to_nutrition(menu: pd.DataFrame, nut: pd.DataFrame, overrides: Dict[str,str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    # Prep normalized columns
    menu = menu.copy()
    nut  = nut.copy()

    # Ensure expected columns exist
    for col in ("base_name","product_code","canonical_product_id"):
        if col not in menu.columns:
            raise SystemExit(f"menu_catalog.csv is missing required column '{col}'")
    if "name" not in nut.columns or "item_id" not in nut.columns:
        raise SystemExit("nutrition_latest.csv must include 'item_id' and 'name'")

    # Normalize & tokens
    menu["base_name_norm"] = menu["base_name"].map(normalize_name)
    menu["tokens"] = menu["base_name_norm"].map(tokset)
    menu["is_breakfast"] = menu.get("is_breakfast", 0).fillna(0).astype(int)
    menu["is_drink"] = menu.get("is_drink", 0).fillna(0).astype(int)

    nut["name_norm"] = nut["name"].map(normalize_name)
    nut["tokens"]    = nut["name_norm"].map(tokset)
    nut["is_breakfast"] = nut.get("is_breakfast", 0).fillna(0).astype(int)
    nut["is_drink"] = nut.get("is_drink", 0).fillna(0).astype(int)
    # Some scrapers call it category; unify to category_nutrition for clarity
    if "category" in nut.columns and "category_nutrition" not in nut.columns:
        nut = nut.rename(columns={"category": "category_nutrition"})

    matched_rows: List[dict] = []
    review_rows:  List[dict] = []

    # Build a quick index for overrides
    nut_by_item = {str(r["item_id"]): r for _, r in nut.iterrows()}

    for _, m in menu.iterrows():
        prod_code = str(m["product_code"])
        cpid      = str(m["canonical_product_id"])
        base      = m["base_name"]
        toks_m    = m["tokens"]
        category  = m.get("category", "")
        is_bf     = int(m["is_breakfast"])
        is_dr     = int(m["is_drink"])

        # 1) Overrides win outright
        if prod_code in overrides:
            item_id = overrides[prod_code]
            nrow = nut_by_item.get(item_id)
            if nrow is not None:
                matched_rows.append({
                    "canonical_product_id": cpid,
                    "product_code": prod_code,
                    "item_id": item_id,
                    "category": category,
                    "name_nutrition": nrow["name"],
                    "category_nutrition": nrow.get("category_nutrition",""),
                    "match_confidence": 1.0,
                    "match_method": "override",
                })
                continue  # next menu item
            # if override points to missing item_id, fall through to normal matching

        # 2) Candidate pool (soft-filter by flags)
        cand = soft_filter_by_flags(nut, is_bf, is_dr).copy()
        # Compute similarity
        cand["sim"] = cand["tokens"].apply(lambda t: jaccard(t, toks_m))

        # Small domain boosts:
        # - exact substring boost (menu base in nutrition or vice versa)
        base_norm = m["base_name_norm"]
        contains_mask = cand["name_norm"].str.contains(re.escape(base_norm), regex=True) | \
                        pd.Series([base_norm in n for n in cand["name_norm"]], index=cand.index)
        cand.loc[contains_mask, "sim"] = cand.loc[contains_mask, "sim"] + 0.10

        # pick best
        cand = cand.sort_values("sim", ascending=False)
        if cand.empty or cand.iloc[0]["sim"] < 0.05:
            # nothing remotely close
            review_rows.append({
                "canonical_product_id": cpid,
                "product_code": prod_code,
                "base_name": base,
                "category": category,
                "is_breakfast": is_bf,
                "is_drink": is_dr,
                "reason": "no_candidates",
                "top_candidate_item_id": "",
                "top_candidate_name": "",
                "top_candidate_sim": ""
            })
            continue

        best = cand.iloc[0]
        best_tokens = set(best["tokens"])
        conf = float(best["sim"])
        aligned = key_alignment(toks_m, best_tokens)

        if conf >= HIGH_CONF or (conf >= MID_CONF and aligned):
            matched_rows.append({
                "canonical_product_id": cpid,
                "product_code": prod_code,
                "item_id": best["item_id"],
                "category": category,
                "name_nutrition": best["name"],
                "category_nutrition": best.get("category_nutrition",""),
                "match_confidence": round(conf, 3),
                "match_method": ("rule" if conf >= HIGH_CONF else "rule_lo"),
            })
        else:
            review_rows.append({
                "canonical_product_id": cpid,
                "product_code": prod_code,
                "base_name": base,
                "category": category,
                "is_breakfast": is_bf,
                "is_drink": is_dr,
                "reason": "low_confidence",
                "top_candidate_item_id": best["item_id"],
                "top_candidate_name": best["name"],
                "top_candidate_sim": round(conf, 3),
            })

    return pd.DataFrame(matched_rows), pd.DataFrame(review_rows)


# -------------------------
# CLI
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog",   default="menu_catalog.csv")
    ap.add_argument("--nutrition", default="nutrition_latest.csv")
    ap.add_argument("--overrides", default="link_overrides.csv")
    ap.add_argument("--out-master", default="products_master.csv")
    ap.add_argument("--out-review", default="link_review_needed.csv")
    args = ap.parse_args()

    menu = read_csv(args.catalog)
    nut  = read_csv(args.nutrition)
    overrides = load_overrides(args.overrides)

    matched, review = link_menu_to_nutrition(menu, nut, overrides)

    matched.to_csv(args.out_master, index=False)
    review.to_csv(args.out_review, index=False)

    print(f"[done] linked {len(matched)} menu items → {args.out_master}")
    print(f"[review] {len(review)} menu items need review → {args.out_review}")
    if len(review):
        # quick hint for overrides
        print("Tip: add rows to link_overrides.csv as 'product_code,item_id' and re-run.")

if __name__ == "__main__":
    main()
