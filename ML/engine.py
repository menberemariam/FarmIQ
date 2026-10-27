"""Deterministic soil analysis + recommendation engine.

Every amount returned here is *calculated* from the measured nutrient deficit
(kg/ha) using standard fertilizer nutrient contents, never guessed. When a crop
is provided, its real per-crop bands (ML/crop_bands.json, derived from the
dataset) are used instead of the generic agronomic ranges. When an
agroecological zone is provided, the Ethiopian zone bands
(Dataset/agroecological_zones.json) take precedence over both, so advice
follows the local (EIAR) context instead of the global dataset.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

import config
import ethiopian_standards as ethio
import fertilizer_catalog
import seasonal_bands
import soil_conditioning
import variety_bands
from i18n import CROP_NAMES_AM, CROP_NAMES_OM, normalize_amharic, normalize_qubee, translate

STATUS_CANONICAL = {
    "deficient": "deficient",
    "low": "deficient",
    "insufficient": "deficient",
    "below": "deficient",
    "deficit": "deficient",
    "ample": "ample",
    "high": "ample",
    "excess": "ample",
    "excessive": "ample",
    "rich": "ample",
    "above": "ample",
    "adequate": "adequate",
    "optimal": "adequate",
    "good": "adequate",
    "normal": "adequate",
    "sufficient": "adequate",
    "balanced": "adequate",
    "maintenance": "adequate",
    # Afaan Oromoo status words (Dataset/terminology_om.json status_words).
    "hanqina": "deficient",
    "hanqataa": "deficient",
    "gahaa": "adequate",
    "gar-malee hedduu": "ample",
    "ol-aanaa": "ample",
}


def normalize_status(status):
    """Map any LLM status phrasing to deficient/ample/adequate (or None)."""
    if not isinstance(status, str):
        return None
    return STATUS_CANONICAL.get(status.strip().lower())


# Exact (case/whitespace-insensitive) element keys only. Deliberately no fuzzy
# matching: "nitrate" or "phosphorous" must not silently become a different
# element.
AGRO_KEY_ALIASES = {name.strip().lower(): name for name in config.ELEMENTS}


def _canonical_element_key(key):
    if not isinstance(key, str):
        return None
    return AGRO_KEY_ALIASES.get(key.strip().lower())


def coerce_element_record(value):
    """Return a usable {status, solution} record, or None.

    Non-dicts, missing/blank statuses and empty solutions are rejected rather
    than passed to the merge step.
    """
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    solution = value.get("solution")
    if not isinstance(status, str) or not status.strip():
        return None
    if not isinstance(solution, str) or not solution.strip():
        return None
    return {"status": status.strip(), "solution": solution.strip()}


def normalize_agronomic_keys(parsed, stats=None):
    """Keep the four canonical element keys, fix casing/whitespace, drop junk.

    Idempotent. Unknown keys and malformed records never reach
    ``merge_recommendations``. When ``stats`` is a dict, ``stats["rejected"]``
    counts the dropped entries (used by the optional LLM telemetry).
    """
    if not isinstance(parsed, dict):
        return {}
    normalized = {}
    for key, value in parsed.items():
        canonical = _canonical_element_key(key)
        record = coerce_element_record(value) if canonical is not None else None
        if canonical is None or record is None:
            if isinstance(stats, dict):
                stats["rejected"] = stats.get("rejected", 0) + 1
            continue
        normalized[canonical] = record
    return normalized


def _load_crop_bands():
    path = Path(config.CROP_BANDS_JSON)
    if not path.exists():
        return {}
    with open(path) as fh:
        return json.load(fh)


@lru_cache(maxsize=8)
def _crop_bands_cache():
    return _load_crop_bands()


def _resolve_bands(crop=None, zone=None, variety=None, region=None, season=None,
                   texture_class=None, soc_pct=None, soc_class=None,
                   woreda=None, soil_type=None, ph=None):
    """Resolve effective bands and their provenance.

    Base precedence is the existing variety > zone > crop > generic path.
    Enhancement E conditioning (SOC -> N, texture -> K/P) is applied after
    that base and before the seasonal N modifier; it only runs when an
    explicit texture/SOC/woreda input (or the env flag) activates it, so the
    legacy path is byte-identical when those inputs are absent.
    A supplied `season` is applied afterwards as an N-only modifier:
    variety/zone P/K/pH are preserved, while a crop/generic base receives the
    seasonal Ethiopian crop baseline. Unknown seasons/crops leave the base
    bands untouched so direct engine callers keep legacy behavior; the API
    validates and rejects unsupported combinations explicitly.
    """
    variety_match = None
    if variety:
        variety_match = variety_bands.resolve_variety(
            crop, region=region, variety=variety)
    if variety_match is not None:
        bands, source = variety_match["bands"], "variety"
    elif zone and ethio.zone_exists(zone):
        bands, source = ethio.zone_advice_ranges(zone), "zone"
    elif crop and crop != "generic" and crop.lower() in _crop_bands_cache():
        bands, source = _crop_bands_cache()[crop.lower()], "crop"
    else:
        bands, source = config.IDEAL_RANGES, "generic"

    conditioning_meta = None
    if soil_conditioning.is_active(
            texture_class=texture_class, soc_pct=soc_pct,
            soc_class=soc_class, woreda=woreda):
        context = soil_conditioning.resolve_context(
            texture_class=texture_class, soc_pct=soc_pct,
            soc_class=soc_class, woreda=woreda, soil_type=soil_type,
            zone=zone,
        )
        bands, conditioning_meta = soil_conditioning.apply_to_bands(
            bands, context, ph=ph)

    season_meta = None
    if season:
        bands, season_meta = seasonal_bands.apply_season_to_bands(
            bands, source, crop, season)
        if season_meta is not None and source in ("crop", "generic"):
            source = "season"
    return bands, source, variety_match, season_meta, conditioning_meta


def get_bands(crop=None, zone=None, variety=None, region=None, season=None,
              texture_class=None, soc_pct=None, soc_class=None, woreda=None,
              soil_type=None, ph=None):
    """Ideal bands for this analysis.

    Precedence:
      1. `variety` — the explicit crop x production-area x variety lookup
         (Dataset/variety_bands.json). This is the most specific layer and
         is only used when the caller names a variety; unknown varieties
         fall through silently so the API can reject them explicitly.
      2. `zone` — the Ethiopian agroecological zone bands (EIAR research
         centers) replace BOTH the generic ranges and the Kaggle crop bands,
         so analysis follows the Ethiopian context instead of the global
         dataset. Unknown/empty zone ids fall through silently.
      3. `crop` — the dataset-derived per-crop bands (legacy behavior).
      4. config.IDEAL_RANGES — the profile-selected generic ranges.

    `season` is an optional N-only modifier applied after the base layer:
    Meher keeps a variety/zone N band, Belg scales it by the crop's Belg/Meher
    factor, and a crop/generic base uses the seasonal Ethiopian baseline for
    N plus P/K/pH. Without `season` the result is exactly the legacy path.

    Enhancement E `texture_class` / `soc_pct` / `soc_class` / `woreda`
    optionally adjust the resolved bands before the season layer. Without
    those inputs (and without SOIL_CONDITIONING_ENABLED) the result is
    byte-identical to the legacy path.
    """
    bands, _, _, _, _ = _resolve_bands(
        crop=crop, zone=zone, variety=variety, region=region, season=season,
        texture_class=texture_class, soc_pct=soc_pct,
        soc_class=soc_class, woreda=woreda, soil_type=soil_type, ph=ph)
    return bands


def available_crops():
    """Sorted list of crops with real per-crop bands."""
    return sorted(_crop_bands_cache().keys())


@lru_cache(maxsize=1)
def climate_medians():
    """Median climate of the crop dataset — fallback when the caller has no
    temperature/humidity/rainfall readings (mirrors the API's climate defaults)."""
    df = pd.read_csv(config.CROP_DATA_CSV)
    return {
        "temperature": float(df["temperature"].median()),
        "humidity": float(df["humidity"].median()),
        "rainfall": float(df["rainfall"].median()),
    }


def crop_feature_row(N, P, K, ph, temperature=None, humidity=None, rainfall=None):
    """One-row DataFrame with exactly config.CROP_FEATURES columns, in model
    order. Missing climate values fall back to the dataset medians.

    The crop model was fit on 7 columns; feeding it only N/P/K/ph raises
    ValueError. Always build its input through this helper.
    """
    climate = climate_medians()
    return pd.DataFrame(
        [
            {
                "N": N,
                "P": P,
                "K": K,
                "temperature": temperature
                if temperature is not None
                else climate["temperature"],
                "humidity": humidity if humidity is not None else climate["humidity"],
                "ph": ph,
                "rainfall": rainfall if rainfall is not None else climate["rainfall"],
            }
        ]
    )[config.CROP_FEATURES]


def training_range_warnings(N, P, K, ph, lang="en"):
    """Extrapolation warnings for inputs outside the model's training ranges.

    Returns [{field, value, range, message}] — message localized via the
    existing i18n "out_of_range" key. Empty list when everything is in range.
    """
    warnings = []
    for name, value in (("N", N), ("P", P), ("K", K), ("ph", ph)):
        lo, hi = config.UI_INPUT_RANGES[name]
        if value < lo or value > hi:
            warnings.append(
                {
                    "field": name,
                    "value": value,
                    "range": [lo, hi],
                    "message": translate(
                        lang, "out_of_range", value=f"{name}={value}", lo=lo, hi=hi
                    ),
                }
            )
    return warnings


def analyze_soil(N, P, K, ph, crop=None, zone=None, ec_e=None, esp=None,
                 soil_type=None, lime_type=None, variety=None, region=None,
                 season=None, texture_class=None, soc_pct=None, soc_class=None,
                 woreda=None):
    """Compare each nutrient against the ideal bands for the given crop/zone.

    Returns a list of dicts:
        {element, col, value, band, status (deficient/ample/adequate), deficit,
         crop, zone, soil_type, lime_type}
    where deficit is the kg/ha shortfall (or excess) vs the band edge. When a
    zone is given its EIAR bands drive the comparison (see get_bands), and the
    zone's machine-readable overrides are applied:

      * P-efficiency (acid highlands) — below the pH threshold, P deficits are
        divided by the fixation factor (Fe/Al oxides lock applied P);
      * K bypass (Vertisols) — soil K at or above the threshold is treated as
        sufficient, no K fertilizer;
      * salinity (arid) — when `ec_e` exceeds the zone threshold, N/P/K
        deficits are zeroed (fertilizer would burn under salt stress) and the
        items are flagged `suppressed` so recommendations switch to gypsum /
        leaching guidance. `esp` > threshold adds a sodicity warning.

    `soil_type` / `lime_type` are the Ethiopian lime-requirement inputs
    (Part 1c): they never change status or nutrient math — only the lime
    tonnage and the additive lime note in `recommend_soil`. Unknown soil
    types canonicalize to None so the legacy lime formula keeps applying.

    `variety` / `region` select the explicit Ethiopian variety band layer
    (Dataset/variety_bands.json). This is opt-in: without a variety the band
    precedence is exactly the legacy zone -> crop -> generic path. When a
    variety AND a zone are supplied, the variety bands drive the comparison
    while the zone overrides still apply below.

    `season` (meher/belg) is an N-only modifier layered on top
    (Dataset/seasonal_bands.json). P/K/pH keep the variety/zone/crop baseline;
    Meher preserves a variety/zone N band, Belg scales it, and a crop/generic
    base uses the seasonal Ethiopian baseline. Without `season` behavior is
    unchanged.

    Enhancement E `texture_class` / `soc_pct` / `soc_class` / `woreda`
    optionally adjust the base bands before the season layer
    (Dataset/soil_conditioning.json). They are opt-in: absent inputs keep
    the legacy analysis byte-identical. The returned items carry the
    conditioning provenance for additive API/report blocks.
    """
    bands, bands_source, variety_match, season_meta, conditioning_meta = \
        _resolve_bands(
            crop=crop, zone=zone, variety=variety, region=region, season=season,
            texture_class=texture_class, soc_pct=soc_pct,
            soc_class=soc_class, woreda=woreda, soil_type=soil_type, ph=ph)
    values = {"N": N, "P": P, "K": K, "ph": ph}
    overrides = ethio.zone_overrides(zone) if zone else {}
    soil_type = ethio.canonical_soil_type(soil_type) if soil_type else None
    lime_type = ethio.normalize_lime_type(lime_type) if lime_type else None
    out = []
    for element, col in config.ELEMENT_TO_COL.items():
        lo, hi = bands[col]
        v = values[col]
        if lo <= v <= hi:
            status, deficit = "adequate", 0.0
        elif v < lo:
            status, deficit = "deficient", lo - v
        else:
            status, deficit = "ample", v - hi
        item = {
            "element": element,
            "col": col,
            "value": v,
            "band": (lo, hi),
            "status": status,
            "deficit": deficit,
            "crop": crop,
            "zone": zone,
            "soil_type": soil_type,
            "lime_type": lime_type,
            "bands_source": bands_source,
            "variety_id": (
                variety_match["id"] if variety_match else None
            ),
            "variety_label": (
                variety_match["variety"] if variety_match else None
            ),
            "region_label": (
                variety_match.get("region_label")
                if variety_match else None
            ),
            "season": season_meta["season"] if season_meta else None,
            "season_label": (
                season_meta["label"] if season_meta else None
            ),
            "season_n_band": (
                tuple(season_meta["n_band"]) if season_meta else None
            ),
            "season_base_n_band": (
                tuple(season_meta["base_n_band"]) if season_meta else None
            ),
            "season_table_n_band": (
                tuple(season_meta["table_n_band"]) if season_meta else None
            ),
            "season_n_factor": (
                season_meta["n_factor"] if season_meta else None
            ),
            "season_note": (
                season_meta["note"] if season_meta else None
            ),
            "season_applied_n_source": (
                season_meta["applied_n_source"] if season_meta else None
            ),
        }
        if conditioning_meta is not None:
            item["soil_conditioning"] = conditioning_meta
        out.append(item)
    return _apply_zone_overrides(out, overrides, ph=ph, ec_e=ec_e, esp=esp)


def _apply_zone_overrides(items, overrides, ph=None, ec_e=None, esp=None):
    """Apply the zone override rules to analyzed items (Phase 3).

    Pure function of (items, overrides, optional salinity inputs) so the same
    rules can be unit-tested with synthetic overrides. Mutates and returns
    the item dicts; no-ops when the zone defines no matching override.
    """
    if not overrides:
        return items

    pause = overrides.get("ec_e_pause_ds_m")
    saline = ec_e is not None and pause is not None and ec_e > pause

    for item in items:
        col = item["col"]
        if col == "P" and item["status"] == "deficient":
            threshold = overrides.get("p_efficiency_ph")
            factor = overrides.get("p_efficiency_factor")
            if threshold is not None and factor and ph is not None \
                    and ph < threshold:
                item["deficit"] = round(item["deficit"] / factor, 1)
                item["p_efficiency"] = True
        if col == "K":
            bypass = overrides.get("k_bypass_above")
            if bypass is not None and item["value"] >= bypass:
                item["status"] = "adequate"
                item["deficit"] = 0.0
                item["k_bypass"] = True
        if saline and col in ("N", "P", "K"):
            # Honest status ("deficient") but zero deficit: the crop cannot
            # take nutrients up under salt stress, so no purchase is advised.
            item["deficit"] = 0.0
            item["suppressed"] = True
            item["ec_e"] = ec_e
            item["esp"] = esp
    return items


def build_soil_row(row_df, model):
    """Align a soil feature row to the model's training schema.

    Legacy models expect exactly N/P/K/ph (the zone column is dropped).
    Zone-aware models (SOIL_MODEL_PROFILE=agroecology) were trained with
    one-hot zone_<name> columns; a missing zone stays all-zero, which the
    model treats as an unseen-zone average. v6 models
    (agroecology_v6) additionally carry soil_type_<name> columns; a row
    without a soil type stays all-zero there too. Anything the model does
    not declare is dropped by the reindex.
    """
    feature_cols = getattr(model, "feature_cols_", None) or config.FEATURE_COLS
    if any(col.startswith("zone_") for col in feature_cols) and "zone" in row_df:
        dummies = pd.get_dummies(row_df["zone"], prefix="zone").astype(int)
        row_df = pd.concat(
            [row_df.drop(columns=["zone"]), dummies], axis=1)
    if (any(col.startswith("soil_type_") for col in feature_cols)
            and "soil_type" in row_df):
        dummies = pd.get_dummies(row_df["soil_type"], prefix="soil_type")
        # Pad every known soil type so column order is training-stable.
        for soil_id in ethio.list_soil_types():
            col = f"soil_type_{soil_id}"
            if col not in dummies.columns:
                dummies[col] = 0
        dummies = dummies[[f"soil_type_{s}" for s in ethio.list_soil_types()]]
        row_df = pd.concat([row_df.drop(columns=["soil_type"]), dummies], axis=1)
    return row_df.reindex(columns=list(feature_cols), fill_value=0)


def _fertilizer_amount(deficit_kg, nutrient_content):
    """kg/ha of fertilizer needed to cover deficit at given nutrient content (0-1)."""
    amount = deficit_kg / nutrient_content
    return round(min(max(amount, 0), 500), 1)


def _for_crop(item, lang="en"):
    crop = item.get("crop")
    if not crop:
        return ""
    if lang == "am":
        return f" ለ{CROP_NAMES_AM.get(crop, crop)}"
    if lang == "om":
        return f" {CROP_NAMES_OM.get(crop, crop)}"
    return f" for {crop}"


def _band_text(item):
    lo, hi = item["band"]
    return f"{lo:.0f}-{hi:.0f}" if item["col"] != "ph" else f"{lo:.1f}-{hi:.1f}"


# Display labels for products that are NOT in Dataset/fertilizer_products.json
# (catalog products resolve their label from the catalog itself). Keep the
# strings in sync with fertilizer_cost.PRODUCT_LABELS.
_PRODUCT_LABELS = {
    "urea": "Urea (46% N)",
    "dap": "DAP (18% N, 46% P2O5)",
    "potassium_sulfate": "Potassium sulfate (50% K2O)",
    "lime": "Agricultural lime (CaCO3)",
    "sulfur": "Elemental sulfur",
}

# Catalog products that have their own deficient templates (Phase 2 flag).
_CATALOG_TEMPLATE_PRODUCTS = ("nps", "npsb", "blended")


def _label_for(product_id):
    """Human label for any product (catalog first, then local fallback)."""
    if product_id is None:
        return None
    product = fertilizer_catalog.get_product(product_id)
    if product is not None:
        return product["label"]
    return _PRODUCT_LABELS.get(product_id, product_id)


def _template_key(templates, kind, status, product):
    """Template lookup: catalog variant (p_deficient_nps ...) when the
    selected product has one, else the classic key, else *_adequate."""
    base = f"{kind}_{status}"
    if product in _CATALOG_TEMPLATE_PRODUCTS:
        variant = f"{base}_{product}"
        if variant in templates:
            return variant
    if base in templates:
        return base
    return f"{kind}_adequate"


_EN_TEMPLATES = {
    "n_deficient": (
        "Nitrogen is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
        "Apply urea (46% N) at approximately {urea} kg/ha, worked into the soil and "
        "split into two top-dressings to avoid losses, or use well-rotted cow dung / "
        "compost at roughly {compost:.0f} kg/ha. Incorporate the material before sowing "
        "and re-test the soil after 6 weeks; over-application wastes money and causes "
        "leaching into groundwater."
    ),
    "n_ample": (
        "Nitrogen is {deficit:.1f} kg/ha above the ideal band{crop} ({band} kg/ha). Do not "
        "add more nitrogen fertilizer this season. Excess N leaches nitrate into water "
        "bodies and encourages lush, disease-prone growth. Reduce or skip top-dressing, "
        "grow a catch crop such as mustard or ryegrass to scavenge surplus N, and test "
        "again before the next planting."
    ),
    "n_adequate": (
        "Nitrogen is within the ideal band{crop} ({band} kg/ha). Apply only maintenance doses: "
        "a light top-dressing of 20-25 kg N/ha at the tillering/growth stage, or an "
        "equivalent sidedress of well-decomposed compost, and re-test the soil each season "
        "to stay inside the band."
    ),
    "p_deficient": (
        "Phosphorus is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
        "Apply DAP (18% N, 46% P2O5) at approximately {dap} kg/ha placed in the seed "
        "furrow, or use bone meal / rock phosphate with compost for slower release. Keep "
        "soil pH near 6-7, since P fixation locks up phosphate in acidic soils. Re-test "
        "the soil next season before repeating the application."
    ),
    "p_deficient_nps": (
        "Phosphorus is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
        "Apply NPS (19% N, 38% P2O5, 7% S) at approximately {nps} kg/ha as a basal "
        "fertilizer at planting, broadcast and incorporated into the soil immediately — "
        "never left on the surface, since the ammonium-N volatilizes. NPS also supplies "
        "the sulfur that EthioSIS maps show deficient across the highlands. Do not apply "
        "under dry-season moisture stress; keep soil pH near 6-7 and re-test next season "
        "before repeating."
    ),
    "p_deficient_npsb": (
        "Phosphorus is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha) and "
        "the soil test shows boron deficiency. Apply NPSB (18.5% N, 36.9% P2O5, 6.8% S, "
        "0.4% B) at approximately {npsb} kg/ha as a strict basal application with uniform "
        "broadcast or precise row placement — concentrated granules burn seedlings "
        "(boron phytotoxicity). Incorporate immediately, keep soil pH near 6-7, and "
        "re-test sulfur and boron next season before repeating."
    ),
    "p_ample": (
        "Phosphorus is {deficit:.1f} kg/ha above the ideal band{crop} ({band} kg/ha). "
        "Soils are P-rich: stop phosphorus fertilization entirely. Excess P runs off into "
        "water bodies and causes eutrophication. Improve structure with gypsum or lime, "
        "and grow P-absorbing crops or cover crops to draw down surplus P over two to "
        "three seasons."
    ),
    "p_adequate": (
        "Phosphorus is within the ideal band{crop} ({band} kg/ha). Apply maintenance doses "
        "only: a modest starter dressing of 20-30 kg P2O5/ha at sowing for most field crops, "
        "and avoid routine broadcast applications. Re-test every season to keep the level in "
        "the band."
    ),
    "k_deficient": (
        "Potassium is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
        "Apply potassium sulfate (50% K2O) at approximately {k_sulfate} kg/ha, or muriate "
        "of potash adjusted for its K2O content, banded near the root zone before sowing. "
        "Alternatively incorporate wood ash or composted crop residues for organic K. "
        "Re-test after harvest, since K is mobile in many sandy soils and needs regular "
        "replenishment."
    ),
    "k_deficient_blended": (
        "Potassium is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
        "Apply the regional NPSKB blend (13.4% N, 26.8% P2O5, 14.1% K2O, plus S, Zn and "
        "B) at approximately {blend} kg/ha directly into the planting furrow for deep-root "
        "placement — the blend is formulated for K-leached western and southwestern "
        "highland soils. Avoid it in highly alkaline Rift Valley soils unless acidic "
        "modifiers are present. Re-test after harvest, since K is mobile and needs "
        "regular replenishment."
    ),
    "k_ample": (
        "Potassium is {deficit:.1f} kg/ha above the ideal band{crop} ({band} kg/ha). "
        "Reduce potassium applications this season; excess K can interfere with calcium "
        "and magnesium uptake and raises soil salinity risk. Use only K-free fertilizers "
        "(e.g., urea and DAP), monitor leaf symptoms, and re-test the soil before the "
        "next crop cycle."
    ),
    "k_adequate": (
        "Potassium is within the ideal band{crop} ({band} kg/ha). Apply maintenance doses: "
        "30-40 kg K2O/ha per season for most crops, or replace it with crop residues and "
        "farmyard manure. Avoid surplus K in rainfed sandy soils where it leaches below the "
        "root zone."
    ),
    "ph_deficient": (
        "Soil pH is {deficit:.2f} units below the ideal band{crop} ({band}). Raise it by "
        "applying finely ground agricultural lime (CaCO3) at roughly {lime_t} t/ha, "
        "worked into the top 15-20 cm at least 3 months before sowing. Split heavy doses "
        "into two applications to avoid shocking the soil biology, and re-test pH after "
        "each season; acidic soil locks up phosphorus and makes aluminum toxic to roots."
    ),
    "ph_ample": (
        "Soil pH is {deficit:.2f} units above the ideal band{crop} ({band}). Lower it by "
        "applying elemental sulfur at approximately {s_t} t/ha, incorporated into the "
        "topsoil in split doses, or by working in acidifying organic matter such as pine "
        "needles, peat or composted leaf litter. High pH ties up iron, zinc and "
        "phosphorus, causing chlorosis; re-test before each new application."
    ),
    "ph_adequate": (
        "Soil pH is within the ideal band{crop} ({band}), where most nutrients are readily "
        "available. Maintain it with regular applications of compost and lime only if a "
        "re-test shows drift toward acidity; avoid blanket liming of already neutral soil."
    ),
}

_AM_TEMPLATES = {
    "n_deficient": (
        "ናይትሮጅን (Nitrogen, N){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ያነሰ ነው። ዩሪያ (Urea, 46% N) በግምት {urea} ኪግ/ሄክታር ይተግብሩ፣ ወደ አፈር ውስጥ በማቀላቀልና "
        "በሁለት ክፍል በመከፋፈል ንጥረ-ነገሩ እንዳይጠፋ ያድርጉ፣ ወይም የበሰበሰ የእንስሳት ፍግ (animal "
        "manure)/ኮምፖስት (compost) በግምት {compost:.0f} ኪግ/ሄክታር ይጠቀሙ። ከመዝራት በፊት ከአፈር ጋር "
        "ያቀላቅሉና ከ6 ሳምንታት በኋላ አፈሩን እንደገና ይፈትሹ። ከመጠን በላይ መጠቀም ገንዘብ ያባክናልና "
        "ናይትሬት (nitrate) ወደ የከርሰ ምድር ውሃ ይፈስሳል።"
    ),
    "n_ample": (
        "ናይትሮጅን (Nitrogen, N){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ይበልጣል። በዚህ ወቅት ተጨማሪ የናይትሮጅን ማዳበሪያ (nitrogen fertilizer) አይጠቀሙ። ከመጠን በላይ "
        "ናይትሮጅን እንደ ናይትሬት (nitrate) ወደ ውሃ ይፈስሳልና ተክሉንም ለበሽታ ያጋልጣል። የላይኛውን "
        "ማዳበሪያ ይቀንሱ ወይም ይተዉት፣ ትርፍ ናይትሮጅንን የሚወስድ መካከለኛ ሰብል (catch crop) እንደ "
        "ሰናፍጭ ወይም ራይግራስ ያልሙ፣ ከቀጣዩ ተከላ በፊት አፈሩን እንደገና ይፈትሹ።"
    ),
    "n_adequate": (
        "ናይትሮጅን (Nitrogen, N) በሚፈለገው ተስማሚ ክልል{crop} ({band} ኪግ/ሄክታር) ውስጥ ነው። "
        "የጥገና መጠን ብቻ ይተግብሩ፦ በእድገት ደረጃ ላይ 20–25 ኪግ/ሄክታር ናይትሮጅን በቀላል የላይኛው "
        "መርጨት ወይም በበሰበሰ ኮምፖስት (compost) ይስጡ፣ በክልሉ ውስጥ ለመቆየት በየወቅቱ አፈሩን ይፈትሹ።"
    ),
    "p_deficient": (
        "ፎስፎረስ (Phosphorus, P){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ያነሰ ነው። ዲኤፒ (DAP, 18% N – 46% P₂O₅) በግምት {dap} ኪግ/ሄክታር በዘር መስመር ይተግብሩ፣ ወይም "
        "በኢትዮጵያ ገበያ የሚገኘውን ኤንፒኤስ (NPS, 19-38-7) ከኮምፖስት (compost) ጋር ይጠቀሙ። የአፈር ፒኤች "
        "(pH) ከ6–7 መሆኑን ያረጋግጡ፣ ምክንያቱም አሲዳማ አፈር ፎስፎረስን ይይዛልና። በሚቀጥለው ወቅት "
        "ከመድገምዎ በፊት አፈሩን እንደገና ይፈትሹ።"
    ),
    "p_deficient_nps": (
        "ፎስፎረስ (Phosphorus, P){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ያነሰ ነው። ኤንፒኤስ (NPS, 19% N – 38% P₂O₅ – 7% S) በግምት {nps} ኪግ/ሄክታር በመዝራት ጊዜ በአፈር "
        "ውስጥ በማቀላቀል ይተግብሩ፤ በአፈር ላይ እንዳይተከል ያድርጉ፣ አመንኩት ናይትሮጅን (ammonium-N) ይተናልና። "
        "ኤንፒኤስ ኢትዮሲስ (EthioSIS) በሰማያዊ ቁጭባዎች ላይ የሚያሳየውን የሰልፈር (Sulfur) እጥረት ይሞላል። "
        "በድርቅ ወቅት ወይም አፈሩ እርጥብነት ሲጠፋ አይተገብሩ፤ ፒኤች ከ6–7 መሆኑን ያረጋግጡ፣ በሚቀጥለው "
        "ወቅት ከመድገምዎ በፊት አፈሩን እንደገና ይፈትሹ።"
    ),
    "p_deficient_npsb": (
        "ፎስፎረስ (Phosphorus, P){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ያነሰ ነው፤ አፈሩ ቦሮን (Boron) እጥረት አለው። ኤንፒኤስቢ (NPSB, 18.5% N – 36.9% P₂O₅ – 6.8% S – "
        "0.4% B) በግምት {npsb} ኪግ/ሄክታር በመዝራት ጊዜ በአፈር ውስጥ በማካካሻ ወይም በትክክል በዘር መስመር "
        "ላይ በመስረቅ ይተግብሩ፤ ጠቅላላ በአንድ ቦታ ከተከማቸ ቦሮን ዘሩን ያረግጣል። በፍጥነት ያቀላቅሉ፤ ፒኤች "
        "ከ6–7 መሆኑን ያረጋግጡ፤ በሚቀጥለው ወቅት ከመድገምዎ በፊት ሰልፈርንና ቦሮንን እንደገና ይፈትሹ።"
    ),
    "p_ample": (
        "ፎስፎረስ (Phosphorus, P){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ይበልጣል። አፈሩ በፎስፎረስ የበለጸገ ነው፦ የፎስፎረስ ማዳበሪያ (phosphorus fertilizer) ሙሉ "
        "በሙሉ ያቁሙ። ትርፍ ፎስፎረስ ወደ ውሃ ተፍሶ የውሃ ብክለት (eutrophication) ያስከትላል። የአፈር መዋቅርን "
        "ለማሻሻል ጂፕሰም (gypsum) ወይም ሎሚ (lime) ይጠቀሙ፣ ትርፍ ፎስፎረስን ለመቀነስ ለሁለት ወይም "
        "ሶስት ወቅት ፎስፎረስን የሚወስዱ ሰብሎች ወይም ሽፋን ሰብሎች (cover crops) ያልሙ።"
    ),
    "p_adequate": (
        "ፎስፎረስ (Phosphorus, P) በሚፈለገው ተስማሚ ክልል{crop} ({band} ኪግ/ሄክታር) ውስጥ ነው። "
        "የጥገና መጠን ብቻ ይተግብሩ፦ ለአብዛኛው የመስክ ሰብል በመዝራት ጊዜ 20–30 ኪግ P₂O₅/ሄክታር "
        "የመነሻ ማዳበሪያ ይስጡ፣ የተለመደውን የመርጨት ልምድ ያስወግዱ። በክልሉ ውስጥ ለመቆየት በየወቅቱ "
        "ይፈትሹ።"
    ),
    "k_deficient": (
        "ፖታስየም (Potassium, K){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ያነሰ ነው። ፖታስየም ሰልፌት (Potassium sulfate, K₂SO₄) በግምት {k_sulfate} ኪግ/ሄክታር፣ ወይም "
        "ሙሪየት ኦፍ ፖታሽ (Muriate of Potash) ከመዝራት በፊት ከሥር ዞኑ አጠገብ ይተግብሩ። በአማራጭ እንጨት "
        "አመድ (wood ash) ወይም የበሰበሱ የሰብል ቅሪቶችን ይጠቀሙ። ከመከር በኋላ እንደገና ይፈትሹ፣ ምክንያቱም "
        "ፖታስየም በአሸዋማ አፈር ውስጥ ይንቀሳቀሳልና የማያቋርጥ መሙላት ይፈልጋል።"
    ),
    "k_deficient_blended": (
        "ፖታስየም (Potassium, K){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ያነሰ ነው። የክልል ውሁዱን NPSKB (13.4% N – 26.8% P₂O₅ – 14.1% K₂O፣ ከሰልፈር፣ ዚንክን፣ ቦሮን "
        "ጋር) በግምት {blend} ኪግ/ሄክታር በቀጥታ ወደዘር መስመሩ ውስጥ በማስተካከል ይተግብሩ፤ ውሁዱ ለጠፉ "
        "የምዕራብና የደቡብ-ምዕራብ ሰማያዊ ቁጭባዎች የተዘጋጀ ነው። ከፍተኛ አልካሊ የሪፍት ሸለቆ አፈሮች ላይ "
        "ያለ አሲድ ማስተካካይ አይጠቀሙ። ከመከር በኋላ እንደገና ይፈትሹ፤ ፖታስየም ይንቀሳቀሳልና የማያቋርጥ "
        "መሙላት ይፈልጋል።"
    ),
    "k_ample": (
        "ፖታስየም (Potassium, K){crop} ከሚፈለገው ተስማሚ ክልል ({band} ኪግ/ሄክታር) በ{deficit:.1f} ኪግ/ሄክታር "
        "ይበልጣል። በዚህ ወቅት የፖታስየም ማዳበሪያን ይቀንሱ፣ ከመጠን በላይ ፖታስየም የካልሲየምና የማግኒዥየም "
        "አቅርቦትን ይከላከላልና የአፈር ጨዋማነት ያስከትላል። ያለ ፖታስየም ማዳበሪያዎችን (እንደ ዩሪያ (urea) እና "
        "ዲኤፒ (DAP)) ብቻ ይጠቀሙ፣ የተክል ምልክቶችን ይከታተሉ፣ ከቀጣዩ ወቅት በፊት አፈሩን ይፈትሹ።"
    ),
    "k_adequate": (
        "ፖታስየም (Potassium, K) በሚፈለገው ተስማሚ ክልል{crop} ({band} ኪግ/ሄክታር) ውስጥ ነው። የጥገና መጠን "
        "ይተግብሩ፦ ለአብዛኛው ሰብል በየወቅቱ 30–40 ኪግ K₂O/ሄክታር፣ ወይም በሰብል ቅሪትና በእርሻ ፍግ "
        "ይተኩ። በአሸዋማ አፈር ላይ ትርፍ ፖታስየምን ያስወግዱ፣ ምክንያቱም ከሥር ዞኑ በታች ይፈስሳልና።"
    ),
    "ph_deficient": (
        "የአፈር ፒኤች (pH){crop} ከሚፈለገው ተስማሚ ክልል ({band}) በ{deficit:.2f} ያነሰ ነው። የግብርና ሎሚ "
        "(agricultural lime, CaCO₃) በግምት {lime_t} ቶን/ሄክታር ከመዝራት ቢያንስ 3 ወር በፊት ከ15–20 ሳ.ሜ "
        "የላይኛው አፈር ጋር በማቀላቀል ይተግብሩ። ከፍተኛ መጠን በሁለት ክፍል ይከፋፍሉ፣ አሲዳማ አፈር "
        "ፎስፎረስን ይይዛልና አሉሚኒየም (aluminum) ለሥር መርዝ ይሆናልና፣ ከእያንዳንዱ ወቅት በኋላ ፒኤችን "
        "እንደገና ይለኩ።"
    ),
    "ph_ample": (
        "የአፈር ፒኤች (pH){crop} ከሚፈለገው ተስማሚ ክልል ({band}) በ{deficit:.2f} ይበልጣል። ሰልፈር "
        "(elemental sulfur) በግምት {s_t} ቶን/ሄክታር በሁለት ክፍል ከላይኛው አፈር ጋር በማቀላቀል ይተግብሩ፣ "
        "ወይም አሲዳማ ኦርጋኒክ ቁስ (እንደ ጥድ መርፌ፣ ፒት (peat)፣ የበሰበሰ ቅጠል) ይጠቀሙ። ከፍተኛ ፒኤች "
        "ብረትን፣ ዚንክንና ፎስፎረስን ይይዛልና የቅጠል ቢጫነት (chlorosis) ያስከትላል፤ ከእያንዳንዱ ትግበራ "
        "በፊት እንደገና ይለኩ።"
    ),
    "ph_adequate": (
        "የአፈር ፒኤች (pH) በሚፈለገው ተስማሚ ክልል{crop} ({band}) ውስጥ ነው። አብዛኛዎቹ ንጥረ-ነገሮች "
        "በዚህ ክልል በቀላሉ ይገኛሉ። ኮምፖስትን (compost) በየጊዜው ይጠቀሙ፣ ፒኤች ወደ አሲድነት "
        "ሲሸጋገር ብቻ ሎሚ (lime) ይተግብሩ፤ ገለልተኛ በሆነ አፈር ላይ ያለምክንያት ሎሚን ያስወግዱ።"
    ),
}

# Afaan Oromoo templates (Qubee). Terminology follows the Oromia extension
# glossary (Dataset/terminology_om.json): irra-naqinsa = top-dress,
# jal-naqinsa/xaa'oo hundee = basal, tarreedhaan naquu = band,
# biyyetti makuu = incorporate, booyoo facaasaa = seed furrow,
# En-Pii-Ees = NPS, Nooraa lafaa = agricultural lime.
_OM_TEMPLATES = {
    "n_deficient": (
        "Naayitroojinii biyyee{crop} banda gabbataa ({band} kg/ha) alatti "
        "{deficit:.1f} kg/ha hanqataa. Yuuriyaa (Urea, 46% N) yakka {urea} kg/ha "
        "eegalee biyyetti makuu fi sadarkaan lamaatti gubbaatti naquu (irra-naqinsa) "
        "kennaa akka hin hinqine, isaan olitti kompoostii (compost) / dikee "
        "(manure) yakka {compost:.0f} kg/ha fayyadaa. Shiifaa obbosuu duraan yaala "
        "biyyee keessatti makuu fi qorannoo biyyee sadarkaa 6 deebisanii "
        "qorachiisuu; xaa'oo ariifataan kaffaltii haqataa fi naayitroojinii bishaan "
        "hundaa keessatti geessaa."
    ),
    "n_ample": (
        "Naayitroojinii biyyee{crop} banda gabbataa ({band} kg/ha) olatti "
        "{deficit:.1f} kg/ha caalaa. Yeroo kun xaa'oo naayitroojinii dabalata "
        "eegali. Naayitroojinii ariifataan nitraatii gara bishaanii geessaa fi "
        "biqiltaa abbaa xumura hin qabu haatti baadhaa. Gubbaatti naquu hiramaa "
        "yookaan eegali, naayitroojinii ol-aanaa fudhata xumuraa hiramaa (catch "
        "crop) ees, obbosuu xumuraa dura deebisanii qorachiisuu."
    ),
    "n_adequate": (
        "Naayitroojinii biyyee banda gabbataa{crop} ({band} kg/ha) keessatti. "
        "Xaa'oo eegalaan qofa eegalee: yeroo latee baasuutti irra-naqinsa qulqulluu "
        "20-25 kg N/ha, isaan olitti kompoostii bu'aa qabu hiramaan, fi biyyee "
        "yeroo yerootti deebisanii qorachiisuu bandatti kaa'uu."
    ),
    "p_deficient": (
        "Fosfarasii biyyee{crop} banda gabbataa ({band} kg/ha) alatti "
        "{deficit:.1f} kg/ha hanqataa. Daappii (DAP, 18% N, 46% P2O5) yakka {dap} "
        "kg/ha booyoo facaasaatti (seed furrow) eegalee, isaan olitti kompoostii "
        "qabu haatti fosfaatii gosa caalaa eegali. Dhangaggaa'ummaa biyyee 6-7 "
        "tuffaa akka ta'e mirkanee'aa; biyyee dhangaggaawaa fosfaatii geessaa. "
        "Xaa'oo yeroo itti aanu eegaluuf dura qorannoo biyyee deebisanii "
        "qorachiisuu."
    ),
    "p_deficient_nps": (
        "Fosfarasii biyyee{crop} banda gabbataa ({band} kg/ha) alatti "
        "{deficit:.1f} kg/ha hanqataa. En-Pii-Ees (NPS, 19% N, 38% P2O5, 7% S) "
        "yakka {nps} kg/ha jal-naqinsa (basal) biqiltaa xumuraatti eegalee, facasaa "
        "fi biyyetti sadarkaan makuu; biyyee alatti akka hin eegalamuu mirkanee'aa, "
        "ammonium-N geessaa. En-Pii-Ees salfarii (Sulfur) EthioSIS biqiltaa lamaan "
        "kessatti agarsiisaa ni moledha. Yeroo qilleensa dhabanii yookaan biyyee "
        "hwaataa tuffuun eegali; dhangaggaa'ummaa 6-7 tuffaa, yeroo itti aanu "
        "deebisanii qorachiisuu."
    ),
    "p_deficient_npsb": (
        "Fosfarasii biyyee{crop} banda gabbataa ({band} kg/ha) alatti "
        "{deficit:.1f} kg/ha hanqataa fi qorannoon booronii (Boron) hanqinaa "
        "agarsiisa. En-Pii-Ees-Bii (NPSB, 18.5% N, 36.9% P2O5, 6.8% S, 0.4% B) "
        "yakka {npsb} kg/ha jal-naqinsa qofa, facasaa qulqulluu yookaan tarreedhaan "
        "naquu tarkaanee keessatti eegalee; gaarii yeroo baay'ee keessatti eegalan "
        "booronii biqiltaa xumuraa baadhaa (phytotoxicity). Sadarkaan makuu, "
        "dhangaggaa'ummaa 6-7 tuffaa, yeroo itti aanu salfarii fi booronii "
        "deebisanii qorachiisuu."
    ),
    "p_ample": (
        "Fosfarasii biyyee{crop} banda gabbataa ({band} kg/ha) olatti "
        "{deficit:.1f} kg/ha caalaa. Biyyee fosfaasii baay'ee qaba: xaa'oo "
        "fosfaasii uttin dhaabi. Fosfarasii ol-aanaa bishaanii gara bishaanii "
        "geessaa fi ishimaa (eutrophication) ta'a. Jijjiiramaan biyyee fooyyuu "
        "jiipsemii yookaan nooraa eegalee, xumuraa yookaan shifaan fosfaatii "
        "ol-aanaa fudhataa yeroo lama sadiitti hiramaa eegalee."
    ),
    "p_adequate": (
        "Fosfarasii biyyee banda gabbataa{crop} ({band} kg/ha) keessatti. Xaa'oo "
        "eegalaan qofa eegalee: biqiltaa madaallawa jal-naqinsa xumuraatti 20-30 kg "
        "P2O5/ha eegalee, facasaa yeroo yeroo dhaabi; bandatti kaa'uu yeroo yerootti "
        "qorachiisuu."
    ),
    "k_deficient": (
        "Potaasiyomii biqiltaa{crop} banda gabbataa ({band} kg/ha) alatti "
        "{deficit:.1f} kg/ha hanqataa. Potaasiyom Salfeetii (K2SO4, 50% K2O) yakka "
        "{k_sulfate} kg/ha, yookaan muriate of potash K2O qabuun, obbosuu dura "
        "hundee biyyee keessatti tarreedhaan eegalee. Daaraa (wood ash) yookaan "
        "kompoostii xumuraa qabu biyyetti makuu ariifataa. Haamaa booda deebisanii "
        "qorachiisuu; potaasiyomii biqiltaa awuuffaa kessatti ni geessaa fi yeroo "
        "yeroo guutuu barbaada."
    ),
    "k_deficient_blended": (
        "Potaasiyomii biqiltaa{crop} banda gabbataa ({band} kg/ha) alatti "
        "{deficit:.1f} kg/ha hanqataa. Unka zoona NPSKB (13.4% N, 26.8% P2O5, 14.1% "
        "K2O, salfarii, zinkiinii fi booronii qabu) yakka {blend} kg/ha booyoo "
        "obbosaa keessatti hundee biqiltaa qulqulluu keessatti eegalee; unka kun "
        "biqiltaa karaa seenaa fi karaa manaa seenaa haatti qabata. Biyyee "
        "ashaboomaa baay'ee Rift Valley kessatti alaana acidii jiru yookaan hin "
        "eegali. Haamaa booda deebisanii qorachiisuu; potaasiyomii ni geessaa fi "
        "yeroo yeroo guutuu barbaada."
    ),
    "k_ample": (
        "Potaasiyomii biqiltaa{crop} banda gabbataa ({band} kg/ha) olatti "
        "{deficit:.1f} kg/ha caalaa. Xaa'oo potaasiyomii yeroo kun hiramaa; "
        "potaasiyomii ol-aanaa kaalsiyoomii fi maaganeeshiyoomii fayyadamuu hinqabduu "
        "fi ishin biyyee agarsiisa. Xaa'oo potaasiyomii al qabu (Yuuriyaa fi Daappii) "
        "qofa eegalee, muraalee biqiltaa ilaali, obbosuu xumuraa dura deebisanii "
        "qorachiisuu."
    ),
    "k_adequate": (
        "Potaasiyomii biqiltaa banda gabbataa{crop} ({band} kg/ha) keessatti. "
        "Xaa'oo eegalaan eegalee: xumuraa yeroo yerootti 30-40 kg K2O/ha, isaan "
        "olitti qunnamtii xumuraa fi dikee faarsuu. Biqiltaa madaallawa awuuffaa "
        "olitti potaasiyomii ariifataan; hundee biqiltaa keessatti geessaa."
    ),
    "ph_deficient": (
        "Dhangaggaa'ummaa biyyee{crop} banda gabbataa ({band}) alatti {deficit:.2f} "
        "safartuu hanqataa. Nooraa lafaa (agricultural lime, CaCO3) yakka {lime_t} "
        "t/ha, obbosuu duraan 3 warrattan olaa biyyee 15-20 sm keessatti makuu. "
        "Wammaanii sadarkaan lamaatti hiramaa, biqiltaa bu'a qabu hinqabduu; "
        "dhangaggaa'ummaa yeroo yerootti deebisanii qorachiisuu — biyyee "
        "dhangaggaawaa fosfaatii geessaa fi alumiiniyamii hundee biqiltaa ta'a."
    ),
    "ph_ample": (
        "Dhangaggaa'ummaa biyyee{crop} banda gabbataa ({band}) olatti {deficit:.2f} "
        "caalaa. Salfarii (elemental sulfur) yakka {s_t} t/ha sadarkaan lamaatti "
        "biyyee olaatti makuu, isaan olitti uumamaa dhangaggaawaa (piniin, peat, "
        "kompoostii qiraa) fayyadaa. Dhangaggaa'ummaa dursa (high pH) badee, "
        "zinkiinii fi fosfaatii geessaa, muraalee biqiltaa ja'a (chlorosis) ta'a; "
        "tarkaanee duraatti deebisanii qorachiisuu."
    ),
    "ph_adequate": (
        "Dhangaggaa'ummaa biyyee banda gabbataa{crop} ({band}) keessatti, naatii "
        "nutfamaan sadarkaa harkaa fayyadamuu ni danda'a. Kompoostii yeroo yerootti "
        "eegalee, dhangaggaa'ummaa dhangaggaayyuu jijjiiramaan nooraa eegalee qofa; "
        "biyyee neutralii keessatti nooraa ariifataan dhaabi."
    ),
}


def _render_template(kind, status, item, lang, product=None):
    """Render the localized template for one element with the computed amounts.

    `product` selects a catalog variant template (p_deficient_nps,
    k_deficient_blended ...) when one exists; None or a legacy product keeps
    the classic templates byte-identical.
    """
    templates = (
        _OM_TEMPLATES if lang == "om"
        else _AM_TEMPLATES if lang == "am"
        else _EN_TEMPLATES
    )
    key = _template_key(templates, kind, status, product)
    amounts = {
        "deficit": item["deficit"],
        "band": _band_text(item),
        "crop": _for_crop(item, lang),
        "urea": _fertilizer_amount(item["deficit"], 0.46),
        "dap": _fertilizer_amount(item["deficit"], 0.46),
        "compost": round(max(item["deficit"] * 40, 0), 0),
        "k_sulfate": _fertilizer_amount(item["deficit"], 0.50),
        "lime_t": ethio.lime_rate_t(item["deficit"], item.get("soil_type"),
                                    item.get("lime_type")),
        "s_t": round(min(item["deficit"] / 0.5 * 1.5, 8.0), 1),
        # Catalog product amounts (only referenced by the catalog templates).
        "nps": _fertilizer_amount(
            item["deficit"], fertilizer_catalog.nutrient_content("P", "nps")),
        "npsb": _fertilizer_amount(
            item["deficit"], fertilizer_catalog.nutrient_content("P", "npsb")),
        "blend": _fertilizer_amount(
            item["deficit"], fertilizer_catalog.nutrient_content("K", "blended")),
    }
    return templates[key].format(**amounts)


def _zone_note(item, zone, lang="en", ph_value=None):
    """Zone-specific guidance appended to one recommendation (Phases 1 & 3).

    Reads the machine-readable overrides from the zone dataset:
      * lime_first_ph (acid highlands) — lime before any other amendment
        when the measured pH is at or below that threshold;
      * split_n (Vertisols) — split the N dose against denitrification;
      * p_efficiency_* (acid highlands) — explain the applied-P fixation
        adjustment already baked into the scaled deficit;
      * k_bypass_above (Vertisols) — explain why no K fertilizer is advised;
      * micronutrient_flag_ph / shift_blend_to (Rift Valley) — above the pH
        threshold Zn/B lock up, so the basal blend becomes NPSZnB.
    Returns "" for zones without a matching override.
    """
    overrides = ethio.zone_overrides(zone)
    if not overrides:
        return ""
    if item["col"] == "ph" and item["status"] == "deficient":
        threshold = overrides.get("lime_first_ph")
        if threshold is not None and item["value"] <= threshold:
            return translate(lang, "zone_lime_first", ph=f"{threshold:g}")
    if item["col"] == "N" and overrides.get("split_n") \
            and not item.get("suppressed"):
        if item["status"] in ("deficient", "adequate"):
            splits = int(overrides.get("n_splits", 2))
            return translate(lang, "zone_split_n", splits=splits)
    if item["col"] == "P" and item.get("p_efficiency"):
        threshold = overrides.get("p_efficiency_ph")
        factor = overrides.get("p_efficiency_factor")
        return translate(lang, "zone_p_fix", ph=f"{threshold:g}",
                         factor=f"{factor:g}")
    if item["col"] == "K" and item.get("k_bypass"):
        threshold = overrides.get("k_bypass_above")
        return translate(lang, "zone_k_bypass", threshold=f"{threshold:g}")
    if item["col"] == "P" and ph_value is not None:
        flag_ph = overrides.get("micronutrient_flag_ph")
        blend = overrides.get("shift_blend_to")
        if flag_ph is not None and blend and ph_value > flag_ph:
            return translate(lang, "zone_npsznb", ph=f"{flag_ph:g}",
                             blend=blend)
    return ""


def _lime_note(item, lang="en"):
    """Ethiopian LRF note appended to a deficient pH recommendation.

    Only fires when the analysis carried an explicit soil_type (Part 1c).
    States the LRF basis, the total program, and the season schedule from
    the 5.0 t/ha single-application ceiling.
    """
    soil_type = item.get("soil_type")
    if not soil_type or item["col"] != "ph" or item["status"] != "deficient":
        return ""
    plan = ethio.lime_plan(item["deficit"], soil_type, item.get("lime_type"))
    if plan is None:
        return ""
    soil = ethio.soil_type_display_name(soil_type, lang)
    parts = []
    for entry in plan["schedule"]:
        season = translate(lang, "lime_season", n=entry["season"])
        parts.append(f"{entry['t_ha']:g} t/ha ({season})")
    return translate(
        lang, "lime_lrf_note",
        soil=soil,
        lrf=f"{plan['lrf']:g}",
        ecce=f"{plan['ecce_pct']:g}",
        total=f"{plan['total_t_ha']:g}",
        max=f"{plan['max_single_t_ha']:g}",
        schedule=" + ".join(parts),
    )


def _season_note(item, lang="en"):
    """Season-specific guidance appended to the N/pH recommendation.

    P/K are season-independent and intentionally get no season note. Belg N
    may carry the crop-level scaling factor when a variety/zone band was
    adjusted instead of replaced by the seasonal table.
    """
    season = item.get("season")
    if not season:
        return ""
    if item["col"] == "N":
        key = "season_n_belg" if season == "belg" else "season_n_meher"
        note = translate(lang, key)
        if str(item.get("season_applied_n_source", "")).endswith(
                "belg_factor") and item.get("season_n_factor") is not None:
            note += translate(
                lang, "season_n_scaled",
                factor=f"{item['season_n_factor']:g}",
            )
        return note
    if item["col"] == "ph" and item["status"] == "deficient":
        return translate(lang, "season_lime_timing")
    return ""


def _conditioning_note(item, lang="en"):
    """Short provenance note for a band adjusted by Enhancement E."""
    meta = item.get("soil_conditioning")
    if not meta:
        return ""
    applied = [
        entry for entry in meta.get("applied") or []
        if entry.get("col") == item["col"]
    ]
    if not applied:
        return ""
    return translate(
        lang, "soil_conditioning_note",
        texture=meta.get("texture_class") or "n/a",
        soc=meta.get("soc_class") or "n/a",
    )


def _salinity_text(item, zone, lang="en"):
    """Full replacement guidance for nutrients suppressed by salinity.

    Used instead of the nutrient templates when the arid-zone ECe override
    paused fertilization: gypsum + leaching, plus a sodicity warning when
    the measured ESP is above the zone threshold.
    """
    overrides = ethio.zone_overrides(zone) or {}
    threshold = overrides.get("ec_e_pause_ds_m", 4.0)
    text = translate(lang, "zone_salinity_pause", ec_e=f"{item['ec_e']:g}",
                     threshold=f"{threshold:g}")
    esp = item.get("esp")
    esp_above = overrides.get("esp_sodic_above")
    if esp is not None and esp_above is not None and esp > esp_above:
        text += translate(lang, "zone_sodicity", esp=f"{esp:g}",
                          threshold=f"{esp_above:g}")
    return text


def recommend_soil(analysis, lang="en", selection=None, zone=None):
    """Deterministic recommendations for every element in the analysis,
    localized into the requested language (en, am or om).

    `selection` is the product map from fertilizer_catalog.resolve_selection;
    when omitted it is resolved from the analysis's crop (so every legacy
    caller — Streamlit, CLI tools, tests — keeps its exact behavior).
    `zone` adds agroecology-specific guidance (lime-first, split N, P-fix
    adjustment, K bypass, NPSZnB blend, salinity pause) to the matching
    recommendations; it never changes amounts by itself.
    `soil_type`/`lime_type` (from analyze_soil) switch the pH-deficient
    lime tonnage to the Ethiopian LRF formula and append the LRF schedule
    note; without them the legacy generic formula applies unchanged.
    """
    kinds = {"N": "n", "P": "p", "K": "k", "ph": "ph"}
    if selection is None:
        crop = analysis[0].get("crop") if analysis else None
        selection = fertilizer_catalog.resolve_selection(crop=crop)
    if not zone and analysis:
        zone = analysis[0].get("zone")
    ph_value = next(
        (i["value"] for i in analysis if i.get("col") == "ph"), None)
    season = next(
        (i.get("season") for i in analysis if i.get("season")), None)
    out = []
    for item in analysis:
        if item.get("suppressed"):
            text = _salinity_text(item, zone, lang)
        else:
            product = selection.get(item["col"])
            text = _render_template(kinds[item["col"]], item["status"], item,
                                    lang, product=product)
            note = _zone_note(item, zone, lang, ph_value=ph_value) if zone else ""
            if note:
                text = text.rstrip() + note
            season_note = _season_note(item, lang) if season else ""
            if season_note:
                text = text.rstrip() + season_note
            conditioning_note = _conditioning_note(item, lang)
            if conditioning_note:
                text = text.rstrip() + conditioning_note
            lime_note = _lime_note(item, lang)
            if lime_note:
                text = text.rstrip() + lime_note
        out.append(
            {
                "element": item["element"],
                "status": item["status"],
                "deficit": item["deficit"],
                "solution": text,
                "source": "rule-based",
                "lang": lang,
            }
        )
    return out


def _nutrient_contents_line(selection):
    """The conversion-factors sentence for the LLM prompt.

    Built from the selected products so an NPS-based analysis never instructs
    the model to divide by DAP's 46%. The legacy trio reproduces the original
    sentence exactly.
    """
    if not selection:
        return ("e.g. urea is 46% N, DAP is 46% P2O5, potassium sulfate is "
                "50% K2O")
    parts = ["urea is 46% N"]
    p_product = selection.get("P")
    if p_product == "nps":
        parts.append("NPS is 38% P2O5 (and 19% N, 7% S)")
    elif p_product == "npsb":
        parts.append("NPSB is 36.9% P2O5 (18.5% N, 6.8% S, 0.4% B)")
    else:
        parts.append("DAP is 46% P2O5")
    k_product = selection.get("K")
    if k_product == "blended":
        parts.append("the regional NPSKB blend is 14.1% K2O (and 26.8% P2O5)")
    else:
        parts.append("potassium sulfate is 50% K2O")
    return ", ".join(parts)


def _ethiopian_context_line(analysis, lang="en"):
    """Ethiopian zone/soil-type context + product guardrail, or "".

    Context is read from the analysis rows produced by ``analyze_soil`` so
    existing ``build_prompt`` callers keep their signature and behaviour.
    """
    if not analysis:
        return ""
    first = analysis[0]
    parts = []
    zone = first.get("zone")
    if zone and ethio.zone_exists(zone):
        parts.append(
            f"agroecological zone {ethio.zone_display_name(zone, lang)} ({zone})"
        )
    soil_type = first.get("soil_type")
    if soil_type:
        parts.append(
            f"soil type {ethio.soil_type_display_name(soil_type, lang)} "
            f"({soil_type})"
        )
    if not parts:
        return ""
    return (
        "\nEthiopian context: " + "; ".join(parts) + ". Use only the products "
        "and nutrient contents listed above; do not substitute products or "
        "application rates used outside Ethiopia."
    )


def build_prompt(analysis, lang="en", selection=None):
    crop = analysis[0].get("crop") if analysis else None
    crop_line = f"\nTarget crop: {crop}." if crop else ""
    language_line = (
        '\nWrite every "solution" in Amharic (አማርኛ). Keep the JSON keys and '
        "status values in English. Use formal extension-style register, bilingual "
        "scientific terms (Amharic + English), end sentences with ።, and keep the "
        "numbers and kg/ha units exactly as given."
        if lang == "am"
        else (
            '\nWrite every "solution" in Afaan Oromoo (Latin Qubee script). '
            "Keep the JSON keys and status values in English. Use formal "
            "extension-style register, bilingual agronomy terms (Afaan Oromoo "
            "+ English in parentheses, e.g. Yuuriyaa (Urea), En-Pii-Ees (NPS)), "
            "and keep the numbers and kg/ha units exactly as given."
            if lang == "om"
            else ""
        )
    )
    soil_data_lines = []
    for item in analysis:
        lo, hi = item["band"]
        if item["col"] == "ph":
            soil_data_lines.append(
                f'{item["element"]}: value {item["value"]:.2f}, status {item["status"]}, '
                f'ideal band {lo:.1f}-{hi:.1f}, difference from band {item["deficit"]:.2f} units'
            )
        else:
            soil_data_lines.append(
                f'{item["element"]}: value {item["value"]:.1f} kg/ha, status {item["status"]}, '
                f'ideal band {lo:.0f}-{hi:.0f} kg/ha, '
                f'difference from band {item["deficit"]:.1f} kg/ha'
            )
    contents = _nutrient_contents_line(selection)
    return f"""
You are an agronomist advising on soil improvement. Below is a soil analysis where each
element has a value, a status ("deficient" = below ideal, "ample" = above ideal,
"adequate" = within ideal), the ideal band for this crop, and the measured difference
from the band (kg/ha for nutrients, pH units for pH).{crop_line}

Your STATUS for each element MUST exactly match the status given in the soil data
(deficient / ample / adequate). Never contradict it: if the data says "ample",
your status must be "ample".

For each element give a dense, highly detailed solution (minimum 100 words) explaining what to add or
remove from the soil (fertilizer, compost, lime, sulfur...). Calculate the amount
from the measured difference using standard nutrient contents ({contents}) and state it in kg/ha (t/ha for
lime/sulfur). Do not invent measurements.{language_line}{_ethiopian_context_line(analysis, lang)}

Return ONLY valid JSON with exactly these four keys and no text outside it:
{{
  "Nitrogen": {{"status": "...", "solution": "..."}},
  "Phosphorus": {{"status": "...", "solution": "..."}},
  "Potassium": {{"status": "...", "solution": "..."}},
  "pH": {{"status": "...", "solution": "..."}}
}}

Soil Data:
{chr(10).join(soil_data_lines)}
"""


def _strip_json_fences(content):
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def parse_llm_json(content):
    """Parse LLM output, tolerating markdown code fences and stray whitespace."""
    if not isinstance(content, str):
        raise ValueError("LLM response is not text")
    text = _strip_json_fences(content)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start : end + 1])


