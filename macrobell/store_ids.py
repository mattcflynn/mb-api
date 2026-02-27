"""
Store-ID parsing, sanitization, and candidate generation.
"""
from __future__ import annotations
import re
from typing import List, Optional

import requests

ALPHA_PREFIX = re.compile(r"^[A-Za-z](\d{5,7})$")
DIGITS = re.compile(r"^\d{4,7}$")
STORE_ID_PATTERN = re.compile(r"^[A-Za-z]?\d{4,7}$")


def sanitize_store_id(raw_id: str | None) -> Optional[str]:
    """
    Normalize store_id to digits only:
      - 'G135807' -> '135807'
      - pure digits -> as-is (keeps leading zeros)
      - else None
    """
    if not raw_id:
        return None
    s = str(raw_id).strip()
    m = ALPHA_PREFIX.match(s)
    if m:
        return m.group(1)
    return s if DIGITS.fullmatch(s) else None


def store_id_candidates(raw_id: str) -> List[str]:
    """Return [original, stripped-leading-zeros] if different."""
    s = str(raw_id).strip()
    nums = s.lstrip("0")
    cand = [s]
    if nums and nums != s:
        cand.append(nums)
    return cand


def parse_store_id_from_url(u: str) -> str | None:
    """
    Extract store ID from a locations URL.
    Typical pattern: https://locations.tacobell.com/<state>/<city>/<STOREID>.html
    """
    m = re.search(r"/([A-Za-z]?\d{4,7})\.html?$", str(u))
    return m.group(1) if m else None


def extract_store_id_from_html(url: str, session: requests.Session) -> str | None:
    """
    Fallback: fetch the store page HTML and look for a numeric storeNumber.
    """
    try:
        r = session.get(url, timeout=25)
        r.raise_for_status()
        html = r.text

        m = re.search(r'"storeNumber"\s*:\s*"(\d{4,7})"', html)
        if m:
            return m.group(1)

        m2 = re.search(r'"storeId"\s*:\s*"(\d{4,7})"', html)
        if m2:
            return m2.group(1)

        m3 = re.search(r"/(\d{4,7})\.html", url)
        if m3:
            return m3.group(1)
    except Exception:
        return None
    return None


def build_id_candidates(url: str, csv_sid: str | None, session: requests.Session) -> List[str]:
    """
    Build a prioritized list of candidate IDs to try for a store:
      1) raw CSV store_id (keeps alpha prefix)
      2) sanitized CSV store_id (digits only)
      3) sanitized ID parsed from URL
      4) digits-only for alpha-prefixed
      5) zero-stripped variant ONLY if base starts with '0'
      6) HTML-extracted storeNumber
    All unique, in priority order.
    """
    cands: List[str] = []

    def add(x: str | None):
        if x and x not in cands:
            cands.append(x)

    raw_csv = (csv_sid or "").strip() if csv_sid else ""
    if raw_csv and STORE_ID_PATTERN.fullmatch(raw_csv):
        add(raw_csv)

    primary = sanitize_store_id(csv_sid)
    add(primary)

    from_url = sanitize_store_id(parse_store_id_from_url(url))
    add(from_url)

    if csv_sid:
        m = ALPHA_PREFIX.match(csv_sid.strip())
        if m:
            add(m.group(1))

    for base in (primary, from_url):
        if base and base.startswith("0"):
            add(base.lstrip("0"))

    html_id = sanitize_store_id(extract_store_id_from_html(url, session))
    add(html_id)

    cands = [c for c in cands if c and STORE_ID_PATTERN.fullmatch(c)]
    return cands
