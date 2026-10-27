"""Ethiopian crop x production-area x variety nutrient target bands.

Loads ``Dataset/variety_bands.json`` — advisory N/P/K/pH target ranges for
named Ethiopian varieties and production areas (EIAR / CIMMYT / JARC /
regional research centers). These are *lookup bands*, not measured soil
observations: the recommendation engine uses them as the most specific band
layer when a caller explicitly supplies a variety.

Resolution is deliberately conservative. A variety is selected only when the
caller asks for one (or supplies a production area that resolves uniquely for
the crop); unknown crops/regions/varieties return None so callers can reject
them instead of silently falling back to a different variety's advice.

Band precedence in the engine is:

    explicit variety  >  agroecological zone  >  crop  >  generic

When both a variety and a zone are supplied, the variety bands drive the
comparison while the zone's machine-readable overrides (lime-first, P-fixation,
K bypass, salinity) still apply.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VARIETY_JSON = BASE_DIR / "Dataset" / "variety_bands.json"

BAND_COLS = ("N", "P", "K", "ph")


def _key(value):
    """Case/separator/punctuation-insensitive lookup key."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    cleaned = re.sub(r"[\s/\-,.'’()]+", "_", cleaned)
    return cleaned.strip("_") or None


@lru_cache(maxsize=1)
def load_variety_data():
    """Validated variety dataset (cached). Missing/malformed -> {}."""
    if not VARIETY_JSON.exists():
        return {}
    try:
        with open(VARIETY_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


@lru_cache(maxsize=1)
def _raw_varieties():
    """Raw variety records from the data file (no canonicalisation)."""
    entries = load_variety_data().get("varieties") or {}
    if not isinstance(entries, dict):
        return {}
    return entries


def _raw_crop_keys():
    return {
        _key(raw.get("crop"))
        for raw in _raw_varieties().values()
        if isinstance(raw, dict) and _key(raw.get("crop"))
    }


def _raw_region_keys():
    return {
        _key(raw.get("region"))
        for raw in _raw_varieties().values()
        if isinstance(raw, dict) and _key(raw.get("region"))
    }


def _variety_index():
    """{variety_id: enriched entry} for the current data file."""
    entries = _raw_varieties()
    if not isinstance(entries, dict):
        return {}
    out = {}
    for variety_id, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        crop = canonical_crop(raw.get("crop"))
        bands = raw.get("bands") or {}
        if crop is None or not isinstance(bands, dict):
            continue
        if any(col not in bands for col in BAND_COLS):
            continue
        entry = dict(raw)
        entry["id"] = variety_id
        entry["crop"] = crop
        entry["bands"] = {
            col: [float(bands[col][0]), float(bands[col][1])]
            for col in BAND_COLS
        }
        entry["region"] = canonical_region(raw.get("region")) or _key(
            raw.get("region"))
        entry["variety"] = str(raw.get("variety") or variety_id)
        entry["aliases"] = [str(alias) for alias in (raw.get("aliases") or [])]
        out[variety_id] = entry
    return out


def _crop_aliases():
    return load_variety_data().get("crop_aliases") or {}


def _region_aliases():
    return load_variety_data().get("region_aliases") or {}


def meta():
    """Dataset metadata ('_meta' entry)."""
    return dict(load_variety_data().get("_meta") or {})


def list_variety_ids():
    """Sorted stable variety IDs."""
    return sorted(_variety_index())


def list_varieties(crop=None, region=None):
    """Variety records, optionally filtered by crop and production area.

    Filters accept the same aliases as ``resolve_variety``. Unknown filters
    return an empty list rather than every record.
    """
    entries = list(_variety_index().values())
    if crop is not None:
        crop_key = canonical_crop(crop)
        if crop_key is None:
            return []
        entries = [entry for entry in entries if entry["crop"] == crop_key]
    if region is not None:
        region_key = canonical_region(region)
        if region_key is None:
            return []
        entries = [
            entry for entry in entries if entry.get("region") == region_key
        ]
    return sorted(
        entries,
        key=lambda entry: (
            entry["crop"], entry.get("region") or "", entry["variety"]))


def available_variety_crops():
    """Sorted crop slugs that have at least one variety record."""
    return sorted({entry["crop"] for entry in _variety_index().values()})


def crop_exists(crop):
    return canonical_crop(crop) is not None


def canonical_crop(crop):
    """Canonical crop slug for an input name/alias, else None."""
    key = _key(crop)
    if key is None:
        return None
    if key in _raw_crop_keys():
        return key
    aliases = _crop_aliases()
    for alias, crop_id in aliases.items():
        if _key(alias) == key:
            target = _key(crop_id)
            return target if target in _raw_crop_keys() else None
    return None


def canonical_region(region):
    """Canonical production-area key for an input name/alias, else None."""
    key = _key(region)
    if key is None:
        return None
    aliases = _region_aliases()
    for alias, region_id in aliases.items():
        if _key(alias) == key:
            return _key(region_id)
    if key in _raw_region_keys():
        return key
    return None


def _match_variety(entry, variety):
    key = _key(variety)
    if key is None:
        return False
    names = [entry["id"], entry["variety"], *entry.get("aliases", [])]
    return any(_key(name) == key for name in names)


def resolve_variety(crop, region=None, variety=None):
    """Best variety record for a crop/area/variety query, or None.

    Matching levels, in order:
      * crop + region + variety  -> ``crop_region_variety``
      * crop + variety           -> ``crop_variety``
      * crop + region (unique)   -> ``crop_region``

    A bare crop never resolves: variety advice is opt-in. Unknown values and
    ambiguous matches return None so the API can answer with a clear 400.
    """
    crop_key = canonical_crop(crop)
    if crop_key is None:
        return None
    if not variety and not region:
        return None

    candidates = [
        entry for entry in _variety_index().values()
        if entry["crop"] == crop_key
    ]
    if variety:
        candidates = [
            entry for entry in candidates if _match_variety(entry, variety)
        ]
        if not candidates:
            return None

    region_key = canonical_region(region) if region else None
    if region:
        if region_key is None:
            return None
        candidates = [
            entry for entry in candidates if entry.get("region") == region_key
        ]
        if not candidates:
            return None

    if len(candidates) != 1:
        return None

    entry = dict(candidates[0])
    entry["bands"] = {
        col: list(entry["bands"][col]) for col in BAND_COLS
    }
    if variety and region:
        entry["match_level"] = "crop_region_variety"
    elif variety:
        entry["match_level"] = "crop_variety"
    else:
        entry["match_level"] = "crop_region"
    return entry


def variety_advice_ranges(crop, region=None, variety=None):
    """Ideal band dict ({'N': (lo, hi), ...}) for a resolved variety, else None."""
    entry = resolve_variety(crop, region=region, variety=variety)
    if entry is None:
        return None
    return {
        col: (float(entry["bands"][col][0]), float(entry["bands"][col][1]))
        for col in BAND_COLS
    }


def is_known_variety(variety_id):
    return variety_id in _variety_index()


def clear_cache():
    """Test/deployment helper: drop cached file contents."""
    load_variety_data.cache_clear()
    _variety_index.cache_clear()
