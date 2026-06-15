"""
report.py — Write a human-readable Markdown summary after deploy.sh runs.

Usage (called by deploy.sh):
    uv run python report.py --log logs/deploy_20260615.log \
        --out logs/latest_report.md --failed "nutrition relink"
"""
from __future__ import annotations
import argparse
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from macrobell.db import connect

NUTRITION_CATEGORIES = (
    "Cantina Chicken Menu", "Tacos", "Burritos",
    "Nachos", "Quesadillas", "Specialties",
)
MIN_PRICE = 100
SIGNIFICANT_PCT = 3.0   # flag items whose national avg moved >= this %
CLOSED_WINDOW_DAYS = 45  # stores not scraped for this many days are flagged


def current_avg(db: sqlite3.Connection, cid: str) -> float | None:
    row = db.execute("""
        SELECT AVG(p.price_cents) FROM prices p
        JOIN (
            SELECT store_id, MAX(collected_at) AS ts
            FROM prices WHERE canonical_product_id = ? AND price_cents >= ?
            GROUP BY store_id
        ) m ON p.store_id = m.store_id AND p.collected_at = m.ts
        WHERE p.canonical_product_id = ? AND p.price_cents >= ?
    """, (cid, MIN_PRICE, cid, MIN_PRICE)).fetchone()
    return row[0] if row else None


def prev_avg(db: sqlite3.Connection, cid: str, today: str) -> float | None:
    row = db.execute("""
        SELECT AVG(p.price_cents) FROM prices p
        JOIN (
            SELECT store_id, MAX(collected_at) AS ts
            FROM prices
            WHERE canonical_product_id = ? AND price_cents >= ?
              AND collected_at < ?
            GROUP BY store_id
        ) m ON p.store_id = m.store_id AND p.collected_at = m.ts
        WHERE p.canonical_product_id = ? AND p.price_cents >= ?
          AND p.collected_at < ?
    """, (cid, MIN_PRICE, today, cid, MIN_PRICE, today)).fetchone()
    return row[0] if row else None


def site_items(db: sqlite3.Connection) -> list[tuple[str, str]]:
    ph = ",".join("?" * len(NUTRITION_CATEGORIES))
    return db.execute(f"""
        SELECT DISTINCT n.name, p.canonical_product_id
        FROM nutrition_items n
        JOIN product_nutrition_map pnm ON pnm.item_id = n.item_id
        JOIN products p ON p.canonical_product_id = pnm.canonical_product_id
        WHERE n.category_nutrition IN ({ph})
          AND n.protein > 0 AND p.us_active = 1
          AND pnm.match_confidence >= 0.80
        ORDER BY n.name
    """, NUTRITION_CATEGORIES).fetchall()


def fmt_money(cents: float) -> str:
    return f"${cents / 100:.2f}"


def parse_log(log_path: Path) -> tuple[str, list[str]]:
    """Return (scraper_summary_line, list_of_error_lines) from the LAST run in the log."""
    summary = ""
    errors = []
    if not log_path.exists():
        return summary, errors
    lines = log_path.read_text().splitlines()
    # Find the last "deploy start" marker and only look at lines after it
    start_idx = 0
    for i, line in enumerate(lines):
        if "MacroBell deploy start" in line:
            start_idx = i
    for line in lines[start_idx:]:
        if line.startswith("[done]"):
            summary = line
        elif "[error]" in line.lower() or "[failed]" in line.lower():
            errors.append(line.strip())
    return summary, errors


