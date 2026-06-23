"""
geocode_stores.py — fill missing store coordinates offline (ZIP centroid).

Replaces the Google/Nominatim store_geocoder.py for ongoing use. Records each
store's coordinate precision in a new stores.coord_source column:

  - 'rooftop'       address-level coords from the original geocoding pipeline
                    (back-filled once for every store that already had coords)
  - 'zip_centroid'  coords derived offline from the store's ZIP centroid

Query the rooftop set later with:  WHERE coord_source = 'rooftop'

Usage:
  uv run python geocode_stores.py          # geocode stores missing coords
  uv run python geocode_stores.py --all    # also refresh existing zip_centroid
                                           # rows (never overwrites 'rooftop')
"""
from __future__ import annotations
import argparse
import sqlite3
from pathlib import Path

from macrobell.db import connect
from macrobell.geocode import Geocoder, zip_from_address


def ensure_coord_source(db: sqlite3.Connection) -> None:
    """Add the coord_source column if absent and back-fill existing coords once."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(stores)")}
    if "coord_source" not in cols:
        db.execute("ALTER TABLE stores ADD COLUMN coord_source TEXT")
    db.execute("""
        UPDATE stores SET coord_source = 'rooftop'
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND coord_source IS NULL
    """)
    db.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="macrobell.db")
    ap.add_argument("--all", action="store_true",
                    help="Also re-geocode existing zip_centroid rows (rooftop coords are never touched)")
    args = ap.parse_args()

    root = Path(__file__).parent
    db = connect(str(root / args.db))
    ensure_coord_source(db)

    if args.all:
        rows = db.execute("""
            SELECT store_id, full_address, zip_code FROM stores
            WHERE latitude IS NULL OR longitude IS NULL OR coord_source = 'zip_centroid'
        """).fetchall()
    else:
        rows = db.execute("""
            SELECT store_id, full_address, zip_code FROM stores
            WHERE latitude IS NULL OR longitude IS NULL
        """).fetchall()

    print(f"{len(rows)} store(s) to geocode (offline ZIP centroid)")
    geo = Geocoder(root)

    done = 0
    misses: list[tuple] = []
    for sid, addr, zc in rows:
        # The address tail is more reliable than the stored zip_code column.
        zip5 = zip_from_address(addr) or (zc or "").strip()[:5]
        coord = geo.from_zip(zip5)
        if coord:
            db.execute(
                "UPDATE stores SET latitude=?, longitude=?, coord_source='zip_centroid' "
                "WHERE store_id=?", (coord[0], coord[1], sid))
            done += 1
        else:
            misses.append((sid, addr, zip5))
    db.commit()

    print(f"geocoded: {done} | unresolved: {len(misses)}")
    for sid, addr, zip5 in misses[:15]:
        print(f"  MISS #{sid} zip={zip5!r} addr={addr!r}")

    print("\ncoord_source breakdown:")
    for src, n in db.execute(
            "SELECT COALESCE(coord_source, '(none)'), COUNT(*) "
            "FROM stores GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {src:<14} {n:,}")
    db.close()


if __name__ == "__main__":
    main()
