"""Ethiopian crop x season advisory bands (Enhancement D).

Loads ``Dataset/seasonal_bands.json``: crop-specific nitrogen targets for the
Meher (main rains) and Belg (short rains) seasons. P/K/pH are deliberately
season-independent; their stored baseline bands are used only when no more
specific variety or agroecological-zone band is available.

Season is an N-only modifier. It never changes P/K/pH, lime chemistry, zone
overrides, salinity rules, or model artifacts. With no season supplied the
engine's legacy behavior is untouched.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SEASONAL_JSON = BASE_DIR / "Dataset" / "seasonal_bands.json"

BAND_COLS = ("N", "P", "K", "ph")
BASE_COLS = ("P", "K", "ph")


def _key(value):
    """Case/separator/punctuation-insensitive lookup key."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    cleaned = re.sub(r"[\s/\-,.'’()]+", "_", cleaned)
    return cleaned.strip("_") or None


@lru_cache(maxsize=1)
def load_seasonal_data():
    """Validated seasonal dataset (cached). Missing/malformed -> {}."""
    if not SEASONAL_JSON.exists():
        return {}
    try:
        with open(SEASONAL_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def _raw_crops():
    crops = load_seasonal_data().get("crops") or {}
    return crops if isinstance(crops, dict) else {}


def _valid_band(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        lo, hi = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    if lo < 0 or hi < lo:
        return None
    return [lo, hi]


def _crop_index():
    """{crop_id: normalized record} for the current data file."""
    out = {}
    for crop_id, raw in _raw_crops().items():
        if not isinstance(raw, dict):
            continue
        canonical = canonical_crop(crop_id)
        if canonical is None:
            continue
        base = raw.get("base_bands") or {}
        seasons = raw.get("seasons") or {}
        if not isinstance(base, dict) or not isinstance(seasons, dict):
            continue
        base_bands = {}
        for col in BASE_COLS:
            band = _valid_band(base.get(col))
            if band is None:
                break
            base_bands[col] = band
        if len(base_bands) != len(BASE_COLS):
            continue
        season_bands = {}
        for season_id, season_raw in seasons.items():
            season_key = canonical_season(season_id)
            if season_key is None or not isinstance(season_raw, dict):
                continue
            n_band = _valid_band(season_raw.get("N"))
            if n_band is None:
                continue
            season_bands[season_key] = {
                "N": n_band,
                "note": str(season_raw.get("note") or ""),
            }
        if not season_bands:
            continue
        out[canonical] = {
            "id": canonical,
            "crop": canonical,
            "crop_label": str(raw.get("crop_label") or canonical.replace("_", " ").title()),
            "base_bands": base_bands,
            "seasons": season_bands,
            "source": str(raw.get("source") or ""),
            "review_status": str(raw.get("review_status") or "unverified"),
        }
    return out


def _crop_aliases():
    aliases = load_seasonal_data().get("crop_aliases") or {}
    return aliases if isinstance(aliases, dict) else {}


def _season_aliases():
    aliases = load_seasonal_data().get("season_aliases") or {}
    return aliases if isinstance(aliases, dict) else {}


def meta():
    """Dataset metadata ('_meta' entry)."""
    return dict(load_seasonal_data().get("_meta") or {})


def list_seasons():
    """Sorted canonical season ids."""
    seasons = load_seasonal_data().get("seasons") or {}
    return sorted(seasons) if isinstance(seasons, dict) else []


def season_info(season):
    """Season metadata ({label, months, note}) or {}."""
    key = canonical_season(season)
    if key is None:
        return {}
    info = (load_seasonal_data().get("seasons") or {}).get(key) or {}
    return dict(info) if isinstance(info, dict) else {}


def season_crops():
    """Sorted crop slugs with at least one seasonal record."""
    return sorted(_crop_index())


def crop_exists(crop):
    return canonical_crop(crop) is not None


def canonical_crop(crop):
    """Canonical crop slug for an input name/alias, else None."""
    key = _key(crop)
    if key is None:
        return None
    raw_crops = _raw_crops()
    for crop_id in raw_crops:
        if _key(crop_id) == key:
            return _key(crop_id)
    aliases = _crop_aliases()
    for alias, crop_id in aliases.items():
        if _key(alias) == key:
            target = _key(crop_id)
            if target in {_key(c) for c in raw_crops}:
                return target
    return None


def canonical_season(season):
    """Canonical season id ('meher'/'belg') for an input alias, else None."""
    key = _key(season)
    if key is None:
        return None
    aliases = _season_aliases()
    for season_id, names in aliases.items():
        canonical = _key(season_id)
        if canonical is None:
            continue
        for name in (names if isinstance(names, (list, tuple)) else [names]):
            if _key(name) == key:
                return canonical
    for season_id in (load_seasonal_data().get("seasons") or {}):
        if _key(season_id) == key:
            return _key(season_id)
    return None


def resolve_seasonal_bands(crop, season):
    """Resolved crop-season record, or None for unknown/unsupported values."""
    crop_key = canonical_crop(crop)
    season_key = canonical_season(season)
    if crop_key is None or season_key is None:
        return None
    record = _crop_index().get(crop_key)
    if record is None or season_key not in record["seasons"]:
        return None
    season_data = record["seasons"][season_key]
    info = season_info(season_key)
    n_band = list(season_data["N"])
    bands = {
        "N": n_band,
        "P": list(record["base_bands"]["P"]),
        "K": list(record["base_bands"]["K"]),
        "ph": list(record["base_bands"]["ph"]),
    }
    return {
        "id": f"{crop_key}_{season_key}",
        "crop": crop_key,
        "crop_label": record["crop_label"],
        "season": season_key,
        "season_label": info.get("label") or season_key.title(),
        "season_months": info.get("months"),
        "season_note": info.get("note") or "",
        "bands": bands,
        "base_bands": {
            "P": list(record["base_bands"]["P"]),
            "K": list(record["base_bands"]["K"]),
            "ph": list(record["base_bands"]["ph"]),
        },
        "crop_note": season_data.get("note") or "",
        "n_factor": n_factor(crop_key),
        "source": record["source"],
        "review_status": record["review_status"],
    }


def n_factor(crop):
    """Belg/Meher N midpoint ratio for a crop, rounded to four decimals."""
    crop_key = canonical_crop(crop)
    if crop_key is None:
        return None
    record = _crop_index().get(crop_key)
    if record is None:
        return None
    meher = record["seasons"].get("meher")
    belg = record["seasons"].get("belg")
    if not meher or not belg:
        return None
    meher_mid = sum(meher["N"]) / 2
    belg_mid = sum(belg["N"]) / 2
    if meher_mid <= 0:
        return None
    return round(belg_mid / meher_mid, 4)


def apply_season_to_bands(base_bands, base_source, crop, season):
    """Apply the seasonal N rule while preserving the base P/K/pH layer.

    Returns ``(bands, metadata)``. When the crop/season is unsupported the
    base bands are returned unchanged with ``metadata = None``.

    - variety/zone base: Meher keeps the base N; Belg scales both N edges by
      the crop's Belg/Meher midpoint factor.
    - crop/generic base: the seasonal table supplies N plus its Ethiopian
      P/K/pH baseline (there is no more specific Ethiopian layer).
    """
    entry = resolve_seasonal_bands(crop, season)
    if entry is None:
        return base_bands, None

    season_key = entry["season"]
    out = {
        "N": list(base_bands["N"]),
        "P": list(base_bands["P"]),
        "K": list(base_bands["K"]),
        "ph": list(base_bands["ph"]),
    }
    if base_source in ("variety", "zone"):
        if season_key == "belg" and entry["n_factor"] is not None:
            out["N"] = [
                round(out["N"][0] * entry["n_factor"], 1),
                round(out["N"][1] * entry["n_factor"], 1),
            ]
            applied_source = f"{base_source}_x_belg_factor"
        else:
            applied_source = base_source
        unchanged = ["P", "K", "ph"]
    else:
        out = {col: list(entry["bands"][col]) for col in BAND_COLS}
        applied_source = "season_table"
        unchanged = ["P", "K", "ph"]

    metadata = {
        "id": entry["id"],
        "crop": entry["crop"],
        "crop_label": entry["crop_label"],
        "season": entry["season"],
        "label": entry["season_label"],
        "months": entry["season_months"],
        "n_band": list(out["N"]),
        "table_n_band": list(entry["bands"]["N"]),
        "base_n_band": list(base_bands["N"]),
        "n_factor": entry["n_factor"],
        "applied_n_source": applied_source,
        "unchanged": unchanged,
        "note": entry["crop_note"],
        "season_note": entry["season_note"],
        "source": entry["source"],
        "review_status": entry["review_status"],
    }
    return out, metadata


def clear_cache():
    """Drop cached dataset/index (tests or data reloads)."""
    load_seasonal_data.cache_clear()
    _raw_crops.cache_clear()
    _crop_index.cache_clear()


def season_meta(season=None):
    """Season metadata for an id, or dataset metadata when omitted."""
    return meta() if season is None else season_info(season)


def list_crops():
    """Alias for season_crops()."""
    return season_crops()


def is_supported(crop, season):
    """True when a crop-season advisory record exists."""
    return resolve_seasonal_bands(crop, season) is not None
