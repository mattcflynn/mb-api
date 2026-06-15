#!/usr/bin/env python3
"""
scrape_nutrition.py
-------------------
Fetches Taco Bell nutrition data from the Nutritionix menu page and writes
nutrition-extract.csv (same schema as the manual extract).

Usage:
  uv run python scrape_nutrition.py                        # CSV only
  uv run python scrape_nutrition.py --load-db --db macrobell.db  # CSV + load DB
"""

import argparse
import csv
import hashlib
import re
import sys

import requests
from bs4 import BeautifulSoup

NUTRITIONIX_URL = "https://www.nutritionix.com/taco-bell/menu/premium"
OUTPUT_CSV = "nutrition-extract.csv"

CSV_HEADERS = [
    "item_id", "name", "category_nutrition", "is_breakfast", "is_drink",
    "calories", "total_fat", "saturated_fat", "trans_fat", "cholesterol",
    "sodium", "total_carb", "fibers", "sugars", "protein",
]

DRINK_KEYWORDS = {"drink", "beverage", "freeze", "fountain", "agua", "coffee", "tea", "juice", "soda"}
BREAKFAST_KEYWORDS = {"breakfast"}

# Categories to skip entirely (beverages / alcohol)
SKIP_CATEGORIES = {"drinks", "cantina beer, wine and spirits", "fountain beverages"}

# Column positions in the data row (after index 0 = name)
#  1=Cal, 2=TotFat, 3=SatFat, 4=TransFat, 5=Chol, 6=Na, 7=TotCarb, 8=Fiber, 9=Sugar, 10=AddedSugar, 11=Protein
COL = {
    "calories":      1,
    "total_fat":     2,
    "saturated_fat": 3,
    "trans_fat":     4,
    "cholesterol":   5,
    "sodium":        6,
    "total_carb":    7,
    "fibers":        8,
    "sugars":        9,
    # 10 = Added Sugars (skip)
    "protein":       11,
}


def make_item_id(name: str) -> str:
    return hashlib.sha256(name.lower().encode()).hexdigest()[:16]


def parse_value(raw: str) -> float | None:
    s = raw.strip()
    if not s or s == "-":
        return None
    m = re.match(r"<\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)) / 2  # "< 1" → 0.5, "< 5" → 2.5
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def fetch_page(url: str) -> BeautifulSoup:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def scrape_nutritionix() -> list[dict]:
    print(f"Fetching {NUTRITIONIX_URL} ...", flush=True)
    soup = fetch_page(NUTRITIONIX_URL)

    # Print "last updated" if present
    for tag in soup.find_all(string=re.compile(r"last.{0,10}updated", re.I)):
        parent = tag.parent
        if parent:
            full = parent.get_text(strip=True)
            if full and full != "Last Updated:":
                print(f"  Page notes: {full[:120]}")
                break

    table = soup.find("table", class_="tblCompare")
    if not table:
        sys.exit("ERROR: tblCompare not found. Page structure may have changed.")

    rows = []
    current_category = "Unknown"

    for tr in table.find_all("tr"):
        tr_classes = tr.get("class", [])

        # Category header rows
        if "subCategory" in tr_classes:
            cell = tr.find(["td", "th"])
            # Category name is in <h3>; fall back to full cell text if not present
            h3 = cell.find("h3") if cell else None
            current_category = h3.get_text(strip=True) if h3 else (cell.get_text(strip=True) if cell else "Unknown")
            continue

        cells = tr.find_all(["td", "th"])

        # Skip header row (first row, no class, has "Calories" text)
        if not tr_classes:
            continue

        # Item rows have class "odd" or "even"
        if not ("odd" in tr_classes or "even" in tr_classes):
            continue

        if len(cells) < 12:
            continue

        # Name: prefer title attribute of the nmItem link
        name_link = cells[0].find("a", class_="nmItem")
        if name_link:
            name = name_link.get("title") or name_link.get_text(strip=True)
        else:
            name = cells[0].get_text(strip=True)
        if not name:
            continue

        cat_lower = current_category.lower()

        # Skip unwanted beverage/alcohol categories
        if any(cat_lower.startswith(skip) for skip in SKIP_CATEGORIES):
            continue

        is_drink = int(any(kw in cat_lower for kw in DRINK_KEYWORDS))
        is_breakfast = int(any(kw in cat_lower for kw in BREAKFAST_KEYWORDS))

        def g(col_idx):
            return parse_value(cells[col_idx].get_text(strip=True))

        rows.append({
            "item_id":            make_item_id(name),
            "name":               name,
            "category_nutrition": current_category,
            "is_breakfast":       is_breakfast,
            "is_drink":           is_drink,
            "calories":           g(COL["calories"]),
            "total_fat":          g(COL["total_fat"]),
            "saturated_fat":      g(COL["saturated_fat"]),
            "trans_fat":          g(COL["trans_fat"]),
            "cholesterol":        g(COL["cholesterol"]),
            "sodium":             g(COL["sodium"]),
            "total_carb":         g(COL["total_carb"]),
            "fibers":             g(COL["fibers"]),
            "sugars":             g(COL["sugars"]),
            "protein":            g(COL["protein"]),
        })

    return rows


def write_csv(rows: list[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def load_db(db_path: str, rows: list[dict]):
    """Upsert scraped rows directly into nutrition_items. Bypasses db_setup.py
    to avoid touching product_nutrition_map with stale data from other CSVs."""
    from macrobell.db import connect
    conn = connect(db_path)
    try:
        conn.executemany("""
            INSERT INTO nutrition_items (
              item_id, name, category_nutrition, is_breakfast, is_drink,
              calories, total_fat, saturated_fat, trans_fat, cholesterol,
              sodium, total_carb, fibers, sugars, protein
            ) VALUES (:item_id,:name,:category_nutrition,:is_breakfast,:is_drink,
                      :calories,:total_fat,:saturated_fat,:trans_fat,:cholesterol,
                      :sodium,:total_carb,:fibers,:sugars,:protein)
            ON CONFLICT(item_id) DO UPDATE SET
              name               = excluded.name,
              category_nutrition = excluded.category_nutrition,
              is_breakfast       = excluded.is_breakfast,
              is_drink           = excluded.is_drink,
              calories           = excluded.calories,
              total_fat          = excluded.total_fat,
              saturated_fat      = excluded.saturated_fat,
              trans_fat          = excluded.trans_fat,
              cholesterol        = excluded.cholesterol,
              sodium             = excluded.sodium,
              total_carb         = excluded.total_carb,
              fibers             = excluded.fibers,
              sugars             = excluded.sugars,
              protein            = excluded.protein
        """, rows)
        conn.commit()
        print(f"[done] upserted {len(rows)} rows into nutrition_items")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="Scrape Taco Bell nutrition from Nutritionix")
    ap.add_argument("--out", default=OUTPUT_CSV, help="Output CSV path")
    ap.add_argument("--load-db", action="store_true", help="Load scraped data into DB via db_setup.py")
    ap.add_argument("--db", default="macrobell.db", help="DB path (used with --load-db)")
    args = ap.parse_args()

    rows = scrape_nutritionix()

    if not rows:
        sys.exit("ERROR: Scraped 0 rows. Page structure may have changed — inspect manually.")

    print(f"Scraped {len(rows)} items.")
    write_csv(rows, args.out)

    if args.load_db:
        load_db(args.db, rows)


if __name__ == "__main__":
    main()
