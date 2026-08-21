"""Deterministic soil analysis + recommendation engine.

Every amount returned here is *calculated* from the measured nutrient deficit
(kg/ha) using standard fertilizer nutrient contents, never guessed. When a crop
is provided, its real per-crop bands (ML/crop_bands.json, derived from the
dataset) are used instead of the generic agronomic ranges.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

import config
from i18n import CROP_NAMES_AM, normalize_amharic

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


def _for_crop(item, lang="en"):
    crop = item.get("crop")
    if not crop:
        return ""
    if lang == "am":
        return f" ለ{CROP_NAMES_AM.get(crop, crop)}"
    return f" for {crop}"


def _band_text(item):
    lo, hi = item["band"]
    return f"{lo:.0f}-{hi:.0f}" if item["col"] != "ph" else f"{lo:.1f}-{hi:.1f}"


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


def _render_template(kind, status, item, lang):
    """Render the localized template for one element with the computed amounts."""
    templates = _AM_TEMPLATES if lang == "am" else _EN_TEMPLATES
    key = f"{kind}_{status}"
    if key not in templates:
        key = f"{kind}_adequate"
    amounts = {
        "deficit": item["deficit"],
        "band": _band_text(item),
        "crop": _for_crop(item, lang),
        "urea": _fertilizer_amount(item["deficit"], 0.46),
        "dap": _fertilizer_amount(item["deficit"], 0.46),
        "compost": round(max(item["deficit"] * 40, 0), 0),
        "k_sulfate": _fertilizer_amount(item["deficit"], 0.50),
        "lime_t": round(min(item["deficit"] / 0.5 * 2.0, 10.0), 1),
        "s_t": round(min(item["deficit"] / 0.5 * 1.5, 8.0), 1),
    }
    return templates[key].format(**amounts)


def recommend_soil(analysis, lang="en"):
    """Deterministic recommendations for every element in the analysis,
    localized into the requested language (en or am)."""
    kinds = {"N": "n", "P": "p", "K": "k", "ph": "ph"}
    out = []
    for item in analysis:
        text = _render_template(kinds[item["col"]], item["status"], item, lang)
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


def build_prompt(analysis, lang="en"):
    crop = analysis[0].get("crop") if analysis else None
    crop_line = f"\nTarget crop: {crop}." if crop else ""
    language_line = (
        '\nWrite every "solution" in Amharic (አማርኛ). Keep the JSON keys and '
        "status values in English. Use formal extension-style register, bilingual "
        "scientific terms (Amharic + English), end sentences with ።, and keep the "
        "numbers and kg/ha units exactly as given."
        if lang == "am"
        else ""
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
lime/sulfur). Do not invent measurements.{language_line}

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


def _numbers_in(text):
    return [float(m) for m in re.findall(r"\d+\.?\d*", text)]


def llm_fact_failures(analysis, llm_recs):
    """N5 numeric-fact validation: every LLM solution must restate at least one
    of the computed facts (band edges, deficit, or derived fertilizer amount)
    within tolerance. Elements that fail fall back to deterministic text."""
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
        matched = any(
            any(abs(n - fact) <= max(1.0, abs(fact) * 0.05) for n in nums) for fact in facts
        )
        if not matched:
            failures.append(item["element"])
    return failures


def merge_recommendations(llm_recs, deterministic_recs, analysis=None, lang="en"):
    """Prefer the LLM solution per element, but only if its status is consistent
    with the computed analysis AND its numbers match the computed facts (N5);
    otherwise fall back to the deterministic (localized) one."""
    failures = set(llm_fact_failures(analysis, llm_recs)) if analysis else set()
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
