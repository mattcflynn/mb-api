"""
report.py — Write a human-readable Markdown summary after deploy.sh runs.

Usage (called by deploy.sh):
    uv run python report.py --log logs/deploy_20260615.log \
        --out logs/latest_report.md --failed "nutrition relink"

Sections:
  - Pipeline status (from --failed)
  - Scrape Summary (parsed from the deploy log)
  - Errors
  - Price Changes Today
  - National Price Movers (basket avg, ≥ SIGNIFICANT_PCT)
  - Price Trends by State (avg basket move per state)
  - New / Retired Menu Items (vs last week's snapshot)
  - Store Openings & Closures (vs last week's sitemap snapshot)

The last two diff against snapshot tables this script maintains, so they are
empty on the first run (a baseline is recorded) and populate from the next run.
"""
from __future__ import annotations
import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from macrobell.db import connect
from macrobell.config import FULL_STORE_LIST_CSV

NUTRITION_CATEGORIES = (
    "Cantina Chicken Menu", "Tacos", "Burritos",
    "Nachos", "Quesadillas", "Specialties",
)
MIN_PRICE = 100
SIGNIFICANT_PCT = 3.0        # flag items whose national avg moved >= this %
STATE_SIGNIFICANT_PCT = 2.0  # flag states whose basket avg moved >= this %
MIN_ITEMS_PER_STATE = 5      # ignore states with too few priced items (noise)


# ---------------------------------------------------------------------------
# National basket helpers
# ---------------------------------------------------------------------------
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


# Per-state avg of latest price for one item (current vs prior-to-today)
_CUR_BY_STATE = """
    SELECT s.state, AVG(p.price_cents) FROM prices p
    JOIN (
        SELECT store_id, MAX(collected_at) AS ts
        FROM prices WHERE canonical_product_id = ? AND price_cents >= ?
        GROUP BY store_id
    ) m ON p.store_id = m.store_id AND p.collected_at = m.ts
    JOIN stores s ON s.store_id = p.store_id
    WHERE p.canonical_product_id = ? AND p.price_cents >= ?
    GROUP BY s.state
"""
_PREV_BY_STATE = """
    SELECT s.state, AVG(p.price_cents) FROM prices p
    JOIN (
        SELECT store_id, MAX(collected_at) AS ts
        FROM prices
        WHERE canonical_product_id = ? AND price_cents >= ? AND collected_at < ?
        GROUP BY store_id
    ) m ON p.store_id = m.store_id AND p.collected_at = m.ts
    JOIN stores s ON s.store_id = p.store_id
    WHERE p.canonical_product_id = ? AND p.price_cents >= ? AND p.collected_at < ?
    GROUP BY s.state
"""


def state_price_movement(db: sqlite3.Connection, today: str) -> list[tuple[float, str, int]]:
    """Return [(avg_pct_change, state, n_items)] across the tracked basket, per state."""
    pcts: dict[str, list[float]] = defaultdict(list)
    for _name, cid in site_items(db):
        cur = {st: a for st, a in
               db.execute(_CUR_BY_STATE, (cid, MIN_PRICE, cid, MIN_PRICE)).fetchall() if a}
        prv = {st: a for st, a in
               db.execute(_PREV_BY_STATE, (cid, MIN_PRICE, today, cid, MIN_PRICE, today)).fetchall() if a}
        for st, c in cur.items():
            p = prv.get(st)
            if p and p > 0:
                pcts[st].append((c - p) / p * 100)
    rows = [(sum(v) / len(v), st, len(v)) for st, v in pcts.items()
            if len(v) >= MIN_ITEMS_PER_STATE]
    rows.sort(key=lambda x: -abs(x[0]))
    return rows


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


