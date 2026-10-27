"""EthioSIS / ESRI laboratory standards + Ethiopian crop suitability matrix.

Part 1 — laboratory fertility thresholds (native units), converted to the
model's kg/ha input scale:

  Attribute            Poor        Moderate       Good          Method
  Total N (Kjeldahl)   < 0.1 %     0.1 - 0.2 %    > 0.2 %       Kjeldahl
  Available P          < 15 mg/kg  15 - 30 mg/kg  > 30 mg/kg    Mehlich-III / Olsen
  Exchangeable K       < 0.2       0.2 - 0.4      > 0.4 cmolc/kg  NH4OAc
  Soil pH              < 5.5       5.5 - 6.5      6.5 - 7.5     1:2.5 water

Documented unit conversions into the model's kg/ha scale (overridable):

  * N: available kg/ha ~= total N% x N_TO_KG_HA_FACTOR (default 400).
    0-20 cm topsoil weighs ~2,000,000 kg/ha; ~2% of total N mineralizes in a
    season => 0.1% total N ~= 40 kg/ha plant-available N.
  * P: 1 mg/kg ~= P_TO_KG_HA_FACTOR kg/ha (default 2.6: bulk density 1.3
    g/cm3 over the 0-20 cm plow layer => 2,600,000 kg soil/ha, so a lab
    15 mg/kg P reads as 39 kg/ha). Set ETHIO_P_FACTOR=1.0 to restore the
    legacy 1:1 mapping.
  * K: 1 cmolc/kg K ~= 391 mg/kg ~= 391 kg/ha (monovalent K factor 39.1 x 10).

Part 1b — agroecological zone bands (Dataset/agroecological_zones.json):
four Ethiopian zones (highland Nitisols, highland Vertisols, Rift Valley,
arid/semi-arid) with ideal/near N, P, K, pH bands in model units, the
dominant soil constraint, the research-station source, and the
machine-readable logic overrides (lime-first, split N, K bypass, Zn/Fe
flag, ECe gypsum pause).

Part 1c — Ethiopian Lime Requirement Factors (Dataset/lime_requirement.json):
LRF multipliers (t/ha per 1.0 pH unit lift at 90% ECCE, 20 cm plow depth)
per soil type, the 5.0 t/ha single-application ceiling with seasonal
splitting, and the dolomitic-lime adjustment (x0.84). `lime_rate_t` is the
single source of truth shared by ML/engine and fertilizer_cost: with no
soil type it reproduces the legacy generic formula byte-for-byte.

Part 2 — crop suitability matrix (S1 highly suitable / S2 moderately
suitable / S3 marginal / N not suitable) from Ethiopian regional land
suitability guidelines, in Dataset/ethiopian_crop_matrix.json. pH,
temperature (C) and rainfall (mm) bands per crop with Amharic names.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CROP_MATRIX_JSON = BASE_DIR / "Dataset" / "ethiopian_crop_matrix.json"
CROP_BANDS_JSON = BASE_DIR / "ML" / "crop_bands.json"
ZONES_JSON = BASE_DIR / "Dataset" / "agroecological_zones.json"
LIME_JSON = BASE_DIR / "Dataset" / "lime_requirement.json"

PROFILE_ENV = "FERTILITY_PROFILE"
DEFAULT_PROFILE = "ethiosis"
VALID_PROFILES = ("ethiosis", "generic")

N_FACTOR_ENV = "ETHIO_N_FACTOR"
DEFAULT_N_FACTOR = 400.0

P_FACTOR_ENV = "ETHIO_P_FACTOR"
DEFAULT_P_FACTOR = 2.6

K_CMOLC_TO_KG_HA = 391.0

# ---------------------------------------------------------------------------
# Profile selection
# ---------------------------------------------------------------------------


def get_profile():
    """Active fertility profile: "ethiosis" (EthioSIS/ESRI bands, default)
    or "generic" (legacy pre-EthioSIS bands)."""
    value = os.getenv(PROFILE_ENV, DEFAULT_PROFILE).strip().lower()
    return value if value in VALID_PROFILES else DEFAULT_PROFILE


def get_n_factor():
    """kg/ha of plant-available N represented by 1% total N (env-overridable)."""
    try:
        factor = float(os.getenv(N_FACTOR_ENV, ""))
    except ValueError:
        return DEFAULT_N_FACTOR
    if factor <= 0:
        return DEFAULT_N_FACTOR
    return factor


def get_p_factor():
    """kg/ha represented by 1 mg/kg available P (default 2.6: bulk density
    1.3 g/cm3 over the 0-20 cm plow layer). Env-overridable; 1.0 restores
    the legacy 1:1 mapping."""
    try:
        factor = float(os.getenv(P_FACTOR_ENV, ""))
    except ValueError:
        return DEFAULT_P_FACTOR
    if factor <= 0:
        return DEFAULT_P_FACTOR
    return factor


# ---------------------------------------------------------------------------
# Unit converters (lab units -> model kg/ha scale, and back)
# ---------------------------------------------------------------------------


def n_pct_to_kg_ha(pct):
    """Total N (%, Kjeldahl) -> plant-available N kg/ha."""
    return float(pct) * get_n_factor()


def n_kg_ha_to_pct(kg_ha):
    """Plant-available N kg/ha -> total N % (inverse of n_pct_to_kg_ha)."""
    return float(kg_ha) / get_n_factor()


def p_mgkg_to_kg_ha(mg_kg):
    """Available P mg/kg -> kg/ha (bulk-density factor, default 2.6)."""
    return float(mg_kg) * get_p_factor()


def p_kg_ha_to_mgkg(kg_ha):
    """P kg/ha -> mg/kg (inverse of p_mgkg_to_kg_ha)."""
    return float(kg_ha) / get_p_factor()


def k_cmolc_to_kg_ha(cmolc_kg):
    """Exchangeable K cmolc/kg -> kg/ha."""
    return float(cmolc_kg) * K_CMOLC_TO_KG_HA


def k_kg_ha_to_cmolc(kg_ha):
    """K kg/ha -> cmolc/kg (inverse of k_cmolc_to_kg_ha)."""
    return float(kg_ha) / K_CMOLC_TO_KG_HA


# ---------------------------------------------------------------------------
# Band presets (in model units)
# ---------------------------------------------------------------------------

# Legacy generic bands (pre-EthioSIS). Labeling bands match the original
# data_analysis.IDEAL_BANDS; advice ranges match the original
# config.IDEAL_RANGES (K differs: dataset-aware 60-150 for labeling,
# agronomic 100-150 for advice).
GENERIC_LABELING_BANDS = {
    "N": {"ideal": (40.0, 60.0), "near": (30.0, 75.0)},
    "P": {"ideal": (15.0, 30.0), "near": (10.0, 45.0)},
    "K": {"ideal": (60.0, 150.0), "near": (40.0, 175.0)},
    "ph": {"ideal": (6.0, 7.0), "near": (5.5, 8.0)},
}

GENERIC_ADVICE_RANGES = {
    "N": (40.0, 60.0),
    "P": (15.0, 30.0),
    "K": (100.0, 150.0),
    "ph": (6.0, 7.0),
}

# P: EthioSIS good starts at 30 mg/kg; 45 is a pragmatic upper core (above
# that, advice simply reports no P needed). Near = moderate band widened to
# the dataset's usable span. pH: EthioSIS good 6.5-7.5, moderate starts 5.5.
# P bands are held in lab mg/kg and multiplied by the bulk-density factor at
# use time, so the agronomic thresholds are identical under any ETHIO_P_FACTOR.
_ETHIOSIS_P_MG_KG = {"ideal": (30.0, 45.0), "near": (15.0, 60.0)}
_ETHIOSIS_PH = {"ideal": (6.5, 7.5), "near": (5.5, 8.0)}


def ethiosis_labeling_bands(n_factor=None, p_factor=None):
    """EthioSIS-calibrated labeling bands (ideal = good core, near = moderate).

    N bands scale with the conversion factor so ETHIO_N_FACTOR stays in
    control of the N thresholds (default 400: 0.15% -> 60, 0.25% -> 100).
    P bands scale with the bulk-density factor (default 2.6: 15/30/45/60
    mg/kg -> 39/78/117/156 kg/ha).
    """
    if n_factor is None:
        n_factor = get_n_factor()
    if p_factor is None:
        p_factor = get_p_factor()
    p = {
        key: tuple(round(v * p_factor, 2) for v in values)
        for key, values in _ETHIOSIS_P_MG_KG.items()
    }
    return {
        "N": {
            "ideal": (round(0.15 * n_factor, 2), round(0.25 * n_factor, 2)),
            "near": (round(0.10 * n_factor, 2), round(0.30 * n_factor, 2)),
        },
        "P": {"ideal": p["ideal"], "near": p["near"]},
        "K": {
            "ideal": (
                round(0.20 * K_CMOLC_TO_KG_HA, 2),
                round(0.40 * K_CMOLC_TO_KG_HA, 2),
            ),
            "near": (40.0, 200.0),
        },
        "ph": dict(_ETHIOSIS_PH),
    }


def ethiosis_advice_ranges(n_factor=None):
    """EthioSIS advice ranges: the good core of each labeling band."""
    labeling = ethiosis_labeling_bands(n_factor)
    return {col: bands["ideal"] for col, bands in labeling.items()}


def labeling_bands(profile=None):
    """Ideal/near labeling bands for a profile (default: active profile)."""
    profile = profile or get_profile()
    if profile == "generic":
        return {col: dict(bands) for col, bands in GENERIC_LABELING_BANDS.items()}
    return ethiosis_labeling_bands()


def advice_ranges(profile=None):
    """Generic advice ranges (config.IDEAL_RANGES shape) for a profile."""
    profile = profile or get_profile()
    if profile == "generic":
        return {col: tuple(rng) for col, rng in GENERIC_ADVICE_RANGES.items()}
    return ethiosis_advice_ranges()


# ---------------------------------------------------------------------------
# Agroecological zone bands (Part 1b)
# ---------------------------------------------------------------------------

ZONE_COLS = ("N", "P", "K", "ph")


def _zone_key(zone):
    """Case/separator-insensitive zone id ('Highland Vertisols', 'Arid/Semi-Arid')."""
    if not isinstance(zone, str):
        return None
    cleaned = zone.strip().lower()
    for char in ("-", "/", ",", " "):
        cleaned = cleaned.replace(char, "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


@lru_cache(maxsize=1)
def load_zones():
    """Agroecological zone bands keyed by zone id (the '_meta' key excluded)."""
    if not ZONES_JSON.exists():
        return {}
    with open(ZONES_JSON, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {key: value for key, value in raw.items() if not key.startswith("_")}


@lru_cache(maxsize=1)
def zone_meta():
    """Conversion/source metadata for the zone dataset ('_meta' entry)."""
    if not ZONES_JSON.exists():
        return {}
    with open(ZONES_JSON, encoding="utf-8") as fh:
        raw = json.load(fh)
    return dict(raw.get("_meta") or {})


def list_zones():
    """Sorted zone ids (stable order for UI selects)."""
    return sorted(load_zones())


def zone_exists(zone):
    return _zone_key(zone) in load_zones()


def canonical_zone(zone):
    """The canonical zone id for any accepted spelling, else None."""
    key = _zone_key(zone)
    return key if key in load_zones() else None


def zone_info(zone):
    """Full zone record (bands, constraint, source, overrides) or None."""
    return load_zones().get(_zone_key(zone))


def zone_overrides(zone):
    """Machine-readable logic overrides for a zone ({} when none/unknown)."""
    entry = zone_info(zone)
    return dict(entry.get("overrides") or {}) if entry else {}


def zone_labeling_bands(zone):
    """Ideal/near labeling bands for a zone in data_analysis shape.

    Values are stored in model units (kg/ha, pH) in the zone JSON, so no
    unit conversion happens here. Raises ValueError for an unknown zone.
    """
    entry = zone_info(zone)
    if entry is None:
        raise ValueError(
            f"Unknown agroecological zone {zone!r}. Valid: {', '.join(list_zones())}"
        )
    bands = {}
    for col in ZONE_COLS:
        raw = entry["bands"][col]
        bands[col] = {
            "ideal": tuple(float(v) for v in raw["ideal"]),
            "near": tuple(float(v) for v in raw["near"]),
        }
    return bands


def zone_advice_ranges(zone):
    """Ideal ranges for a zone in config.IDEAL_RANGES shape (ideal core only)."""
    return {
        col: bands["ideal"] for col, bands in zone_labeling_bands(zone).items()
    }


def zone_display_name(zone, lang="en"):
    """Localized zone name (falls back to the raw id for unknown zones)."""
    entry = zone_info(zone)
    if not entry:
        return zone
    if lang == "am":
        return entry.get("name_am") or entry.get("name_en") or zone
    if lang == "om":
        return entry.get("name_om") or entry.get("name_en") or zone
    return entry.get("name_en") or zone


# ---------------------------------------------------------------------------
# Ethiopian Lime Requirement Factors (Part 1c)
# ---------------------------------------------------------------------------

# Legacy generic formula constants (pre-LRF, still the default when no soil
# type is given): 2.0 t/ha per 0.5 pH unit (= 4.0 per unit), capped at 10.
LEGACY_LIME_T_PER_HALF_PH = 2.0
LEGACY_LIME_CAP_T_HA = 10.0

LIME_TYPES = ("calcitic", "dolomitic")
DEFAULT_LIME_TYPE = "calcitic"


@lru_cache(maxsize=1)
def load_lime_requirement():
    """Full lime-requirement dataset (soil types + _meta) or {}."""
    if not LIME_JSON.exists():
        return {}
    with open(LIME_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def lime_meta():
    """ECCE / ceiling / dolomitic metadata ('_meta' entry, {} when missing)."""
    return dict(load_lime_requirement().get("_meta") or {})


def list_soil_types():
    """Sorted soil-type ids (stable order for UI selects)."""
    return sorted((load_lime_requirement().get("soil_types") or {}).keys())


def canonical_soil_type(soil_type):
    """The canonical soil-type id for any accepted spelling, else None.

    Accepts the id itself ('nitisol', 'cambisol_luvisol') and display-name
    spellings ('Nitisols', 'Vertisols', 'Andosols', 'Arenosols (sandy
    soils)', Amharic names) via the same separator-insensitive key used for
    zones.
    """
    key = _zone_key(soil_type)
    if key is None:
        return None
    soil_types = load_lime_requirement().get("soil_types") or {}
    if key in soil_types:
        return key
    for soil_id, entry in soil_types.items():
        for name_field in ("name_en", "name_am", "name_om"):
            if _zone_key(entry.get(name_field)) == key:
                return soil_id
        # 'Cambisols / Luvisols' also matches 'cambisol' / 'luvisol'.
        for alias in (entry.get("aliases") or []):
            if _zone_key(alias) == key:
                return soil_id
    return None


def soil_type_exists(soil_type):
    return canonical_soil_type(soil_type) is not None


def soil_type_info(soil_type):
    """Full soil-type record (LRF, buffering, areas, source) or None."""
    key = canonical_soil_type(soil_type)
    if key is None:
        return None
    return dict((load_lime_requirement().get("soil_types") or {})[key])


def soil_type_display_name(soil_type, lang="en"):
    """Localized soil-type name (falls back to the raw id for unknown)."""
    entry = soil_type_info(soil_type)
    if not entry:
        return soil_type
    if lang == "am":
        return entry.get("name_am") or entry.get("name_en") or soil_type
    if lang == "om":
        return entry.get("name_om") or entry.get("name_en") or soil_type
    return entry.get("name_en") or soil_type


def lime_lrf(soil_type):
    """LRF multiplier (t/ha per 1.0 pH unit) or None for unknown soil type."""
    entry = soil_type_info(soil_type)
    return float(entry["lrf"]) if entry else None


def zone_soil_default(zone):
    """Default soil type for an agroecological zone, else None (hint only —
    never auto-applied; callers must pass soil_type explicitly)."""
    zone_id = canonical_zone(zone)
    if zone_id is None:
        return None
    mapping = load_lime_requirement().get("zone_soil_defaults") or {}
    return mapping.get(zone_id)


def normalize_lime_type(lime_type):
    """Canonical lime type ('calcitic'/'dolomitic'), default when empty/None."""
    if not isinstance(lime_type, str) or not lime_type.strip():
        return DEFAULT_LIME_TYPE
    value = lime_type.strip().lower()
    return value if value in LIME_TYPES else DEFAULT_LIME_TYPE


def lime_rate_t(ph_deficit, soil_type=None, lime_type=DEFAULT_LIME_TYPE):
    """Tons of agricultural lime per hectare for a pH-unit deficit.

    Single source of truth shared by ML/engine templates and the fertilizer
    cost estimator (they can never disagree).

      * soil_type None / unknown  -> legacy generic formula, byte-identical
        to the pre-LRF one-liner: round(min(deficit/0.5*2.0, 10.0), 1).
      * soil_type given           -> Ethiopian LRF: deficit x lrf, times the
        dolomitic factor (0.84) when that lime type is selected. Rounded to
        2 decimals; the 5.0 t/ha single-application ceiling is a scheduling
        rule (lime_application_schedule), not a total cap.
    """
    try:
        deficit = max(0.0, float(ph_deficit))
    except (TypeError, ValueError):
        return 0.0
    lrf = lime_lrf(soil_type) if soil_type else None
    if lrf is None:
        return round(min(deficit / 0.5 * LEGACY_LIME_T_PER_HALF_PH,
                         LEGACY_LIME_CAP_T_HA), 1)
    total = deficit * lrf
    if normalize_lime_type(lime_type) == "dolomitic":
        total *= float(lime_meta().get("dolomitic_factor", 0.84))
    return round(total, 2)


def lime_application_schedule(total_t, max_single=None):
    """Season-by-season split of a total lime requirement.

    First season applies at most the Ethiopian single-application ceiling
    (default 5.0 t/ha from the dataset _meta); any remainder moves to the
    next season (and so on). Returns [] for a non-positive total; a total
    within the ceiling is a single season.
    """
    if max_single is None:
        max_single = float(lime_meta().get("max_single_application_t_ha", 5.0))
    try:
        remaining = round(float(total_t), 2)
    except (TypeError, ValueError):
        return []
    if remaining <= 0 or max_single <= 0:
        return []
    schedule = []
    season = 1
    while remaining > 1e-9:
        applied = min(remaining, max_single)
        schedule.append({"season": season, "t_ha": round(applied, 2)})
        remaining = round(remaining - applied, 2)
        season += 1
    return schedule


def lime_plan(ph_deficit, soil_type, lime_type=DEFAULT_LIME_TYPE):
    """Additive response block describing the Ethiopian lime program.

    Shape (consumed by the /api/predict `lime_plan` extra):
      {soil_type, name_en, name_am, name_om, lrf, ecce_pct, lime_type, ph_deficit,
       total_t_ha, max_single_t_ha, split_required, schedule, source}
    Returns None when the soil type is unknown.
    """
    info = soil_type_info(soil_type)
    if info is None:
        return None
    key = canonical_soil_type(soil_type)
    deficit = max(0.0, float(ph_deficit or 0.0))
    total = lime_rate_t(deficit, key, lime_type)
    max_single = float(lime_meta().get("max_single_application_t_ha", 5.0))
    schedule = lime_application_schedule(total, max_single)
    return {
        "soil_type": key,
        "name_en": info.get("name_en"),
        "name_am": info.get("name_am"),
        "name_om": info.get("name_om"),
        "lrf": float(info["lrf"]),
        "ecce_pct": float(lime_meta().get("ecce_pct", 90)),
        "lime_type": normalize_lime_type(lime_type),
        "ph_deficit": round(deficit, 2),
        "total_t_ha": total,
        "max_single_t_ha": max_single,
        "split_required": total > max_single,
        "schedule": schedule,
        "source": info.get("source"),
    }


# ---------------------------------------------------------------------------
# Crop suitability matrix (Part 2)
# ---------------------------------------------------------------------------

CLASS_RANK = {"S1": 0, "S2": 1, "S3": 2, "N": 3}


@lru_cache(maxsize=1)
def load_crop_matrix():
    """Full crop suitability matrix keyed by crop slug (ML slug when the crop
    is part of the 22-crop model)."""
    if not CROP_MATRIX_JSON.exists():
        return {}
    with open(CROP_MATRIX_JSON, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def ml_crop_keys():
    """Crops that have dataset-derived bands (the 22-crop ML model)."""
    if not CROP_BANDS_JSON.exists():
        return frozenset()
    with open(CROP_BANDS_JSON, encoding="utf-8") as fh:
        return frozenset(json.load(fh).keys())


def ethiopian_staples():
    """Matrix crops that are NOT part of the 22-crop ML model (rule-only)."""
    return sorted(
        crop
        for crop, entry in load_crop_matrix().items()
        if crop not in ml_crop_keys()
    )


def _value_in_ranges(value, ranges):
    for rng in ranges:
        lo = rng.get("min")
        hi = rng.get("max")
        if lo is not None and value < lo:
            continue
        if hi is not None and value > hi:
            continue
        return True
    return False


def _value_in_exclusions(value, rules):
    for rule in rules:
        lt = rule.get("lt")
        gt = rule.get("gt")
        if lt is not None and value < lt:
            return True
        if gt is not None and value > gt:
            return True
    return False


def _fmt_band(rng):
    lo, hi = rng.get("min"), rng.get("max")
    if lo is not None and hi is not None:
        return f"{lo}-{hi}"
    if lo is not None:
        return f">={lo}"
    return f"<={hi}"


def _fmt_exclusion(rule):
    if rule.get("lt") is not None:
        return f"< {rule['lt']}"
    return f"> {rule['gt']}"


def crop_suitability(crop, ph=None, temperature=None, rainfall=None):
    """Ethiopian land-suitability class for a crop under given conditions.

    Evaluates only the parameters provided (at least one required):
    pH (unitless), temperature (C), rainfall (mm). Worst parameter wins:
    any N violation -> overall N; otherwise S1 requires every provided
    parameter inside its S1 band, S2 when all inside S1/S2, else S3.

    Returns None for unknown crops or when no parameter is given, else:
        {crop, class, param_class, checks: [{param, value, class, band}],
         reasons: [str, ...]}  with class in S1/S2/S3/N.
    """
    entry = load_crop_matrix().get(crop)
    if not entry:
        return None
    values = {
        "ph": ph,
        "temperature": temperature,
        "rainfall": rainfall,
    }
    present = {k: v for k, v in values.items() if v is not None}
    if not present:
        return None

    param_class = {}
    checks = []
    reasons = []
    for param, value in present.items():
        value = float(value)
        result = None
        band = None
        exclusions = entry.get("N", {}).get(param)
        if exclusions and _value_in_exclusions(value, exclusions):
            result = "N"
            band = " or ".join(_fmt_exclusion(r) for r in exclusions)
            reasons.append(
                f"{param} {value:g} violates the not-suitable bound ({band})"
            )
        else:
            s1_ranges = entry.get("S1", {}).get(param) or []
            s2_ranges = entry.get("S2", {}).get(param) or []
            if _value_in_ranges(value, s1_ranges):
                result = "S1"
                band = ", ".join(_fmt_band(r) for r in s1_ranges)
                reasons.append(f"{param} {value:g} matches S1 ({band})")
            elif _value_in_ranges(value, s2_ranges):
                result = "S2"
                band = ", ".join(_fmt_band(r) for r in s2_ranges)
                reasons.append(f"{param} {value:g} matches S2 ({band})")
            else:
                result = "S3"
                s1_text = ", ".join(_fmt_band(r) for r in s1_ranges) or "n/a"
                s2_text = ", ".join(_fmt_band(r) for r in s2_ranges) or "n/a"
                band = f"S1 {s1_text} / S2 {s2_text}"
                reasons.append(
                    f"{param} {value:g} is outside the S1 ({s1_text}) and "
                    f"S2 ({s2_text}) bands"
                )
        param_class[param] = result
        checks.append(
            {"param": param, "value": value, "class": result, "band": band}
        )

    overall = max(param_class.values(), key=lambda c: CLASS_RANK[c])
    return {
        "crop": crop,
        "class": overall,
        "param_class": param_class,
        "checks": checks,
        "reasons": reasons,
    }
