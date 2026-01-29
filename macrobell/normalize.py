"""
Text normalization utilities for product names and DataFrame columns.
"""
from __future__ import annotations
import re

import pandas as pd

from macrobell.config import SIZE_WORDS

MARKS = r"[®™()]"


def normalize_name(s: str) -> str:
    """Lowercase, strip marks/punctuation, collapse whitespace."""
    s = (s or "").lower()
    s = re.sub(MARKS, "", s)
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase and underscore-ify column names."""
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def flag_category(name_or_cat: str) -> tuple[int, int]:
    """Return (is_breakfast, is_drink) flags based on keywords."""
    nm = (name_or_cat or "").lower()
    is_breakfast = 1 if "breakfast" in nm else 0
    is_drink = (
        1
        if any(
            tok in nm
            for tok in ("drink", "beverage", "freeze", "soda", "tea", "coffee", "lemonade")
        )
        else 0
    )
    return is_breakfast, is_drink


def split_base_and_size(s: str) -> tuple[str, str]:
    """Split a product name into (base_name, size_variant)."""
    tokens = normalize_name(s).split()
    base, size = [], []
    for t in tokens:
        if t in SIZE_WORDS or t in {"party", "pack", "box", "combo"}:
            size.append(t)
        else:
            base.append(t)
    return (" ".join(base).strip(), " ".join(size).strip())
