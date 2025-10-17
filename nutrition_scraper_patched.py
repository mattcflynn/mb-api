#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Taco Bell Nutrition Scraper — Auto-versioned + Breakfast/Drinks Flags
---------------------------------------------------------------------
- Discovers the current calculator JSON URL from Taco Bell's nutrition page
- Falls back to a known CloudFront URL if discovery fails
- Computes per-item nutrition using serving_weight_grams
- Adds `is_breakfast` and `is_drink` columns
"""

import json, gzip, re, csv, argparse, requests
from pathlib import Path
from urllib.parse import urljoin

NUTRITION_LANDING_URL = "https://www.tacobell.com/nutrition/info"
FALLBACK_JSON_GZ = "https://d2eawub7utcl6.cloudfront.net/calculator/10197-0-1760620706.json.gz"
CALC_JSON_GZ_PATTERN = re.compile(r"calculator/\d+-0-\d+\.json\.gz")

NUTRIENT_FIELDS = [
    "calories","fat_calories","total_fat","saturated_fat","trans_fat",
    "polyunsaturated_fat","monounsaturated_fat","cholesterol","sodium",
    "total_carb","fibers","sugars","protein",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NutritionScraper/1.2)"}


def discover_latest_url(session: requests.Session) -> str:
    try:
        r = session.get(NUTRITION_LANDING_URL, timeout=20)
        r.raise_for_status()
        m = CALC_JSON_GZ_PATTERN.search(r.text)
        if m:
            path = m.group(0)
            full_match = re.search(r"https?://[^\"']*" + re.escape(path), r.text)
            if full_match:
                return full_match.group(0)
            return urljoin(NUTRITION_LANDING_URL, path)
    except Exception:
        pass
    return FALLBACK_JSON_GZ


def fetch_json(session: requests.Session, url: str) -> dict:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    raw = r.content
    if raw[:1] == b"{":  # plain JSON
        return json.loads(raw.decode("utf-8"))
    try:
        data = gzip.decompress(raw)
    except OSError:
        data = raw
    return json.loads(data.decode("utf-8"))


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def per_100g_factor(ing, w):
    denom = safe_float(ing.get("serving_weight"), 100.0)
    return w / (denom or 100.0)


def compute_item(defaults, ingredients):
    totals = {f: 0.0 for f in NUTRIENT_FIELDS}
    total_w = 0.0
    for e in defaults.values():
        ing_id = str(e.get("ingredient_id"))
        w = safe_float(e.get("serving_weight_grams"))
        total_w += w
        ing = ingredients.get(ing_id)
        if not ing:
            continue
        factor = per_100g_factor(ing, w)
        for f in NUTRIENT_FIELDS:
            val = ing.get(f)
            if isinstance(val, (int, float)):
                totals[f] += float(val) * factor
    return total_w, totals


def build_defaults(calc, item_id, template_id):
    defaults = calc.get("itemDefaultIngredients", {}).get(str(item_id))
    if defaults:
        return defaults
    # fallback minimal set (uses correct 'ingredient_id' key)
    assembled = {}
    t_groups = calc.get("templateGroups", {}).get(str(template_id), [])
    order = 0
    for g in t_groups:
        gid = str(g.get("group_id"))
        g_list = calc.get("groupIngredients", {}).get(gid, [])
        if not g_list:
            continue
        ref = g_list[0]
        ing_id = str(ref.get("ingredient_id"))
        order += 1
        assembled[ing_id] = {
            "ingredient_id": int(ing_id),
            "serving_weight_grams": 0.0,
            "group_order": order,
            "name": calc["ingredients"].get(ing_id, {}).get("name", ""),
        }
    return assembled


def main(out_csv):
    s = requests.Session(); s.headers.update(HEADERS)
    url = discover_latest_url(s)
    print(f"[info] Using calculator: {url}")
    data = fetch_json(s, url)
    calc = data["calculator"]

    items = calc["items"]
    ingredients = calc["ingredients"]
    categories = {str(k): v.get("name", "") for k, v in calc.get("categories", {}).items()}

    # Identify breakfast and drinks categories from names
    breakfast_ids = {cid for cid, name in categories.items()
                     if "breakfast" in (name or "").lower()}
    drinks_ids = {cid for cid, name in categories.items()
                  if any(tok in (name or "").lower() for tok in ("drink", "beverage", "freeze"))}

    rows = []
    for item_id, item in items.items():
        name = item.get("name", "")
        cat_id = str(item.get("category_id", ""))
        category = categories.get(cat_id, "")
        template_id = item.get("template_id")

        defaults = build_defaults(calc, item_id, template_id)
        total_w, totals = compute_item(defaults, ingredients)

        row = {
            "item_id": item_id,
            "name": name,
            "category": category,
            "is_breakfast": 1 if cat_id in breakfast_ids else 0,
            "is_drink": 1 if cat_id in drinks_ids else 0,
            "serving_weight_grams": round(total_w, 2),
        }
        for f in NUTRIENT_FIELDS:
            row[f] = round(totals.get(f, 0.0), 2)
        rows.append(row)

    fields = ["item_id", "name", "category", "is_breakfast", "is_drink", "serving_weight_grams"] + NUTRIENT_FIELDS
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"[done] Wrote {len(rows)} rows → {out_csv}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="nutrition_latest.csv")
    args = p.parse_args()
    main(args.out)