_ELEMENT_KEY_RE = re.compile(r'"\s*([^"]+?)\s*"\s*:')


def _balanced_object(text, start):
    """Return the complete {...} from ``start``, or None if it is truncated."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_llm_json_partial(content, stats=None):
    """Parse LLM output, salvaging complete element records from truncation.

    Returns a normalized dict with the elements that parsed. Unlike
    ``parse_llm_json`` this never raises for malformed/truncated text; an
    entirely unusable response yields ``{}``. ``stats["rejected"]`` counts
    canonical keys whose records were unusable.
    """
    if not isinstance(content, str):
        return {}
    text = _strip_json_fences(content)
    try:
        parsed = parse_llm_json(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return normalize_agronomic_keys(parsed, stats=stats)

    recovered = {}
    for match in _ELEMENT_KEY_RE.finditer(text):
        canonical = _canonical_element_key(match.group(1))
        if canonical is None or canonical in recovered:
            continue
        object_start = text.find("{", match.end())
        if object_start == -1:
            continue
        raw = _balanced_object(text, object_start)
        if raw is None:
            continue
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            value = None
        record = coerce_element_record(value)
        if record is not None:
            recovered[canonical] = record
        elif isinstance(stats, dict):
            stats["rejected"] = stats.get("rejected", 0) + 1
    return recovered


def _numbers_in(text):
    return [float(m) for m in re.findall(r"\d+\.?\d*", text)]


def llm_fact_failures(analysis, llm_recs, selection=None):
    """N5 numeric-fact validation: every LLM solution must restate at least one
    of the computed facts (band edges, deficit, or derived fertilizer amount)
    within tolerance. Elements that fail fall back to deterministic text.

    `selection` adds the selected products' amounts (e.g. NPS at 38% P2O5)
    to the accepted facts; the classic 46%/50% amounts stay accepted so
    legacy prompt behavior is unchanged.
    """
    failures = []
    for item in analysis:
        rec = llm_recs.get(item["element"])
        if rec is None or not isinstance(rec, dict) or not isinstance(rec.get("solution"), str):
            failures.append(item["element"])
            continue
        nums = _numbers_in(rec["solution"])
        lo, hi = item["band"]
        facts = [lo, hi, item["deficit"]]
        if item["deficit"] > 0:
            facts.append(_fertilizer_amount(item["deficit"], 0.46))
            facts.append(_fertilizer_amount(item["deficit"], 0.50))
            if selection and item["col"] in ("N", "P", "K"):
                product = selection.get(item["col"])
                if product:
                    facts.append(_fertilizer_amount(
                        item["deficit"],
                        fertilizer_catalog.nutrient_content(item["col"], product),
                    ))
        matched = any(
            any(abs(n - fact) <= max(1.0, abs(fact) * 0.05) for n in nums) for fact in facts
        )
        if not matched:
            failures.append(item["element"])
    return failures


def merge_recommendations(llm_recs, deterministic_recs, analysis=None, lang="en",
                          selection=None):
    """Prefer the LLM solution per element, but only if its status is consistent
    with the computed analysis AND its numbers match the computed facts (N5);
    otherwise fall back to the deterministic (localized) one."""
    llm_recs = normalize_agronomic_keys(llm_recs)
    failures = set(llm_fact_failures(analysis, llm_recs, selection=selection)) if analysis else set()
    merged = []
    for det in deterministic_recs:
        llm = llm_recs.get(det["element"])
        if (
            llm is not None
            and isinstance(llm, dict)
            and det["element"] not in failures
        ):
            llm_status = normalize_status(llm.get("status"))
            llm_solution = llm.get("solution")
            if lang == "am" and isinstance(llm_solution, str):
                llm_solution = normalize_amharic(llm_solution)
            if lang == "om" and isinstance(llm_solution, str):
                llm_solution = normalize_qubee(llm_solution)
            if llm_status == det["status"] and isinstance(llm_solution, str) and llm_solution.strip():
                merged.append(
                    {
                        "element": det["element"],
                        "status": llm_status,
                        "deficit": det["deficit"],
                        "solution": llm_solution.strip(),
                        "source": "llm",
                        "lang": lang,
                    }
                )
                continue
        merged.append(det)
    return merged


# ---------------------------------------------------------------------------
# Phase 2 — micronutrient verdicts, product-selection trace, application guide
# ---------------------------------------------------------------------------

_MICRO_MESSAGES = {
    "en": {
        "s_def": ("Sulfur {value} ppm is below the {crit} ppm critical level "
                  "— sulfur-deficient."),
        "s_ok": ("Sulfur {value} ppm meets the {crit} ppm critical level "
                 "— adequate."),
        "b_def": ("Boron {value} ppm is below the {suff} ppm sufficiency level "
                  "— boron-deficient."),
        "b_ok": ("Boron {value} ppm is at or above the {suff} ppm sufficiency "
                 "level — adequate."),
        "b_exc": ("Boron {value} ppm exceeds the {toxic} ppm toxicity-risk "
                  "level — excess; avoid boron-bearing supplements."),
    },
    "am": {
        "s_def": ("የሰልፈር (Sulfur) {value} ፒ.ፒ.ኤም. ከ{crit} ፒ.ፒ.ኤም. በታች ነው — "
                  "የሰልፈር እጥረት አለ።"),
        "s_ok": ("የሰልፈር (Sulfur) {value} ፒ.ፒ.ኤም. ከ{crit} ፒ.ፒ.ኤም. ተስማሚ ነው።"),
        "b_def": ("ቦሮን (Boron) {value} ፒ.ፒ.ኤም. ከ{suff} ፒ.ፒ.ኤም. በታች ነው — "
                  "የቦሮን እጥረት አለ።"),
        "b_ok": ("ቦሮን (Boron) {value} ፒ.ፒ.ኤም. ከ{suff} ፒ.ፒ.ኤም. በላይ ነው — "
                 "ተስማሚ።"),
        "b_exc": ("ቦሮን (Boron) {value} ፒ.ፒ.ኤም. ከ{toxic} ፒ.ፒ.ኤም. በላይ ነው — "
                  "ትርፍ፤ ቦሮን የሚወስዱ ማዳበሪያ አይጠቀሙ።"),
    },
    "om": {
        "s_def": ("Salfarii (Sulfur) {value} ppm {crit} ppm gadii — "
                  "salfarii hanqataa."),
        "s_ok": ("Salfarii (Sulfur) {value} ppm {crit} ppm safartuu — gahaa."),
        "b_def": ("Booronii (Boron) {value} ppm {suff} ppm gadii — "
                  "booronii hanqataa."),
        "b_ok": ("Booronii (Boron) {value} ppm {suff} ppm naa'siin — gahaa."),
        "b_exc": ("Booronii (Boron) {value} ppm {toxic} ppm caalaa — "
                  "gar-malee hedduu; xaa'oo booronii qabu eegali."),
    },
}


def micronutrient_check(sulfur_ppm=None, boron_ppm=None, lang="en"):
    """S/B soil-test verdicts against the EthioSIS decision thresholds.

    Returned by /api/predict only when the caller supplies sulfur_ppm
    and/or boron_ppm — absent inputs give None, keeping the legacy response
    byte-identical. Statuses: deficient / adequate / excess (boron only).
    """
    if sulfur_ppm is None and boron_ppm is None:
        return None
    messages = _MICRO_MESSAGES.get(lang, _MICRO_MESSAGES["en"])
    out = {}
    if sulfur_ppm is not None:
        deficient = sulfur_ppm < fertilizer_catalog.SULFUR_CRITICAL_PPM
        out["sulfur"] = {
            "value": sulfur_ppm,
            "unit": "ppm",
            "threshold": fertilizer_catalog.SULFUR_CRITICAL_PPM,
            "status": "deficient" if deficient else "adequate",
            "message": (messages["s_def"] if deficient else messages["s_ok"]).format(
                value=sulfur_ppm,
                crit=f"{fertilizer_catalog.SULFUR_CRITICAL_PPM:g}",
            ),
        }
    if boron_ppm is not None:
        if boron_ppm < fertilizer_catalog.BORON_SUFFICIENT_PPM:
            status, key = "deficient", "b_def"
        elif boron_ppm > fertilizer_catalog.BORON_TOXIC_PPM:
            status, key = "excess", "b_exc"
        else:
            status, key = "adequate", "b_ok"
        out["boron"] = {
            "value": boron_ppm,
            "unit": "ppm",
            "threshold": fertilizer_catalog.BORON_SUFFICIENT_PPM,
            "toxic_above": fertilizer_catalog.BORON_TOXIC_PPM,
            "status": status,
            "message": messages[key].format(
                value=boron_ppm,
                suff=f"{fertilizer_catalog.BORON_SUFFICIENT_PPM:g}",
                toxic=f"{fertilizer_catalog.BORON_TOXIC_PPM:g}",
            ),
        }
    return out


_SEL_MESSAGES = {
    "en": {
        "off": ("FERT_PRODUCT_CATALOG is off: legacy products in use (DAP, "
                "potassium sulfate). Set FERT_PRODUCT_CATALOG=on to select "
                "the Ethiopian NPS/NPSB/region blends."),
        "p_default": ("No boron test given: NPS is the default basal "
                      "phosphorus source (DAP phased out for macro-use since "
                      "2014-15 to prevent sulfur starvation)."),
        "p_npsb": ("Boron {boron} ppm < {suff} ppm → boron deficient: NPSB "
                   "supplies boron with the basal phosphorus."),
        "p_nps_b_ok": ("Boron {boron} ppm ≥ {suff} ppm → boron sufficient: "
                       "NPS without boron."),
        "p_nps_b_exc": ("Boron {boron} ppm > {toxic} ppm → toxicity risk: NPS "
                        "without boron (avoid boron-bearing blends)."),
        "s_def": ("Sulfur {sulfur} ppm < {crit} ppm → sulfur deficient: the "
                  "selected NPS/NPSB blend supplies 7% sulfur."),
        "s_ok": ("Sulfur {sulfur} ppm ≥ {crit} ppm → adequate; NPS is still "
                 "used because DAP is phased out for macro-use."),
        "k_region": ("Region '{region}' matches the K-leached blend target "
                     "areas → region blend (Formula 3/5)."),
        "k_crop": ("Crop '{crop}' is a tuber/enset/perennial target → region "
                   "blend (Formula 3/5)."),
        "k_default": ("No blend target match (region/crop): potassium sulfate "
                      "for the potassium deficit."),
    },
    "am": {
        "off": ("FERT_PRODUCT_CATALOG ጠፍቋል፦ የቀደሙ ምርቶች ብቻ ተጠቀሙ (ዲኤፒ፣ "
                "ፖታስየም ሰልፌት)። NPS/NPSB እና የክልል ውሁዶችን ለመምረጥ "
                "FERT_PRODUCT_CATALOG=on ያስኩ።"),
        "p_default": ("የቦሮን የታመመ አልተፈለገም: ኤንፒኤስ (NPS) የመሬት ፎስፎረስ "
                      "የመሬት ማዳበሪያ ነው (ዲኤፒ ከ2014-15 ጀምሮ ለሰልፈር እጥረት "
                      "ማስቆም ምክንያት ተወው።"),
        "p_npsb": ("ቦሮን {boron} ፒ.ፒ.ኤም. < {suff} ፒ.ፒ.ኤም. → የቦሮን እጥረት "
                   "አለ፦ ኤንፒኤስቢ (NPSB) ቦሮን ይሰጣል።"),
        "p_nps_b_ok": ("ቦሮን {boron} ፒ.ፒ.ኤም. ≥ {suff} ፒ.ፒ.ኤም. → ቦሮን "
                       "ተስማሚ ነው፦ ኤንፒኤስ (NPS) ውሃጥ ያለ ቦሮን።"),
        "p_nps_b_exc": ("ቦሮን {boron} ፒ.ፒ.ኤም. > {toxic} ፒ.ፒ.ኤም. → የመርዝ "
                        "ስጋት አለ፦ ኤንፒኤስ (NPS) ያለ ቦሮን።"),
        "s_def": ("ሰልፈር {sulfur} ፒ.ፒ.ኤም. < {crit} ፒ.ፒ.ኤም. → የሰልፈር እጥረት "
                  "አለ፤ የተመረጠው ኤንፒኤስ/ኤንፒኤስቢ 7% ሰልፈር ይሰጣል።"),
        "s_ok": ("ሰልፈር {sulfur} ፒ.ፒ.ኤም. ≥ {crit} ፒ.ፒ.ኤም. → ተስማሚ፤ እንዲያውም "
                 "ኤንፒኤስ ይጠቀማል፣ ዲኤፒ ከተወገደ ስለሆነ።"),
        "k_region": ("'{region}' ክልል ፖታስየም የጠፉ ውሁድ ቁጭባዎች መጠን ነው → "
                     "የክልል ውሁድ (Formula 3/5)።"),
        "k_crop": ("'{crop}' በቆሎ/እንሰት/ዘመናዊ እህል መጠን ነው → የክልል ውሁድ "
                   "(Formula 3/5)።"),
        "k_default": ("ውሁድ አይዛመድም (ክልል/እህል)፦ ፖታስየም እጥረት ለማስተካከል "
                       "ፖታስየም ሰልፌት።"),
    },
    "om": {
        "off": ("FERT_PRODUCT_CATALOG dhaabamee: xaa'oo duraan qofa fayyadama "
                "(Daappii, Potaasiyom Salfeetii). FERT_PRODUCT_CATALOG=on "
                "eegalee xaa'oo hunda EthioSIS En-Pii-Ees/En-Pii-Ees-Bii/unka "
                "zoona filadhu."),
        "p_default": ("Qorannoo booronii hin gahe: En-Pii-Ees (NPS) jal-naqinsa "
                      "fosfaatii duraa dha (Daappii yeroo 2014-15 irra eegaluun "
                      "salfarii hanqinaa ta'uun dhaabamee)."),
        "p_npsb": ("Booronii {boron} ppm < {suff} ppm → booronii hanqataa: "
                   "En-Pii-Ees-Bii (NPSB) booronii jal-naqinsatti baana."),
        "p_nps_b_ok": ("Booronii {boron} ppm ≥ {suff} ppm → booronii gahaa: "
                       "En-Pii-Ees (NPS) booronii al qabu."),
        "p_nps_b_exc": ("Booronii {boron} ppm > {toxic} ppm → garii laafinaa: "
                        "En-Pii-Ees (NPS) booronii al qabu."),
        "s_def": ("Salfarii {sulfur} ppm < {crit} ppm → salfarii hanqataa: "
                  "unka En-Pii-Ees/En-Pii-Ees-Bii filatame 7% salfarii baana."),
        "s_ok": ("Salfarii {sulfur} ppm ≥ {crit} ppm → gahaa; En-Pii-Ees "
                 "ittiinis fayyadama, Daappii dhaabamee waan jiruun."),
        "k_region": ("Manaa '{region}' manaa unka potaasiyomii → unka zoona "
                     "(Formula 3/5)."),
        "k_crop": ("Xumuraa '{crop}' shiroo/enset/zamanee → unka zoona "
                   "(Formula 3/5)."),
        "k_default": ("Unka hin wabaramu (zoona/xumuraa): Potaasiyom Salfeetii "
                      "hanqinaa potaasiyomiif."),
    },
}


def product_selection_block(selection, crop=None, sulfur_ppm=None,
                             boron_ppm=None, region=None, lang="en"):
    """Traceable product-choice explanation for the API response.

    Additive: /api/predict includes it only when the caller sends S/B/region
    or catalog mode is on. Reasons are localized (en/am).
    """
    messages = _SEL_MESSAGES.get(lang, _SEL_MESSAGES["en"])
    mode = fertilizer_catalog.catalog_mode_enabled()
    reasons = []
    if not mode:
        reasons.append(messages["off"])
    else:
        if selection.get("P") == "npsb":
            reasons.append(messages["p_npsb"].format(
                boron=boron_ppm,
                suff=f"{fertilizer_catalog.BORON_SUFFICIENT_PPM:g}"))
        elif boron_ppm is not None and boron_ppm > fertilizer_catalog.BORON_TOXIC_PPM:
            reasons.append(messages["p_nps_b_exc"].format(
                boron=boron_ppm,
                toxic=f"{fertilizer_catalog.BORON_TOXIC_PPM:g}"))
        elif boron_ppm is not None:
            reasons.append(messages["p_nps_b_ok"].format(
                boron=boron_ppm,
                suff=f"{fertilizer_catalog.BORON_SUFFICIENT_PPM:g}"))
        else:
            reasons.append(messages["p_default"])
        if sulfur_ppm is not None:
            key = ("s_def" if sulfur_ppm < fertilizer_catalog.SULFUR_CRITICAL_PPM
                   else "s_ok")
            reasons.append(messages[key].format(
                sulfur=sulfur_ppm,
                crit=f"{fertilizer_catalog.SULFUR_CRITICAL_PPM:g}"))
        if selection.get("K") == "blended":
            if fertilizer_catalog.region_targets_k(region):
                reasons.append(messages["k_region"].format(region=region))
            else:
                reasons.append(messages["k_crop"].format(crop=crop))
        else:
            reasons.append(messages["k_default"])
    products = {}
    for col, product_id in selection.items():
        product = fertilizer_catalog.get_product(product_id)
        entry = {"id": product_id, "label": _label_for(product_id)}
        if product is not None:
            entry["label_am"] = product.get("name_am")
            entry["label_om"] = product.get("name_om")
        products[col] = entry
    return {
        "catalog_mode": mode,
        "products": products,
        "reasons": reasons,
        "crop": crop,
        "region": region,
        "lang": lang,
    }


# Growth-stage application schedules per product (structured form of the
# matrix's execution rules; bilingual). Keep product ids in sync with
# Dataset/fertilizer_products.json plus the three db-only products.
_APPLICATION_SCHEDULES = {
    "urea": {
        "timing": "split",
        "en": {
            "splits": [
                {"stage": "at planting", "share": "1/3"},
                {"stage": ("top-dress at knee-high maize / 30-40 days after "
                           "sowing for teff"),
                 "share": "2/3"},
            ],
            "restrictions": [
                ("Never leave on an exposed dry soil surface — up to 40% of "
                 "the N can volatilize within 48 hours"),
                "Do not apply during heavy downpours (nitrate leaching)",
            ],
        },
        "am": {
            "splits": [
                {"stage": "በመዝራት ጊዜ", "share": "1/3"},
                {"stage": ("በማይዝ-ሀይ የበቆሎ ደረጃ / ለጤፍ ከመዝራት 30–40 ቀናት "
                           "በኋላ የላይኛው መርጨት"),
                 "share": "2/3"},
            ],
            "restrictions": [
                ("በአፈር ላይ እንዳይተከል ያድርጉ — እስከ 40% የናይትሮጅን 48 ሰዓት ውስጥ "
                 "ይተኛል"),
                ("ከባድ ዝናብ ጊዜ አይተገብሩ — ናይትሬት (nitrate) እንዳይፈስ "
                 "ለመከላከል"),
            ],
        },
        "om": {
            "splits": [
                {"stage": "jalqaba xumuraatti", "share": "1/3"},
                {"stage": ("gubbaatti naquu (irra-naqinsa) boqqolloon dhibbaa "
                           "ta'e / xaafiif jalqaba obbosuu 30-40 guyyaa booda"),
                 "share": "2/3"},
            ],
            "restrictions": [
                ("Biyyee qilleensa alaatti akka hin eegalamuu mirkanee'aa — "
                 "naayitroojinii 40% ga'e 48 saatii keessatti ni geessaa"),
                ("Yeroo zuubaa guddoo keessatti eegali (naayitroojinii geessuu "
                 "hin kalaaluu)"),
            ],
        },
    },
    "nps": {
        "timing": "basal",
        "en": {
            "splits": [{"stage": "at planting (basal, all of it)", "share": "all"}],
            "restrictions": [
                ("Broadcast and incorporate immediately — ammonium-N "
                 "volatilizes if left on the surface"),
                "No application under dry-season moisture stress",
            ],
        },
        "am": {
            "splits": [{"stage": "በመዝራት ጊዜ (መሬት ማዳበሪያ፣ ሙሉው)", "share": "all"}],
            "restrictions": [
                ("በአፈር ላይ በማተካት ይተከል — በአፈር ላይ ተተከለ አመንኩት ናይትሮጅን "
                 "ይተኛል"),
                "በድርቅ ወቅት ወይም አፈሩ እርጥብነት ሲጠፋ አይተገብሩ",
            ],
        },
        "om": {
            "splits": [
                {"stage": "jalqaba xumuraatti (xaa'oo hundee, hunda)",
                 "share": "all"},
            ],
            "restrictions": [
                ("Facasaa fi biyyetti sadarkaan makuu — biyyee alatti eegalan "
                 "ammonium-N ni geessaa"),
                "Yeroo qilleensa dhabanii yookaan biyyee hwaataa tuffuun eegali",
            ],
        },
    },
    "npsb": {
        "timing": "basal",
        "en": {
            "splits": [{"stage": "at planting (basal, all of it)", "share": "all"}],
            "restrictions": [
                ("Uniform broadcast or precise row placement — concentrated "
                 "boron burns seedlings (phytotoxicity)"),
                ("Strictly basal; incorporate immediately and re-test S/B "
                 "next season"),
            ],
        },
        "am": {
            "splits": [{"stage": "በመዝራት ጊዜ (መሬት ማዳበሪያ፣ ሙሉው)", "share": "all"}],
            "restrictions": [
                ("በሰፋሚ ማተካት ወይም በትክክል በዘር መስመር ላይ ይተከል — ጠቅላላ "
                 "በአንድ ቦታ ከተከማቸ ቦሮን ዘር ለዘር መርዝ ይሆናል።"),
                "በትክክል መሬት ማዳበሪያ ብቻ፤ በፍጥነት ያቀላቅሉ፤ በሚቀጥለው ወቅት ሰልፈር/ቦሮን ይፈትሹ",
            ],
        },
        "om": {
            "splits": [
                {"stage": "jalqaba xumuraatti (xaa'oo hundee, hunda)",
                 "share": "all"},
            ],
            "restrictions": [
                ("Facasaa qulqulluu yookaan tarreedhaan naquu tarkaanee — "
                 "booronii yeroo baay'ee keessatti biqiltaa xumuraa baadhaa"),
                ("Jal-naqinsa qofa; sadarkaan makuu, yeroo itti aanu "
                 "salfarii/booronii deebisanii qorachiisuu"),
            ],
        },
    },
    "dap": {
        "timing": "basal",
        "en": {
            "splits": [{"stage": "in the seed furrow at planting", "share": "all"}],
            "restrictions": [
                ("Exception use only — phased out for macro-use since "
                 "2014-15 to prevent sulfur starvation"),
                "Supplies no sulfur; do not use where NPS/NPSB is available",
            ],
        },
        "am": {
            "splits": [{"stage": "በመዝራት ጊዜ በዘር መስመር ውስጥ", "share": "all"}],
            "restrictions": [
                ("በየው መጠቀም ብቻ — ከ2014-15 ጀምሮ ለሰልፈር እጥረት ማስቆም ምክንያት "
                 "ተወው"),
                "ሰልፈር አይሰጥም፤ ኤንፒኤስ/ኤንፒኤስቢ ቢኖር አይጠቀሙ",
            ],
        },
        "om": {
            "splits": [
                {"stage": "jalqaba xumuraatti booyoo facaasaatti", "share": "all"},
            ],
            "restrictions": [
                ("Yeroo qabxii qofa — yeroo 2014-15 irra salfarii hanqinaa "
                 "ta'uun dhaabamee"),
                ("Salfarii hin baanu; En-Pii-Ees/En-Pii-Ees-Bii jiru yookaan "
                 "hin eegali"),
            ],
        },
    },
    "potassium_sulfate": {
        "timing": "basal",
        "en": {
            "splits": [{"stage": "banded near the root zone before sowing",
                        "share": "all"}],
            "restrictions": [
                ("Avoid contact with the seed row (salt injury); irrigate or "
                 "incorporate after application"),
                "Re-test after harvest — K leaches from sandy soils",
            ],
        },
        "am": {
            "splits": [{"stage": "ከመዝራት በፊት ከሥር ዞኑ አጠገብ", "share": "all"}],
            "restrictions": [
                ("ከዘር መስመር ጋር አይነካና — ጨዋታ ያስከትላል፤ ከመዝራት በኋላ "
                 "ያቀላቅሉ"),
                "ከመከር በኋላ ይፈትሹ — በአሸዋማ አፈር ፖታስየም ይፈስሳል",
            ],
        },
        "om": {
            "splits": [
                {"stage": ("obbosuu dura hundee biyyee keessatti tarreedhaan"),
                 "share": "all"},
            ],
            "restrictions": [
                ("Booyoo obbosaa keessatti akka hin yaalamin — biqiltaa "
                 "baadhaa; eegaluuf bishaanii yookaan biyyetti makuu"),
                ("Haamaa booda deebisanii qorachiisuu — biqiltaa awuuffaa "
                 "potaasiyomii ni geessaa"),
            ],
        },
    },
    "blended": {
        "timing": "basal",
        "en": {
            "splits": [{"stage": "placed directly into the planting furrow",
                        "share": "all"}],
            "restrictions": [
                ("Avoid highly alkaline Rift Valley soils unless specialized "
                 "acidic modifiers are present"),
                "Place in the furrow for deep roots — do not broadcast",
            ],
        },
        "am": {
            "splits": [{"stage": "በቀጥታ ወደዘር መስመር ውስጥ", "share": "all"}],
            "restrictions": [
                ("ከፍተኛ አልካሊ የሪፍት ሸለቆ አፈሮች ላይ ያለ አሲድ ማስተካካይ "
                 "አይጠቀሙ"),
                "ለሥር ጥልፈት በመስመሩ ውስጥ ይስጡ — አይበተኑ",
            ],
        },
        "om": {
            "splits": [
                {"stage": "galmeessaan booyoo obbosaa keessatti", "share": "all"},
            ],
            "restrictions": [
                ("Biyyee ashaboomaa baay'ee Rift Valley kessatti alaana "
                 "acidii jiru yookaan hin eegali"),
                "Hundee biqiltaa qulqulluu keessatti eegalee — facasuu hin danda'u",
            ],
        },
    },
    "lime": {
        "timing": "pre-planting",
        "en": {
            "splits": [{"stage": ("incorporated into the top 15-20 cm at least "
                                  "3 months before sowing"),
                        "share": "all"}],
            "restrictions": [
                "Split heavy doses into two applications",
                "Re-test pH after each season before repeating",
            ],
        },
        "am": {
            "splits": [{"stage": ("ከመዝራት ቢያንስ 3 ወር በፊት ከ15–20 ሳ.ሜ የላይኛውን "
                                  "አፈር ጋር በማቀላቀል"),
                        "share": "all"}],
            "restrictions": [
                "ከፍተኛ መጠን በሁለት ክፍል ይከፋፍሉ",
                "ከእያንዳንዱ ወቅት በኋላ ፒኤችን እንደገና ይፈትሹ",
            ],
        },
        "om": {
            "splits": [
                {"stage": ("obbosuu duraan 3 warrattan olaa biyyee 15-20 sm "
                           "keessatti makuu"),
                 "share": "all"},
            ],
            "restrictions": [
                "Wammaanii sadarkaan lamaatti hiramaa",
                "Yeroo yerootti dhangaggaa'ummaa deebisanii qorachiisuu",
            ],
        },
    },
    "sulfur": {
        "timing": "split",
        "en": {
            "splits": [{"stage": "incorporated into the topsoil in split doses",
                        "share": "all"}],
            "restrictions": [
                ("Do not re-apply without a re-test — elemental sulfur "
                 "acidifies slowly"),
                "Keep granules away from the seed row",
            ],
        },
        "am": {
            "splits": [{"stage": "በሁለት ክፍል ከላይኛው አፈር ጋር በማቀላቀል",
                        "share": "all"}],
            "restrictions": [
                ("ከመድገም በፊት እንደገና ይፈትሹ — ኤለመንታል ሰልፈር በዝግነት "
                 "አሲድነት ያስከትላል"),
                "ከዘር መስመር ይርቁ",
            ],
        },
        "om": {
            "splits": [
                {"stage": "sadarkaan lamaatti biyyee olaatti makuu",
                 "share": "all"},
            ],
            "restrictions": [
                ("Qorannoo deebii al eegali — salfarii sadarkaan bu'a qabaati"),
                "Booyoo obbosaa irra fadhuu",
            ],
        },
    },
}


def product_for_rec(col, status, selection=None):
    """Product a recommendation applies to (None → maintenance, no purchase).

    pH maps to lime/sulfur by status like the cost estimator; nutrients map
    through the selection only when actually deficient — adequate/ample
    elements get maintenance guidance, not a product.
    """
    if col == "ph":
        return {"deficient": "lime", "ample": "sulfur"}.get(status)
    if status != "deficient":
        return None
    if not selection:
        return None
    return selection.get(col)


def application_schedule(product, crop=None, lang="en"):
    """Growth-stage application schedule for one product (Phase 2 additive).

    Returns {product, label, timing, splits[{stage, share}], restrictions[],
    crop, lang}, a maintenance entry for None, or None for unknown products.
    """
    if product is None:
        notes = {
            "en": ("Maintenance only — follow the rate in the solution text "
                   "and re-test each season."),
            "am": ("የጥገና መጠን ብቻ — በመፍትሔው ውስጥ ያለውን መጠን ተከትለው በየወቅቱ "
                   "አፈሩን ይፈትሹ።"),
            "om": ("Xaa'oo eegalaan qofa — caasaa keessatti laafuu mura'aa "
                   "ilaali fi biyyee yeroo yerootti deebisanii qorachiisuu."),
        }
        return {
            "product": None,
            "label": None,
            "timing": "maintenance",
            "splits": [],
            "restrictions": [],
            "note": notes.get(lang, notes["en"]),
            "crop": crop,
            "lang": lang,
        }
    schedule = _APPLICATION_SCHEDULES.get(product)
    if schedule is None:
        return None
    localized = schedule.get(lang, schedule["en"])
    return {
        "product": product,
        "label": _label_for(product),
        "timing": schedule["timing"],
        "splits": [dict(split) for split in localized["splits"]],
        "restrictions": list(localized["restrictions"]),
        "crop": crop,
        "lang": lang,
    }
