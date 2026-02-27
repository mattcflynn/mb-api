#!/usr/bin/env python3
"""
relink.py — Improved nutrition linker for MacroBell.

Reads products + nutrition_items from macrobell.db, skips already-confident
links (>=0.80), applies category-aware pre-filtering, and uses improved
Jaccard scoring to link the remaining ~333 eligible products.

Outputs:
  - Updated product_nutrition_map rows in macrobell.db
  - link_review_needed_v2.csv (items that couldn't be auto-linked)
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import html
import re

from macrobell.config import DEFAULT_DB, SIZE_WORDS, STOPWORDS
from macrobell.db import connect
from macrobell.normalize import normalize_name

DB_PATH = DEFAULT_DB
OVERRIDES_CSV = "link_overrides.csv"
REVIEW_OUT = "link_review_needed_v2.csv"

# Nutrition categories to exclude (junk/noise)
JUNK_CATEGORIES = {
    "Test Items",
    "Cantina Beer, Wine and Spirits",
    "Freeze Test Items",
    "Side Portions - Hidden",
    "JSON only",
    "Drinks",
}

# Products in these menu categories are excluded
EXCLUDED_MENU_CATEGORIES = {"deals-and-combos", "party-packs"}

# Tokens to strip before scoring (size + quantity + unit words)
QUANTITY_WORDS = {"2", "3", "4", "5", "6", "12", "pack", "box", "combo", "party"}
UNIT_WORDS = {"oz", "fl", "ml", "lb", "lbs", "piece", "pieces", "serves", "serving"}
STRIP_TOKENS = STOPWORDS | SIZE_WORDS | QUANTITY_WORDS | UNIT_WORDS


def clean_nutrition_name(s: str) -> str:
    """Decode HTML entities and strip HTML tags from nutrition item names."""
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return s

# Category-aware pre-filter: menu_category → function(category_nutrition) → bool
CATEGORY_FILTER: dict[str, callable] = {
    "breakfast": lambda c: "breakfast" in c.lower(),
    "tacos": lambda c: "taco" in c.lower(),
    "burritos": lambda c: "burrito" in c.lower(),
    "nachos": lambda c: "nacho" in c.lower(),
    "quesadillas": lambda c: "quesadilla" in c.lower(),
    "sides-sweets": lambda c: any(k in c.lower() for k in ("side", "sweet", "dessert", "cinnabon")),
    "cantina-chicken-menu": lambda c: "cantina" in c.lower(),
    "bowls": lambda c: "bowl" in c.lower() or "specialty" in c.lower(),
}

HIGH_CONF = 0.80   # threshold when multiple candidates exist
LOW_CONF = 0.70    # threshold when only one candidate in filtered set
MIN_SCORE = 0.50   # below this → send to review regardless


def tokset(s: str) -> frozenset[str]:
    toks = normalize_name(s).split()
    # strip single-char tokens (e.g., 'v' from '(V)', 'r' from '&reg;' residue) and stop tokens
    return frozenset(t for t in toks if t not in STRIP_TOKENS and len(t) > 1)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def load_overrides(path: str) -> dict[str, str]:
    """Load product_code → item_id overrides from CSV."""
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    with open(p) as f:
        for row in csv.DictReader(f):
            pc = str(row.get("product_code", "")).strip()
            iid = str(row.get("item_id", "")).strip()
            if pc and iid:
                out[pc] = iid
    return out


def main() -> None:
    conn = connect(DB_PATH)
    cur = conn.cursor()

    # ── 1. Load candidate products (unlinked or low-confidence) ──────────
    cur.execute("""
        SELECT p.canonical_product_id, p.product_code, p.base_name, p.category
        FROM products p
        LEFT JOIN product_nutrition_map m ON p.canonical_product_id = m.canonical_product_id
        WHERE p.us_active = 1
          AND p.is_drink = 0
          AND p.category NOT IN ('deals-and-combos', 'party-packs')
          AND (m.canonical_product_id IS NULL OR m.match_confidence < 0.80)
        ORDER BY p.category, p.base_name
    """)
    products = cur.fetchall()
    print(f"Candidate products to link: {len(products)}")

    # ── 2. Load usable nutrition items ───────────────────────────────────
    cur.execute("""
        SELECT item_id, name, category_nutrition
        FROM nutrition_items
        WHERE category_nutrition NOT IN ({})
    """.format(",".join("?" * len(JUNK_CATEGORIES))), list(JUNK_CATEGORIES))
    nut_rows = cur.fetchall()
    print(f"Usable nutrition items: {len(nut_rows)}")

    # Build nutrition lookup structures
    nut_list = []
    for item_id, name, cat_nut in nut_rows:
        clean = clean_nutrition_name(name)
        norm = normalize_name(clean)
        toks = tokset(clean)
        nut_list.append({
            "item_id": str(item_id),
            "name": name,
            "name_norm": norm,
            "category_nutrition": cat_nut or "",
            "toks": toks,
        })

    nut_by_id = {n["item_id"]: n for n in nut_list}

    # ── 3. Load overrides (product_code → item_id) ───────────────────────
    overrides = load_overrides(OVERRIDES_CSV)

    # ── 4. Score & link ──────────────────────────────────────────────────
    linked: list[tuple] = []     # (cpid, item_id, confidence, method)
    review: list[dict] = []

    for cpid, prod_code, base_name, menu_cat in products:
        base_norm = normalize_name(base_name)
        prod_toks = tokset(base_name)

        # Override wins outright
        if prod_code in overrides:
            iid = overrides[prod_code]
            if iid in nut_by_id:
                linked.append((cpid, iid, 1.0, "override"))
                continue
            # Override points to missing item — fall through

        # Category-aware candidate filtering
        cat_filter = CATEGORY_FILTER.get(menu_cat)
        if cat_filter:
            candidates = [n for n in nut_list if cat_filter(n["category_nutrition"])]
            # Fall back to all if filter yields nothing
            if not candidates:
                candidates = nut_list
        else:
            candidates = nut_list

        if not candidates:
            review.append({
                "canonical_product_id": cpid,
                "product_code": prod_code,
                "base_name": base_name,
                "menu_category": menu_cat,
                "reason": "no_candidates",
                "best_item_id": "",
                "best_name": "",
                "best_score": "",
            })
            continue

        # Score all candidates
        scored = []
        for n in candidates:
            sim = jaccard(prod_toks, n["toks"])
            # Substring containment boost (normalized names)
            if base_norm in n["name_norm"] or n["name_norm"] in base_norm:
                sim = min(1.0, sim + 0.10)
            # Product-recall boost: all product tokens appear in nutrition name
            if prod_toks and prod_toks.issubset(n["toks"]):
                sim = min(1.0, sim + 0.15)
            scored.append((sim, n))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]

        # Threshold: lower when only 1 candidate in filtered set
        threshold = LOW_CONF if len(candidates) == 1 else HIGH_CONF

        if best_score >= threshold:
            linked.append((cpid, best["item_id"], round(best_score, 3), "rule"))
        elif best_score >= MIN_SCORE:
            review.append({
                "canonical_product_id": cpid,
                "product_code": prod_code,
                "base_name": base_name,
                "menu_category": menu_cat,
                "reason": "low_confidence",
                "best_item_id": best["item_id"],
                "best_name": best["name"],
                "best_score": round(best_score, 3),
            })
        else:
            review.append({
                "canonical_product_id": cpid,
                "product_code": prod_code,
                "base_name": base_name,
                "menu_category": menu_cat,
                "reason": "no_match",
                "best_item_id": best["item_id"],
                "best_name": best["name"],
                "best_score": round(best_score, 3),
            })

    # ── 5. Upsert into product_nutrition_map ─────────────────────────────
    cur.executemany("""
        INSERT INTO product_nutrition_map
            (canonical_product_id, item_id, match_confidence, match_method, reviewed)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(canonical_product_id) DO UPDATE SET
            item_id = excluded.item_id,
            match_confidence = excluded.match_confidence,
            match_method = excluded.match_method
    """, linked)
    conn.commit()

    # ── 6. Write review file ──────────────────────────────────────────────
    if review:
        fields = ["canonical_product_id", "product_code", "base_name", "menu_category",
                  "reason", "best_item_id", "best_name", "best_score"]
        with open(REVIEW_OUT, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(review)

    # ── 7. Summary ────────────────────────────────────────────────────────
    total = cur.execute("SELECT COUNT(*) FROM product_nutrition_map").fetchone()[0]
    print(f"\nAuto-linked: {len(linked)}")
    print(f"Sent to review: {len(review)} → {REVIEW_OUT}")
    print(f"Total links in DB: {total}")

    conn.close()


if __name__ == "__main__":
    main()
