#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
product_linker.py
Deterministically link menu_catalog.csv (product_code) to nutrition_latest.csv (item_id).

Inputs:
  --catalog menu_catalog.csv
  --nutrition nutrition_latest.csv
  --overrides link_overrides.csv  (optional, columns: product_code,item_id)

Outputs:
  products_master.csv
  link_review_needed.csv
"""

import argparse, csv, re
from pathlib import Path
import pandas as pd

STOPWORDS = {"the","a","and","with","of","for"}
SIZE_WORDS = {"large","medium","small","grande","mini","double","triple","party","pack","box","combo"}
MARKS = r"[®™()]"

def normalize_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(MARKS, "", s)
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def core_tokens(s: str) -> set[str]:
    toks = [t for t in normalize_name(s).split() if t not in STOPWORDS and t not in SIZE_WORDS]
    return set(toks)

def jaccard(a: set, b: set) -> float:
    if not a and not b: return 1.0
    if not a or not b: return 0.0
    inter = len(a & b); union = len(a | b)
    return inter / union if union else 0.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="menu_catalog.csv")
    ap.add_argument("--nutrition", default="nutrition_latest.csv")
    ap.add_argument("--overrides", default="link_overrides.csv")
    ap.add_argument("--out-master", default="products_master.csv")
    ap.add_argument("--out-review", default="link_review_needed.csv")
    args = ap.parse_args()

    cat = pd.read_csv(args.catalog)
    nut = pd.read_csv(args.nutrition)

    # Prep
    cat["base_name_norm"] = cat["base_name"].apply(normalize_name)
    nut["name_norm"] = nut["name"].apply(normalize_name)
    nut["tokens"] = nut["name_norm"].apply(core_tokens)
    cat["tokens"] = cat["base_name_norm"].apply(core_tokens)

    # Optional overrides
    overrides = {}
    if Path(args.overrides).exists():
        ov = pd.read_csv(args.overrides)
        for _, r in ov.iterrows():
            overrides[str(r["product_code"]).strip()] = str(r["item_id"]).strip()

    rows_master, rows_review = [], []

    # Fast blocking by simple token overlap + flags
    for _, r in cat.iterrows():
        code = r["product_code"]
        base = r["base_name"]
        cat_name = r.get("category","") or ""
        is_bf = int(r.get("is_breakfast",0) or 0)
        is_dr = int(r.get("is_drink",0) or 0)
        toks = r["tokens"]

        # Overrides win outright
        if code in overrides:
            match = nut[nut["item_id"].astype(str) == str(overrides[code])]
            if not match.empty:
                m = match.iloc[0]
                rows_master.append({
                    **r.to_dict(),
                    "item_id": m["item_id"],
                    "name_nutrition": m["name"],
                    "category_nutrition": m.get("category",""),
                    "match_confidence": 1.0,
                    "match_method": "override",
                })
                continue

        cand = nut.copy()
        # breakfast/drink alignment
        cand = cand[(cand.get("is_breakfast",0)==is_bf) & (cand.get("is_drink",0)==is_dr)]
        # token overlap block
        cand["sim"] = cand["tokens"].apply(lambda t: jaccard(t, toks))
        cand = cand[cand["sim"] > 0.25]  # coarse block

        if cand.empty:
            rows_review.append({**r.to_dict(), "reason":"no_candidates"})
            continue

        best = cand.sort_values("sim", ascending=False).iloc[0]
        conf = float(best["sim"])

        if conf >= 0.80:
            rows_master.append({
                **r.to_dict(),
                "item_id": best["item_id"],
                "name_nutrition": best["name"],
                "category_nutrition": best.get("category",""),
                "match_confidence": round(conf,3),
                "match_method": "rule",
            })
        elif conf >= 0.60 and (("supreme" in toks) == ("supreme" in set(best["tokens"]))):
            rows_master.append({
                **r.to_dict(),
                "item_id": best["item_id"],
                "name_nutrition": best["name"],
                "category_nutrition": best.get("category",""),
                "match_confidence": round(conf,3),
                "match_method": "rule_lo",
            })
        else:
            rr = r.to_dict()
            rr["top_candidate_item_id"] = best["item_id"]
            rr["top_candidate_name"] = best["name"]
            rr["top_candidate_sim"] = round(conf,3)
            rows_review.append(rr)

    # Write outputs
    pd.DataFrame(rows_master).to_csv(args.out_master, index=False)
    pd.DataFrame(rows_review).to_csv(args.out_review, index=False)

    print(f"[done] linked {len(rows_master)} products → {args.out_master}")
    print(f"[review] {len(rows_review)} rows need manual review → {args.out_review}")

if __name__ == "__main__":
    main()
