"""
Shared HTTP session factory and cookie warm-up.
"""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from macrobell.config import (
    REQUEST_TIMEOUT,
    RETRY_BACKOFF_FACTOR,
    RETRY_CONNECT,
    RETRY_READ,
    RETRY_STATUS_FORCELIST,
    RETRY_TOTAL,
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tacobell.com/",
    "Origin": "https://www.tacobell.com",
}


def make_session(
    *,
    retries: int | None = None,
    connection_mode: str = "close",
) -> requests.Session:
    """
    Build a requests.Session with retry logic and browser-like headers.

    Parameters
    ----------
    retries : int | None
        Override the default retry count.  Pass ``0`` to disable retries.
    connection_mode : str
        ``"close"`` (default, used by api_scraper_db) or ``"keep-alive"``
        (used by code_mapper_all).
    """
    s = requests.Session()
    headers = dict(BROWSER_HEADERS)
    headers["Connection"] = connection_mode
    s.headers.update(headers)

    total = retries if retries is not None else RETRY_TOTAL
    retry = Retry(
        total=total,
        connect=RETRY_CONNECT if retries is None else total,
        read=RETRY_READ if retries is None else total,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_FORCELIST,
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def warm_cookies(session: requests.Session) -> None:
    """Pre-warm cookies (Akamai/site) to reduce 403s."""
    try:
        session.get("https://www.tacobell.com/locations", timeout=REQUEST_TIMEOUT)
    except Exception:
        pass
