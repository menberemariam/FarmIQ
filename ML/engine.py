"""Deterministic soil analysis + recommendation engine.

Every amount returned here is *calculated* from the measured nutrient deficit
(kg/ha) using standard fertilizer nutrient contents, never guessed. When a crop
is provided, its real per-crop bands (ML/crop_bands.json, derived from the
dataset) are used instead of the generic agronomic ranges.
"""

import json
from functools import lru_cache
from pathlib import Path

import config

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
}


def normalize_status(status):
    """Map any LLM status phrasing to deficient/ample/adequate (or None)."""
    if not isinstance(status, str):
        return None
    return STATUS_CANONICAL.get(status.strip().lower())


def _load_crop_bands():
    path = Path(config.CROP_BANDS_JSON)
    if not path.exists():
        return {}
    with open(path) as fh:
        return json.load(fh)


@lru_cache(maxsize=8)
def _crop_bands_cache():
    return _load_crop_bands()


def get_bands(crop=None):
    """Ideal bands for a crop (from real dataset percentiles) or the generic
    agronomic ranges from config when no crop is given."""
    if crop and crop != "generic":
        bands = _crop_bands_cache().get(crop.lower())
        if bands:
            return bands
    return config.IDEAL_RANGES


def available_crops():
    """Sorted list of crops with real per-crop bands."""
    return sorted(_crop_bands_cache().keys())


def analyze_soil(N, P, K, ph, crop=None):
    """Compare each nutrient against the ideal bands for the given crop.

    Returns a list of dicts:
        {element, col, value, band, status (deficient/ample/adequate), deficit, crop}
    where deficit is the kg/ha shortfall (or excess) vs the band edge.
    """
    bands = get_bands(crop)
    values = {"N": N, "P": P, "K": K, "ph": ph}
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
        out.append(
            {
                "element": element,
                "col": col,
                "value": v,
                "band": (lo, hi),
                "status": status,
                "deficit": deficit,
                "crop": crop,
            }
        )
    return out


def _fertilizer_amount(deficit_kg, nutrient_content):
    """kg/ha of fertilizer needed to cover deficit at given nutrient content (0-1)."""
    amount = deficit_kg / nutrient_content
    return round(min(max(amount, 0), 500), 1)


def _for_crop(item):
    return f" for {item['crop']}" if item.get("crop") else ""


def _band_text(item):
    lo, hi = item["band"]
    return f"{lo:.0f}-{hi:.0f}" if item["col"] != "ph" else f"{lo:.1f}-{hi:.1f}"


def _recommend_n(item):
    status, deficit = item["status"], item["deficit"]
    band = _band_text(item)
    crop = _for_crop(item)
    if status == "deficient":
        urea = _fertilizer_amount(deficit, 0.46)
        compost = round(max(deficit * 40, 0), 0)
        return (
            f"Nitrogen is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
            f"Apply urea (46% N) at approximately {urea} kg/ha, worked into the soil and "
            f"split into two top-dressings to avoid losses, or use well-rotted cow dung / "
            f"compost at roughly {compost:.0f} kg/ha. Incorporate the material before sowing "
            f"and re-test the soil after 6 weeks; over-application wastes money and causes "
            f"leaching into groundwater."
        )
    if status == "ample":
        return (
            f"Nitrogen is {deficit:.1f} kg/ha above the ideal band{crop} ({band} kg/ha). Do not "
            f"add more nitrogen fertilizer this season. Excess N leaches nitrate into water "
            f"bodies and encourages lush, disease-prone growth. Reduce or skip top-dressing, "
            f"grow a catch crop such as mustard or ryegrass to scavenge surplus N, and test "
            f"again before the next planting."
        )
    return (
        f"Nitrogen is within the ideal band{crop} ({band} kg/ha). Apply only maintenance doses: "
        f"a light top-dressing of 20-25 kg N/ha at the tillering/growth stage, or an "
        f"equivalent sidedress of well-decomposed compost, and re-test the soil each season "
        f"to stay inside the band."
    )


def _recommend_p(item):
    status, deficit = item["status"], item["deficit"]
    band = _band_text(item)
    crop = _for_crop(item)
    if status == "deficient":
        dap = _fertilizer_amount(deficit, 0.46)
        return (
            f"Phosphorus is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
            f"Apply DAP (18% N, 46% P2O5) at approximately {dap} kg/ha placed in the seed "
            f"furrow, or use bone meal / rock phosphate with compost for slower release. Keep "
            f"soil pH near 6-7, since P fixation locks up phosphate in acidic soils. Re-test "
            f"the soil next season before repeating the application."
        )
    if status == "ample":
        return (
            f"Phosphorus is {deficit:.1f} kg/ha above the ideal band{crop} ({band} kg/ha). "
            f"Soils are P-rich: stop phosphorus fertilization entirely. Excess P runs off into "
            f"water bodies and causes eutrophication. Improve structure with gypsum or lime, "
            f"and grow P-absorbing crops or cover crops to draw down surplus P over two to "
            f"three seasons."
        )
    return (
        f"Phosphorus is within the ideal band{crop} ({band} kg/ha). Apply maintenance doses "
        f"only: a modest starter dressing of 20-30 kg P2O5/ha at sowing for most field crops, "
        f"and avoid routine broadcast applications. Re-test every season to keep the level in "
        f"the band."
    )


