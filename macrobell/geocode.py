"""
Offline store geocoding via US Census ZCTA centroids.

No API keys, no rate limits, no network after the one-time gazetteer download
(cached as zcta_cache.csv next to the project root). Resolution is ZIP-centroid
(~1-2 mi within a ZIP), which is well within tolerance for nearby-store radius
search. This replaces the Google/Nominatim path in store_geocoder.py.
"""
from __future__ import annotations
import csv
import io
import re
import urllib.request
import zipfile
from pathlib import Path

# Same source that build_site_data.py uses for zip_latlon.json.
ZCTA_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
            "2023_Gazetteer/2023_Gaz_zcta_national.zip")
ZCTA_CACHE = "zcta_cache.csv"

# A store's ZIP is the 5-digit token at the END of the address. Leading street
# numbers (e.g. "12130 Business Blvd ...") must NOT be mistaken for it.
_ZIP_TAIL = re.compile(r"(\d{5})(?:-\d{4})?\s*$")


def zip_from_address(address: str | None) -> str | None:
    """Return the 5-digit ZIP parsed from the tail of an address, or None."""
    if not address:
        return None
    m = _ZIP_TAIL.search(address.strip())
    return m.group(1) if m else None


def load_zcta(root: Path) -> dict[str, tuple[float, float]]:
    """Return {zip5: (lat, lon)} from the cached Census gazetteer (download once)."""
    cache = root / ZCTA_CACHE
    if not cache.exists():
        with urllib.request.urlopen(ZCTA_URL, timeout=120) as resp:
            zf = zipfile.ZipFile(io.BytesIO(resp.read()))
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        cache.write_bytes(zf.read(name))
    out: dict[str, tuple[float, float]] = {}
    with open(cache, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fields = {k.strip(): k for k in reader.fieldnames}
        for row in reader:
            zc = row[fields["GEOID"]].strip()
            out[zc] = (round(float(row[fields["INTPTLAT"]].strip()), 4),
                       round(float(row[fields["INTPTLONG"]].strip()), 4))
    return out


class Geocoder:
    """ZIP-centroid geocoder. Construct once (loads the gazetteer), then reuse."""

    def __init__(self, root: Path):
        self.zcta = load_zcta(root)

    def from_zip(self, zip5: str | None) -> tuple[float, float] | None:
        if not zip5:
            return None
        return self.zcta.get(zip5.strip()[:5])

    def from_address(self, address: str | None) -> tuple[float, float] | None:
        return self.from_zip(zip_from_address(address))
