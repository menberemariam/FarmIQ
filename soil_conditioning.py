"""SOC/texture-conditioned advisory bands (Enhancement E).

Loads ``Dataset/soil_conditioning.json``: rule-based, *unverified* band
modifiers for soil organic carbon (N) and texture (K, P). Values are deltas
applied to the project's existing variety/zone/crop/generic bands, never
absolute replacement bands.

Safety rules:
  * Missing/malformed files return ``{}``; malformed rules are ignored.
  * Conditioning is opt-in. A caller must supply an explicit texture/SOC
    value, a woreda with a mapping, or set ``SOIL_CONDITIONING_ENABLED=true``.
  * ``soil_type`` alone never activates conditioning (it keeps its existing
    lime-LRF meaning) unless ``SOIL_CONDITIONING_USE_SOIL_TYPE=true``.
  * With no conditioning context the engine's legacy output is byte-identical.

The module is a pure lookup: no model loading and no writes.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONDITIONING_JSON = BASE_DIR / "Dataset" / "soil_conditioning.json"
WORDEDA_JSON = BASE_DIR / "Dataset" / "woreda_soil_defaults.json"

BAND_COLS = ("N", "P", "K", "ph")
TEXTURE_ENV = "SOIL_CONDITIONING_ENABLED"
SOIL_TYPE_ENV = "SOIL_CONDITIONING_USE_SOIL_TYPE"


def _key(value):
    """Case/separator/punctuation-insensitive lookup key."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    cleaned = re.sub(r"[\s/\-,.'’()]+", "_", cleaned)
    return cleaned.strip("_") or None


def _env_flag(name, default=False):
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on", "enabled")


def _load_json(path):
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_conditioning_data():
    """Validated conditioning dataset (cached). Missing/malformed -> {}."""
    return _load_json(CONDITIONING_JSON)


@lru_cache(maxsize=1)
def load_woreda_data():
    """Woreda fallback dataset (cached). Missing/malformed -> {}."""
    return _load_json(WORDEDA_JSON)


@lru_cache(maxsize=1)
def _texture_index():
    out = {}
    raw = load_conditioning_data().get("texture_classes") or {}
    if not isinstance(raw, dict):
        return out
    for texture_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        key = _key(texture_id)
        if not key:
            continue
        out[key] = {
            "id": key,
            "name_en": str(entry.get("name_en") or key.replace("_", " ").title()),
            "aliases": [str(a) for a in (entry.get("aliases") or [])],
            "retention": entry.get("retention"),
        }
    return out


@lru_cache(maxsize=1)
def _soc_index():
    out = {}
    raw = load_conditioning_data().get("soc_classes") or {}
    if not isinstance(raw, dict):
        return out
    for class_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        key = _key(class_id)
        if not key:
            continue
        out[key] = {
            "id": key,
            "name_en": str(entry.get("name_en") or key.replace("_", " ").title()),
            "min_pct": _number(entry.get("min_pct")),
            "max_pct": _number(entry.get("max_pct")),
        }
    return out


@lru_cache(maxsize=1)
def _woreda_index():
    raw = load_woreda_data().get("woredas") or {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, entry in raw.items():
        key = _key(name)
        if key and isinstance(entry, dict):
            out[key] = dict(entry)
    return out


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_delta(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    lo, hi = _number(value[0]), _number(value[1])
    if lo is None or hi is None:
        return None
    return [lo, hi]


def _valid_band(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    lo, hi = _number(value[0]), _number(value[1])
    if lo is None or hi is None:
        return None
    return [lo, hi]


def meta():
    """Dataset metadata ('_meta' entry)."""
    return dict(load_conditioning_data().get("_meta") or {})


def list_texture_classes():
    """Sorted texture records for UI/reference endpoints."""
    return [dict(_texture_index()[key]) for key in sorted(_texture_index())]


def list_soc_classes():
    """SOC classes ordered by lower bound."""
    return sorted(
        (dict(entry) for entry in _soc_index().values()),
        key=lambda entry: entry.get("min_pct") or 0.0,
    )


def canonical_texture(value):
    """Canonical texture id for a name/alias, else None."""
    key = _key(value)
    if key is None:
        return None
    textures = _texture_index()
    if key in textures:
        return key
    for texture_id, entry in textures.items():
        if any(_key(alias) == key for alias in entry.get("aliases", [])):
            return texture_id
    return None


def canonical_soc_class(value):
    """Canonical SOC class id for a name, else None."""
    key = _key(value)
    if key is None:
        return None
    classes = _soc_index()
    if key in classes:
        return key
    for class_id, entry in classes.items():
        if _key(entry.get("name_en")) == key:
            return class_id
    return None


def soc_class_for_pct(soc_pct):
    """SOC class for a numeric percentage (None when invalid)."""
    pct = _number(soc_pct)
    if pct is None or pct < 0:
        return None
    classes = _soc_index()
    if not classes:
        return "very_low" if pct < 1.0 else "low" if pct < 2.0 else (
            "medium" if pct < 3.0 else "high")
    for class_id, entry in sorted(
            classes.items(), key=lambda item: item[1].get("min_pct") or 0.0):
        min_pct = entry.get("min_pct")
        max_pct = entry.get("max_pct")
        if min_pct is not None and pct < min_pct:
            continue
        if max_pct is not None and pct < max_pct:
            return class_id
        if max_pct is None and (min_pct is None or pct >= min_pct):
            return class_id
    return sorted(classes)[-1] if classes else None


def _woreda_entry(woreda):
    key = _key(woreda)
    if key is None:
        return {}
    return _woreda_index().get(key) or {}


def _soil_type_texture(soil_type):
    if not soil_type:
        return None
    defaults = (load_conditioning_data().get("defaults") or {}).get(
        "soil_type_texture") or {}
    key = _key(soil_type)
    if key is None:
        return None
    for soil_id, texture in defaults.items():
        if _key(soil_id) == key:
            return canonical_texture(texture)
    # Accept display-name spellings through ethiopian_standards when available.
    try:
        import ethiopian_standards as ethio

        canonical = ethio.canonical_soil_type(soil_type)
        if canonical:
            return canonical_texture(defaults.get(canonical))
    except Exception:
        return None
    return None


def _zone_soc(zone):
    if not zone:
        return None
    defaults = (load_conditioning_data().get("defaults") or {}).get(
        "zone_soc") or {}
    key = _key(zone)
    if key is None:
        return None
    for zone_id, soc in defaults.items():
        if _key(zone_id) == key:
            return canonical_soc_class(soc)
    return None


def is_active(texture_class=None, soc_pct=None, soc_class=None, woreda=None,
              enabled=None):
    """True when conditioning may run (explicit inputs, woreda or env flag)."""
    if enabled is None:
        enabled = _env_flag(TEXTURE_ENV)
    return bool(
        enabled
        or texture_class
        or soc_pct is not None
        or soc_class
        or woreda
    )


def resolve_context(texture_class=None, soc_pct=None, soc_class=None,
                    woreda=None, soil_type=None, zone=None,
                    use_soil_type=None, use_defaults=True):
    """Resolve explicit values and fallbacks into one conditioning context.

    Returns a dict with ``texture_class``, ``soc_class``, ``soc_pct``,
    ``woreda``, per-property ``source_level`` and the resolved defaults.
    Unknown explicit values are returned as None (the API can reject them).
    """
    if use_soil_type is None:
        use_soil_type = _env_flag(SOIL_TYPE_ENV, default=False)

    texture = canonical_texture(texture_class) if texture_class else None
    soc = canonical_soc_class(soc_class) if soc_class else None
    if soc_pct is not None:
        soc = soc_class_for_pct(soc_pct)

    sources = {
        "texture": "explicit" if texture else None,
        "soc": "explicit" if (soc_class or soc_pct is not None) else None,
    }
    woreda_entry = _woreda_entry(woreda) if woreda else {}

    if use_defaults:
        if texture is None:
            wt = canonical_texture(woreda_entry.get("texture_class"))
            if wt:
                texture, sources["texture"] = wt, "woreda"
        if soc is None:
            ws = canonical_soc_class(woreda_entry.get("soc_class"))
            if ws:
                soc, sources["soc"] = ws, "woreda"
        if texture is None and use_soil_type:
            st = _soil_type_texture(soil_type)
            if st:
                texture, sources["texture"] = st, "soil_type"
        if soc is None:
            zs = _zone_soc(zone)
            if zs:
                soc, sources["soc"] = zs, "zone"

    return {
        "texture_class": texture,
        "soc_class": soc,
        "soc_pct": _number(soc_pct),
        "woreda": woreda if woreda else None,
        "source_level": sources,
        "woreda_defaults": dict(woreda_entry),
    }


def _apply_delta(band, delta):
    lo, hi = _valid_band(band) or [None, None]
    if lo is None or hi is None:
        return None
    new_lo = max(0.0, lo + delta[0])
    new_hi = max(0.0, hi + delta[1])
    if new_lo > new_hi:
        new_lo, new_hi = new_hi, new_lo
    return [round(new_lo, 2), round(new_hi, 2)]


def _rule_for(col, context, ph=None):
    """First matching (rule_type, key, delta) for one column, else None."""
    rules = load_conditioning_data().get("rules") or {}
    if not isinstance(rules, dict):
        return None
    col_rules = rules.get(col) or {}
    if not isinstance(col_rules, dict):
        return None

    if col == "N":
        soc = context.get("soc_class")
        entry = (col_rules.get("soc") or {}).get(soc) if soc else None
        delta = _valid_delta((entry or {}).get("delta"))
        return ("soc", soc, delta) if delta else None

    if col == "K":
        texture = context.get("texture_class")
        entry = ((col_rules.get("texture") or {}).get(texture)
                 if texture else None)
        delta = _valid_delta((entry or {}).get("delta"))
        return ("texture", texture, delta) if delta else None

    if col == "P":
        texture = context.get("texture_class")
        for entry in col_rules.get("texture_ph") or []:
            if not isinstance(entry, dict):
                continue
            if _key(entry.get("texture")) != texture:
                continue
            ph_lt = _number(entry.get("ph_lt"))
            if ph_lt is not None and (ph is None or float(ph) >= ph_lt):
                continue
            delta = _valid_delta(entry.get("delta"))
            if delta:
                return ("texture_ph", texture, delta)
        return None

    return None


def apply_to_bands(bands, context, ph=None):
    """Apply matching conditioning rules to a band dict.

    Returns ``(bands, metadata)``. ``bands`` is a new dict with N/P/K/ph lists.
    When nothing matches the original bands are returned unchanged (copied)
    and metadata.applied is empty. Never raises on malformed data.
    """
    base = {
        col: list(bands.get(col, (0.0, 0.0))) for col in BAND_COLS
    }
    if not context:
        return base, None

    applied = []
    out = {col: list(base[col]) for col in BAND_COLS}
    for col in ("N", "K", "P"):
        match = _rule_for(col, context, ph=ph)
        if match is None:
            continue
        rule_type, key, delta = match
        adjusted = _apply_delta(out[col], delta)
        if adjusted is None:
            continue
        applied.append(
            {
                "col": col,
                "rule": rule_type,
                "key": key,
                "delta": delta,
                "before": list(out[col]),
                "after": list(adjusted),
            }
        )
        out[col] = adjusted

    meta_data = meta()
    return out, {
        "texture_class": context.get("texture_class"),
        "soc_class": context.get("soc_class"),
        "soc_pct": context.get("soc_pct"),
        "woreda": context.get("woreda"),
        "source_level": dict(context.get("source_level") or {}),
        "applied": applied,
        "base_bands": {col: list(base[col]) for col in BAND_COLS},
        "adjusted_bands": {col: list(out[col]) for col in BAND_COLS},
        "source": (meta_data.get("sources") or [None])[0],
        "review_status": meta_data.get("review_status"),
    }


def reference():
    """Full reference payload for /api/soil-conditioning."""
    meta_data = meta()
    return {
        "meta": meta_data,
        "texture_classes": list_texture_classes(),
        "soc_classes": list_soc_classes(),
        "rules": load_conditioning_data().get("rules") or {},
        "defaults": load_conditioning_data().get("defaults") or {},
        "woreda_count": len(_woreda_index()),
        "enabled": _env_flag(TEXTURE_ENV),
        "use_soil_type": _env_flag(SOIL_TYPE_ENV, default=False),
    }


def clear_cache():
    """Drop cached datasets/indexes (tests or data reloads)."""
    load_conditioning_data.cache_clear()
    load_woreda_data.cache_clear()
    _texture_index.cache_clear()
    _soc_index.cache_clear()
    _woreda_index.cache_clear()