def _recommend_k(item):
    status, deficit = item["status"], item["deficit"]
    band = _band_text(item)
    crop = _for_crop(item)
    if status == "deficient":
        k_sulfate = _fertilizer_amount(deficit, 0.50)
        return (
            f"Potassium is {deficit:.1f} kg/ha below the ideal band{crop} ({band} kg/ha). "
            f"Apply potassium sulfate (50% K2O) at approximately {k_sulfate} kg/ha, or muriate "
            f"of potash adjusted for its K2O content, banded near the root zone before sowing. "
            f"Alternatively incorporate wood ash or composted crop residues for organic K. "
            f"Re-test after harvest, since K is mobile in many sandy soils and needs regular "
            f"replenishment."
        )
    if status == "ample":
        return (
            f"Potassium is {deficit:.1f} kg/ha above the ideal band{crop} ({band} kg/ha). "
            f"Reduce potassium applications this season; excess K can interfere with calcium "
            f"and magnesium uptake and raises soil salinity risk. Use only K-free fertilizers "
            f"(e.g., urea and DAP), monitor leaf symptoms, and re-test the soil before the "
            f"next crop cycle."
        )
    return (
        f"Potassium is within the ideal band{crop} ({band} kg/ha). Apply maintenance doses: "
        f"30-40 kg K2O/ha per season for most crops, or replace it with crop residues and "
        f"farmyard manure. Avoid surplus K in rainfed sandy soils where it leaches below the "
        f"root zone."
    )


def _recommend_ph(item):
    status, deficit = item["status"], item["deficit"]
    band = _band_text(item)
    crop = _for_crop(item)
    if status == "deficient":
        lime_t = round(min(deficit / 0.5 * 2.0, 10.0), 1)
        return (
            f"Soil pH is {deficit:.2f} units below the ideal band{crop} ({band}). Raise it by "
            f"applying finely ground agricultural lime (CaCO3) at roughly {lime_t} t/ha, "
            f"worked into the top 15-20 cm at least 3 months before sowing. Split heavy doses "
            f"into two applications to avoid shocking the soil biology, and re-test pH after "
            f"each season; acidic soil locks up phosphorus and makes aluminum toxic to roots."
        )
    if status == "ample":
        s_t = round(min(deficit / 0.5 * 1.5, 8.0), 1)
        return (
            f"Soil pH is {deficit:.2f} units above the ideal band{crop} ({band}). Lower it by "
            f"applying elemental sulfur at approximately {s_t} t/ha, incorporated into the "
            f"topsoil in split doses, or by working in acidifying organic matter such as pine "
            f"needles, peat or composted leaf litter. High pH ties up iron, zinc and "
            f"phosphorus, causing chlorosis; re-test before each new application."
        )
    return (
        f"Soil pH is within the ideal band{crop} ({band}), where most nutrients are readily "
        f"available. Maintain it with regular applications of compost and lime only if a "
        f"re-test shows drift toward acidity; avoid blanket liming of already neutral soil."
    )


def recommend_soil(analysis):
    """Deterministic recommendations for every element in the analysis."""
    recommenders = {
        "N": _recommend_n,
        "P": _recommend_p,
        "K": _recommend_k,
        "ph": _recommend_ph,
    }
    out = []
    for item in analysis:
        text = recommenders[item["col"]](item)
        out.append(
            {
                "element": item["element"],
                "status": item["status"],
                "deficit": item["deficit"],
                "solution": text,
                "source": "rule-based",
            }
        )
    return out


def build_prompt(analysis):
    crop = analysis[0].get("crop") if analysis else None
    crop_line = f"\nTarget crop: {crop}." if crop else ""
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
    return f"""
You are an agronomist advising on soil improvement. Below is a soil analysis where each
element has a value, a status ("deficient" = below ideal, "ample" = above ideal,
"adequate" = within ideal), the ideal band for this crop, and the measured difference
from the band (kg/ha for nutrients, pH units for pH).{crop_line}

Your STATUS for each element MUST exactly match the status given in the soil data
(deficient / ample / adequate). Never contradict it: if the data says "ample",
your status must be "ample".

For each element give a solution (minimum 50 words) explaining what to add or
remove from the soil (fertilizer, compost, lime, sulfur...). Calculate the amount
from the measured difference using standard nutrient contents (e.g. urea is 46% N,
DAP is 46% P2O5, potassium sulfate is 50% K2O) and state it in kg/ha (t/ha for
lime/sulfur). Do not invent measurements.

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


def parse_llm_json(content):
    """Parse LLM output, tolerating markdown code fences and stray whitespace."""
    if not isinstance(content, str):
        raise ValueError("LLM response is not text")
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start : end + 1])


def merge_recommendations(llm_recs, deterministic_recs):
    """Prefer the LLM solution per element, but only if its status is consistent
    with the computed analysis; otherwise fall back to the deterministic one."""
    merged = []
    for det in deterministic_recs:
        llm = llm_recs.get(det["element"])
        if llm is not None and isinstance(llm, dict):
            llm_status = normalize_status(llm.get("status"))
            llm_solution = llm.get("solution")
            if llm_status == det["status"] and isinstance(llm_solution, str) and llm_solution.strip():
                merged.append(
                    {
                        "element": det["element"],
                        "status": llm_status,
                        "deficit": det["deficit"],
                        "solution": llm_solution.strip(),
                        "source": "llm",
                    }
                )
                continue
        merged.append(det)
    return merged
