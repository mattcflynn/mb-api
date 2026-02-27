"""
Centralized constants for the MacroBell pipeline.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Set

# ── API URLs ──────────────────────────────────────────────────────────
MENU_API_BASE = "https://www.tacobell.com/tacobellwebservices"
SITEMAP_INDEX_URL = "https://locations.tacobell.com/sitemap.xml"
NUTRITION_LANDING_URL = "https://www.tacobell.com/nutrition/info"
FALLBACK_NUTRITION_JSON_GZ = (
    "https://d2eawub7utcl6.cloudfront.net/calculator/10197-0-1760620706.json.gz"
)

# ── HTTP settings ─────────────────────────────────────────────────────
REQUEST_TIMEOUT = (5, 12)  # (connect, read)
HARD_DEADLINE_SEC = 20
RETRY_TOTAL = 2
RETRY_CONNECT = 2
RETRY_READ = 2
RETRY_BACKOFF_FACTOR = 0.3
RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

# ── Rate limits ───────────────────────────────────────────────────────
JITTER_MIN = 0.12
JITTER_MAX = 0.28
DEFAULT_SLEEP_MIN = 0.10
DEFAULT_SLEEP_MAX = 0.25

# ── Cache settings ────────────────────────────────────────────────────
CACHE_DIR = Path(".cache/menus")
CACHE_TTL_SEC = 24 * 3600

# ── File paths ────────────────────────────────────────────────────────
DEFAULT_DB = "macrobell.db"
CHUNKS_DIR = "store_chunks"
FULL_STORE_LIST_CSV = "taco_bell_stores_from_sitemap.csv"
FINAL_STORE_CSV = "taco_bell_stores_final_with_coords.csv"
MANUAL_FIXES_CSV = "manual_addresses.csv"
CHUNK_FILE_PATTERN = "*_with_coords.csv"

# ── Matching thresholds ───────────────────────────────────────────────
HIGH_CONF = 0.80
MID_CONF = 0.65

# ── Region mappings ───────────────────────────────────────────────────
REGIONS: Dict[str, Set[str]] = {
    "West_Coast":     {"CA", "OR", "WA"},
    "Pacific":        {"AK", "HI"},
    "Mountain":       {"AZ", "NV", "UT", "CO", "NM", "ID", "MT", "WY"},
    "Southwest":      {"TX", "OK"},
    "South_Central":  {"AR", "LA"},
    "Southeast":      {"FL", "GA", "SC", "NC", "AL", "MS", "TN", "KY"},
    "Great_Lakes":    {"IL", "IN", "MI", "OH", "WI"},
    "Midwest_Plains": {"ND", "SD", "NE", "KS", "MN", "IA", "MO"},
    "Mid_Atlantic":   {"PA", "NJ", "NY", "DE", "MD", "DC", "VA", "WV"},
    "New_England":    {"ME", "NH", "VT", "MA", "CT", "RI"},
}

US_ABBR = {k: k for k in [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR",
]}

NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "dc": "DC", "puerto rico": "PR",
}

# ── NLP word sets ─────────────────────────────────────────────────────
STOPWORDS = {"the", "a", "an", "and", "with", "of", "for"}
SIZE_WORDS = {
    "large", "medium", "small", "grande", "mini",
    "double", "triple", "party", "pack", "box", "combo",
}