def build_report(db: sqlite3.Connection, log_path: Path, failed_steps: list[str]) -> str:
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append(f"# MacroBell Deploy Report — {today}")
    lines.append(f"*Generated {now}*\n")

    # --- Pipeline status ---
    all_steps = ["prices", "nutrition", "relink", "build", "push"]
    lines.append("## Pipeline")
    for step in all_steps:
        icon = "✗" if step in failed_steps else "✓"
        lines.append(f"- {icon} {step}")
    lines.append("")

    # --- Scraper summary from log ---
    scraper_summary, error_lines = parse_log(log_path)
    if scraper_summary:
        # "[done] 8134 stores scraped | prices: 42311 | staged: 234"
        m = re.search(r"(\d+) stores scraped \| prices: (\d+) \| staged: (\d+)", scraper_summary)
        if m:
            lines.append("## Scrape Summary")
            lines.append(f"- Stores visited: **{int(m.group(1)):,}**")
            lines.append(f"- Price rows written (changed): **{int(m.group(2)):,}**")
            lines.append(f"- Staged rows written: **{int(m.group(3)):,}**")
            lines.append("")

    # --- Errors ---
    if error_lines:
        lines.append(f"## Errors ({len(error_lines)})")
        for e in error_lines[:20]:
            lines.append(f"- `{e}`")
        if len(error_lines) > 20:
            lines.append(f"- *...and {len(error_lines) - 20} more — see log*")
        lines.append("")

    # --- Price changes today ---
    changed_today = db.execute("""
        SELECT COUNT(DISTINCT store_id), COUNT(*)
        FROM prices
        WHERE collected_at >= ? AND price_cents >= ?
    """, (today, MIN_PRICE)).fetchone()
    if changed_today and changed_today[1]:
        lines.append("## Price Changes Today")
        lines.append(f"- {changed_today[0]:,} stores had at least one price change")
        lines.append(f"- {changed_today[1]:,} total price rows written")
        lines.append("")

    # --- Notable movers (national avg vs prior) ---
    movers = []
    for name, cid in site_items(db):
        cur = current_avg(db, cid)
        prv = prev_avg(db, cid, today)
        if cur and prv and prv > 0:
            pct = (cur - prv) / prv * 100
            if abs(pct) >= SIGNIFICANT_PCT:
                movers.append((pct, name, prv, cur))
    movers.sort(key=lambda x: -abs(x[0]))

    if movers:
        lines.append(f"## Notable Price Movers (≥{SIGNIFICANT_PCT:.0f}% change)")
        lines.append(f"{'Item':<40} {'Before':>8} {'After':>8} {'Change':>8}")
        lines.append("-" * 68)
        for pct, name, prv, cur in movers:
            arrow = "▲" if pct > 0 else "▼"
            lines.append(f"{name:<40} {fmt_money(prv):>8} {fmt_money(cur):>8} {arrow}{abs(pct):.1f}%")
        lines.append("")

    # --- New stores ---
    new_stores = db.execute("""
        SELECT s.store_id, s.city, s.state, s.full_address
        FROM stores s
        WHERE s.store_id IN (
            SELECT store_id FROM prices
            GROUP BY store_id
            HAVING MIN(collected_at) >= ?
        )
        ORDER BY s.state, s.city
    """, (today,)).fetchall()

    if new_stores:
        lines.append(f"## New Stores ({len(new_stores)})")
        for sid, city, state, addr in new_stores:
            city_fmt = (city or "").replace("-", " ").title()
            lines.append(f"- #{sid} — {city_fmt}, {state or ''} — {addr or ''}")
        lines.append("")

    # --- Possibly closed (not scraped recently) ---
    cutoff = f"{today}T00:00:00"
    possibly_closed = db.execute("""
        SELECT store_id, city, state, full_address, last_scraped_date
        FROM stores
        WHERE last_scraped_date IS NOT NULL
          AND last_scraped_date < ?
          AND last_scraped_date >= date(?, '-' || ? || ' days')
        ORDER BY last_scraped_date DESC
        LIMIT 25
    """, (cutoff, today, CLOSED_WINDOW_DAYS)).fetchall()

    if possibly_closed:
        lines.append(f"## Possibly Closed / Unreachable (not scraped today, last seen ≤{CLOSED_WINDOW_DAYS}d ago)")
        for sid, city, state, addr, last in possibly_closed:
            city_fmt = (city or "").replace("-", " ").title()
            last_short = (last or "")[:10]
            lines.append(f"- #{sid} — {city_fmt}, {state or ''} — {addr or ''} *(last: {last_short})*")
        lines.append("")

    if not movers and not new_stores and not possibly_closed and not error_lines:
        lines.append("*No notable changes this run.*\n")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--log", required=True, help="Path to today's deploy log")
    ap.add_argument("--out", required=True, help="Output report path")
    ap.add_argument("--failed", default="", help="Space-separated list of failed step names")
    args = ap.parse_args()

    failed = args.failed.split() if args.failed else []
    db = connect(args.db)
    try:
        report = build_report(db, Path(args.log), failed)
    finally:
        db.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[report] wrote {out}")

    # Also write a fixed "latest" symlink/copy for easy morning access
    latest = out.parent / "latest_report.md"
    latest.write_text(report)


if __name__ == "__main__":
    main()
