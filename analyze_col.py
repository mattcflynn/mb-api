#!/usr/bin/env python3
"""
analyze_col.py
--------------
Correlates state-level Taco Bell pricing with cost-of-living metrics:
  - Median household income (Census ACS 2022)
  - Median gross rent (Census ACS 2022)
  - State minimum wage (DOL/NCSL 2025)

Usage: uv run python analyze_col.py
"""

import requests
import pandas as pd
from scipy import stats
from macrobell.db import connect

# State minimum wages effective early 2025 (federal floor $7.25 where no state law)
MIN_WAGES = {
    "AL":7.25,"AK":11.73,"AZ":14.35,"AR":11.00,"CA":16.50,"CO":14.81,"CT":16.35,
    "DE":15.00,"FL":13.00,"GA":7.25,"HI":14.00,"ID":7.25,"IL":14.00,"IN":7.25,
    "IA":7.25,"KS":7.25,"KY":7.25,"LA":7.25,"ME":14.65,"MD":15.00,"MA":15.00,
    "MI":10.33,"MN":10.85,"MS":7.25,"MO":12.30,"MT":10.30,"NE":13.50,"NV":12.00,
    "NH":7.25,"NJ":15.49,"NM":12.00,"NY":16.00,"NC":7.25,"ND":7.25,"OH":10.45,
    "OK":7.25,"OR":14.70,"PA":7.25,"RI":14.00,"SC":7.25,"SD":11.20,"TN":7.25,
    "TX":7.25,"UT":7.25,"VT":13.67,"VA":12.00,"WA":16.28,"WV":8.75,"WI":7.25,
    "WY":5.15,"DC":17.50,
}

STATE_NAMES = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
    "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA",
    "Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS",
    "Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD","Massachusetts":"MA",
    "Michigan":"MI","Minnesota":"MN","Mississippi":"MS","Missouri":"MO","Montana":"MT",
    "Nebraska":"NE","Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM",
    "New York":"NY","North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK",
    "Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC",
    "South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT",
    "Virginia":"VA","Washington":"WA","West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY",
    "District of Columbia":"DC",
}


def get_tb_prices() -> pd.DataFrame:
    c = connect("macrobell.db")
    rows = c.execute("""
        WITH latest AS (
            SELECT p.store_id, p.canonical_product_id, p.price_cents
            FROM prices p
            WHERE collected_at = (
                SELECT MAX(p2.collected_at) FROM prices p2
                WHERE p2.store_id=p.store_id
                  AND p2.canonical_product_id=p.canonical_product_id
            )
            AND price_cents >= 100 AND price_cents <= 1500
        )
        SELECT s.state,
               COUNT(DISTINCT l.store_id) AS n_stores,
               ROUND(AVG(l.price_cents)/100.0, 4) AS avg_price,
               ROUND(AVG(CASE WHEN pr.base_name='crunchy taco'
                              THEN l.price_cents END)/100.0, 4) AS crunchy_taco_avg
        FROM latest l
        JOIN stores s ON l.store_id = s.store_id
        JOIN products pr ON l.canonical_product_id = pr.canonical_product_id
        WHERE pr.category NOT IN ('deals-and-combos','party-packs','passes','online-exclusives')
          AND s.state IS NOT NULL
        GROUP BY s.state
        HAVING n_stores >= 10
        ORDER BY avg_price DESC
    """).fetchall()
    c.close()
    df = pd.DataFrame(rows, columns=["state","n_stores","avg_price","crunchy_taco_avg"])
    df["state"] = df["state"].str.upper()
    return df


def get_census_data() -> pd.DataFrame:
    """ACS 5-year 2022: B19013=median household income, B25064=median gross rent."""
    url = ("https://api.census.gov/data/2022/acs/acs5"
           "?get=NAME,B19013_001E,B25064_001E&for=state:*")
    data = requests.get(url, timeout=30).json()
    rows = []
    for row in data[1:]:
        name, income, rent, _ = row
        abbrev = STATE_NAMES.get(name)
        if abbrev:
            rows.append({
                "state":         abbrev,
                "median_income": int(income) if income not in (None, "-1") else None,
                "median_rent":   int(rent)   if rent   not in (None, "-1") else None,
            })
    return pd.DataFrame(rows)


def correlate(df: pd.DataFrame, x_col: str, y_col: str, label: str):
    sub = df[[x_col, y_col]].dropna()
    if len(sub) < 10:
        return
    r,  p  = stats.pearsonr(sub[x_col], sub[y_col])
    rho, p2 = stats.spearmanr(sub[x_col], sub[y_col])
    sig  = "***" if p  < 0.001 else ("**" if p  < 0.01 else ("*" if p  < 0.05 else "  "))
    sig2 = "***" if p2 < 0.001 else ("**" if p2 < 0.01 else ("*" if p2 < 0.05 else "  "))
    print(f"  {label:32s}  r={r:+.3f}{sig}  ρ={rho:+.3f}{sig2}  n={len(sub)}")


def main():
    print("Loading TB prices from DB...")
    tb = get_tb_prices()

    print("Fetching Census ACS 2022 data...")
    census = get_census_data()

    df = tb.merge(census, on="state", how="left")
    df["min_wage"] = df["state"].map(MIN_WAGES)

    print(f"\n{len(df)} states in analysis\n")

    hdr = "(* p<0.05  ** p<0.01  *** p<0.001)"
    print("=" * 65)
    print(f"CORRELATIONS WITH AVG TB PRICE (all items)  {hdr}")
    print("=" * 65)
    correlate(df, "median_income", "avg_price", "Median household income")
    correlate(df, "median_rent",   "avg_price", "Median gross rent")
    correlate(df, "min_wage",      "avg_price", "State minimum wage")

    print()
    print("=" * 65)
    print("CORRELATIONS WITH CRUNCHY TACO PRICE (single item)")
    print("=" * 65)
    correlate(df, "median_income", "crunchy_taco_avg", "Median household income")
    correlate(df, "median_rent",   "crunchy_taco_avg", "Median gross rent")
    correlate(df, "min_wage",      "crunchy_taco_avg", "State minimum wage")

    print()
    print("=" * 75)
    print(f"{'ST':2}  {'Stores':>6}  {'AvgPrice':>8}  {'CrunchyTaco':>11}  "
          f"{'Income':>8}  {'Rent':>6}  {'MinWage':>7}")
    print("-" * 75)
    for _, r in df.sort_values("avg_price", ascending=False).iterrows():
        inc  = f"${r.median_income/1000:5.1f}k" if pd.notna(r.median_income) else "      ?"
        rent = f"${r.median_rent:4.0f}"          if pd.notna(r.median_rent)   else "    ?"
        wage = f"${r.min_wage:4.2f}"             if pd.notna(r.min_wage)      else "    ?"
        ct   = f"${r.crunchy_taco_avg:5.2f}"     if pd.notna(r.crunchy_taco_avg) else "     ?"
        print(f"{r.state:2}  {r.n_stores:6.0f}  ${r.avg_price:6.2f}    {ct}        "
              f"{inc}   {rent}   {wage}")


if __name__ == "__main__":
    main()