# ---------------------------------------------------------------------------
# Snapshot tables (for week-over-week item & store diffs)
# ---------------------------------------------------------------------------
def ensure_snapshot_tables(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS item_snapshots (
            snapshot_date TEXT, item_key TEXT, name TEXT,
            PRIMARY KEY (snapshot_date, item_key)
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS store_snapshots (
            snapshot_date TEXT, store_key TEXT, state TEXT,
            PRIMARY KEY (snapshot_date, store_key)
        )""")
    db.commit()


def _prev_snapshot_date(db: sqlite3.Connection, table: str, today: str) -> str | None:
    row = db.execute(
        f"SELECT MAX(snapshot_date) FROM {table} WHERE snapshot_date < ?", (today,)
    ).fetchone()
    return row[0] if row and row[0] else None


def item_changes(db: sqlite3.Connection, today: str):
    """Return (added, retired) lists of item names vs the previous snapshot, then record today's."""
    current = {cid: name for name, cid in site_items(db)}
    prev_date = _prev_snapshot_date(db, "item_snapshots", today)
    added, retired = [], []
    if prev_date:
        prev = {k: n for k, n in db.execute(
            "SELECT item_key, name FROM item_snapshots WHERE snapshot_date = ?", (prev_date,)
        ).fetchall()}
        added = sorted(current[k] for k in current.keys() - prev.keys())
        retired = sorted(prev[k] for k in prev.keys() - current.keys())

    db.execute("DELETE FROM item_snapshots WHERE snapshot_date = ?", (today,))
    db.executemany(
        "INSERT INTO item_snapshots (snapshot_date, item_key, name) VALUES (?, ?, ?)",
        [(today, cid, name) for cid, name in current.items()])
    db.commit()
    return added, retired, prev_date, len(current)


def read_sitemap_stores(csv_path: Path) -> dict[str, str]:
    """Return {store_url: state} from the sitemap CSV, or {} if unavailable."""
    if not csv_path.exists():
        return {}
    out: dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            url = (r.get("url") or "").strip()
            if url:
                out[url] = (r.get("state") or "").upper()
    return out


def store_changes(db: sqlite3.Connection, today: str, csv_path: Path):
    """Diff the current sitemap store set vs the previous snapshot; record today's.

    Returns (opened_by_state, closed_by_state, prev_date, current_count, had_sitemap).
    """
    current = read_sitemap_stores(csv_path)
    if not current:
        return {}, {}, None, 0, False

    prev_date = _prev_snapshot_date(db, "store_snapshots", today)
    opened, closed = defaultdict(int), defaultdict(int)
    if prev_date:
        prev = {k: st for k, st in db.execute(
            "SELECT store_key, state FROM store_snapshots WHERE snapshot_date = ?", (prev_date,)
        ).fetchall()}
        for k in current.keys() - prev.keys():
            opened[current[k]] += 1
        for k in prev.keys() - current.keys():
            closed[prev[k]] += 1

    db.execute("DELETE FROM store_snapshots WHERE snapshot_date = ?", (today,))
    db.executemany(
        "INSERT INTO store_snapshots (snapshot_date, store_key, state) VALUES (?, ?, ?)",
        [(today, url, st) for url, st in current.items()])
    db.commit()
    return dict(opened), dict(closed), prev_date, len(current), True


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------
def parse_log(log_path: Path) -> tuple[str, list[str]]:
    """Return (scraper_summary_line, error_lines) from the LAST run in the log."""
    summary = ""
    errors: list[str] = []
    if not log_path.exists():
        return summary, errors
    lines = log_path.read_text().splitlines()
    start_idx = 0
    for i, line in enumerate(lines):
        if "MacroBell deploy start" in line:
            start_idx = i
    for line in lines[start_idx:]:
        # Match the price-scraper summary specifically, not just any [done] line.
        if "stores scraped" in line and "prices:" in line:
            summary = line
        elif "[error]" in line.lower() or "[failed]" in line.lower():
            errors.append(line.strip())
    return summary, errors


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def build_report(db: sqlite3.Connection, log_path: Path, failed_steps: list[str]) -> str:
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    had_content = False

    lines.append(f"# MacroBell Deploy Report — {today}")
    lines.append(f"*Generated {now}*\n")

    # --- Pipeline status ---
    all_steps = ["sitemap", "onboard", "geocode", "prices", "nutrition", "relink", "build", "push"]
    lines.append("## Pipeline")
    for step in all_steps:
        icon = "✗" if step in failed_steps else "✓"
        lines.append(f"- {icon} {step}")
    lines.append("")

    # --- Scraper summary from log ---
    scraper_summary, error_lines = parse_log(log_path)
    if scraper_summary:
        m = re.search(r"(\d+) stores scraped \| prices: (\d+) \| staged: (\d+)", scraper_summary)
        if m:
            lines.append("## Scrape Summary")
            lines.append(f"- Stores visited: **{int(m.group(1)):,}**")
            lines.append(f"- Price rows written (changed): **{int(m.group(2)):,}**")
            lines.append(f"- Staged rows written: **{int(m.group(3)):,}**")
            lines.append("")

    # --- Errors ---
    if error_lines:
        had_content = True
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

    # --- National price movers + basket headline ---
    movers = []
    item_pcts: list[float] = []
    for name, cid in site_items(db):
        cur = current_avg(db, cid)
        prv = prev_avg(db, cid, today)
        if cur and prv and prv > 0:
            pct = (cur - prv) / prv * 100
            item_pcts.append(pct)
            if abs(pct) >= SIGNIFICANT_PCT:
                movers.append((pct, name, prv, cur))
    movers.sort(key=lambda x: -abs(x[0]))

    if movers:
        had_content = True
        lines.append(f"## National Price Movers (≥{SIGNIFICANT_PCT:.0f}% change)")
        lines.append("```")
        lines.append(f"{'Item':<40} {'Before':>8} {'After':>8} {'Change':>8}")
        lines.append("-" * 68)
        for pct, name, prv, cur in movers:
            arrow = "▲" if pct > 0 else "▼"
            lines.append(f"{name[:40]:<40} {fmt_money(prv):>8} {fmt_money(cur):>8} {arrow}{abs(pct):.1f}%")
        lines.append("```")
        lines.append("")

    # --- Price trends by state (always-on digest) ---
    state_moves = state_price_movement(db, today)
    if state_moves:
        had_content = True
        national = sum(item_pcts) / len(item_pcts) if item_pcts else 0.0
        ndir = "▲" if national >= 0 else "▼"
        lines.append("## Price Trends by State")
        lines.append(f"- National basket avg: **{ndir}{abs(national):.2f}%** week-over-week")
        flagged = [r for r in state_moves if abs(r[0]) >= STATE_SIGNIFICANT_PCT]
        if flagged:
            lines.append(f"- {len(flagged)} state(s) moved ≥{STATE_SIGNIFICANT_PCT:.0f}%")
        lines.append("")
        lines.append("```")
        lines.append(f"{'State':<6} {'Items':>6} {'Avg change':>12}")
        lines.append("-" * 26)
        for pct, st, n in state_moves[:8]:
            arrow = "▲" if pct > 0 else "▼"
            flag = "  *" if abs(pct) >= STATE_SIGNIFICANT_PCT else ""
            lines.append(f"{(st or '?').upper():<6} {n:>6} {arrow}{abs(pct):>9.2f}%{flag}")
        lines.append("```")
        lines.append("")

    # --- New / retired menu items ---
    added, retired, item_prev, item_total = item_changes(db, today)
    if added or retired:
        had_content = True
        lines.append("## Menu Items")
        if added:
            lines.append(f"**New ({len(added)})**")
            for n in added:
                lines.append(f"- 🟢 {n}")
        if retired:
            lines.append(f"**Retired ({len(retired)})**")
            for n in retired:
                lines.append(f"- 🔴 {n}")
        lines.append("")
    elif item_prev is None:
        lines.append(f"## Menu Items\n- *Baseline recorded ({item_total} items); changes show from next run.*\n")

    # --- Store openings & closures ---
    if "sitemap" in failed_steps:
        lines.append("## Store Openings & Closures\n- *Sitemap step failed this run — skipped to avoid false diffs.*\n")
        return "\n".join(lines)
    opened, closed, store_prev, store_total, had_sitemap = store_changes(
        db, today, Path(FULL_STORE_LIST_CSV))
    if not had_sitemap:
        lines.append("## Store Openings & Closures\n- *Sitemap unavailable this run — skipped.*\n")
    elif store_prev is None:
        lines.append(f"## Store Openings & Closures\n- *Baseline recorded ({store_total:,} stores); changes show from next run.*\n")
    elif opened or closed:
        had_content = True
        n_open, n_close = sum(opened.values()), sum(closed.values())
        lines.append("## Store Openings & Closures")
        lines.append(f"- 🟢 **{n_open}** opened   🔴 **{n_close}** closed   (vs {store_prev})")
        states = sorted(set(opened) | set(closed), key=lambda s: -(opened.get(s, 0) + closed.get(s, 0)))
        lines.append("```")
        lines.append(f"{'State':<6} {'Opened':>7} {'Closed':>7}")
        lines.append("-" * 22)
        for st in states:
            lines.append(f"{(st or '?').upper():<6} {opened.get(st, 0):>7} {closed.get(st, 0):>7}")
        lines.append("```")
        lines.append("")

    if not had_content and not error_lines:
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
        ensure_snapshot_tables(db)
        report = build_report(db, Path(args.log), failed)
    finally:
        db.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[report] wrote {out}")

    latest = out.parent / "latest_report.md"
    latest.write_text(report)


if __name__ == "__main__":
    main()
