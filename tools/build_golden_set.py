#!/usr/bin/env python3
"""Build the evaluation-only golden set for the LLM advisory pipeline.

The golden set is *not* training data. It is a deterministic regression and
contract fixture: the statuses are pinned from the same band engine that the
API serves, and the five hand-authored stress cases add advisory assertions
(lime-first, NPSZnB, no nitrogen top-dress, etc.).

Run:
    python3 tools/build_golden_set.py
    python3 tools/evaluate_llm_quality.py
"""

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ethiopian_standards as ethio  # noqa: E402
from ML.engine import analyze_soil  # noqa: E402

OUT_PATH = PROJECT_ROOT / "tests" / "golden" / "llm_cases.json"

ZONES = list(ethio.list_zones())
CROPS = [
    "teff", "wheat", "maize", "barley", "sorghum", "chickpea",
    "onion", "faba_bean", "coffee", "sesame", "lentil", "rice",
]
ELEMENTS = ("Nitrogen", "Phosphorus", "Potassium", "pH")
COLS = {"Nitrogen": "N", "Phosphorus": "P", "Potassium": "K", "pH": "ph"}

SAFE_RANGES = {
    "N": (0.0, 400.0),
    "P": (0.0, 300.0),
    "K": (0.0, 1500.0),
    "ph": (3.5, 9.94),
}

PATTERNS = [
    ("deficient", "deficient", "deficient", "deficient"),
    ("deficient", "deficient", "deficient", "adequate"),
    ("deficient", "deficient", "adequate", "deficient"),
    ("deficient", "adequate", "deficient", "adequate"),
    ("adequate", "deficient", "adequate", "adequate"),
    ("adequate", "adequate", "adequate", "adequate"),
    ("deficient", "deficient", "ample", "deficient"),
    ("ample", "deficient", "adequate", "ample"),
    ("deficient", "adequate", "adequate", "deficient"),
    ("adequate", "adequate", "deficient", "adequate"),
    ("deficient", "deficient", "deficient", "ample"),
]

STRESS_CASES = [
    {
        "id": "stress_acidic_nitisol_wheat",
        "input": {
            "N": 140, "P": 5, "K": 350, "ph": 4.8,
            "crop": "wheat", "zone": "highland_nitisols",
            "soil_type": "nitisol",
        },
        "must_include": {
            "pH": ["lime", "first"],
            "Phosphorus": ["pH below 5", "efficiency"],
        },
        "must_not_include": {},
        "numbers": {"pH": [2.94]},
        "tags": ["acidic", "nitisol", "wheat", "lime_first", "edge"],
        "rationale": (
            "Strongly acidic Nitisol. The repo pins pH 5.5-6.5 and the LRF "
            "yields 2.94 t/ha lime (not the paste's 4-5 t/ha); applied P is "
            "scaled by the 0.4 fixation factor."
        ),
    },
    {
        "id": "stress_high_k_rift_onion",
        "input": {
            "N": 150, "P": 40, "K": 950, "ph": 7.8,
            "crop": "onion", "zone": "rift_valley",
        },
        "must_include": {"Phosphorus": ["NPSZnB"]},
        "must_not_include": {"Potassium": ["Apply maintenance doses"]},
        "numbers": {},
        "tags": ["high_k", "rift_valley", "onion", "micronutrient_lockout"],
        "rationale": (
            "Rift Valley K band is 400-900 kg/ha, so 950 (not the paste's "
            "850) is the ample case. pH 7.8 crosses the 7.5 Zn/B lockout "
            "threshold, so the P advice must switch to NPSZnB and K must not "
            "get a maintenance dose."
        ),
    },
    {
        "id": "stress_teff_lodging_boundary",
        "input": {
            "N": 125, "P": 50, "K": 300, "ph": 6.2, "crop": "teff",
        },
        "must_include": {"Nitrogen": ["Do not add more nitrogen"]},
        "must_not_include": {"Nitrogen": ["Apply urea"]},
        "numbers": {},
        "tags": ["teff", "lodging", "edge"],
        "rationale": (
            "Generic N band is 60-100 kg/ha, so 125 is ample in this repo "
            "(the paste assumed 'adequate'). The safety property under test "
            "is that no cereal-booster nitrogen is prescribed."
        ),
    },
    {
        "id": "stress_vertisol_chickpea_legume_gap",
        "input": {
            "N": 80, "P": 45, "K": 400, "ph": 6.8,
            "crop": "chickpea", "zone": "highland_vertisols",
            "soil_type": "vertisol",
        },
        "must_include": {},
        "must_not_include": {},
        "numbers": {},
        "tags": ["vertisol", "chickpea", "legume", "known_gap"],
        "known_gaps": ["legume_n_fixation"],
        "rationale": (
            "Vertisol N band is 150-300 kg/ha, so N=80 is deficient and the "
            "engine currently recommends urea. The repo has no legume / "
            "nodulation template yet; this case records the gap without "
            "gating on advisory text that does not exist."
        ),
    },
    {
        "id": "stress_alkaline_rift_maize",
        "input": {
            "N": 180, "P": 20, "K": 500, "ph": 8.4,
            "crop": "maize", "zone": "rift_valley",
        },
        "must_include": {"Phosphorus": ["NPSZnB"]},
        "must_not_include": {},
        "numbers": {},
        "tags": ["alkaline", "rift_valley", "maize", "micronutrient_lockout"],
        "rationale": (
            "pH 8.4 is above the Rift Valley 7.5 threshold; the P advice must "
            "name the NPSZnB blend rather than plain NPS/DAP."
        ),
    },
]


def _clamp(col, value):
    lo, hi = SAFE_RANGES[col]
    return min(hi, max(lo, value))


def _element_value(col, lo, hi, status, edge_kind):
    if edge_kind == "band_edge":
        value = lo
    elif edge_kind == "deficit_0.5":
        value = lo - 0.5
    elif status == "deficient":
        value = lo - max(1.0, 0.15 * (hi - lo))
    elif status == "adequate":
        value = (lo + hi) / 2.0
    else:
        value = hi + max(1.0, 0.15 * (hi - lo))
    return round(_clamp(col, value), 2)


def _statuses(case_input):
    analysis = analyze_soil(**case_input)
    return {item["element"]: item["status"] for item in analysis}


def _stress_cases():
    out = []
    for spec in STRESS_CASES:
        case = {
            "id": spec["id"],
            "kind": "stress",
            "tags": spec["tags"],
            "input": spec["input"],
            "expected": {
                "statuses": _statuses(spec["input"]),
                "must_include": spec.get("must_include", {}),
                "must_not_include": spec.get("must_not_include", {}),
                "numbers": spec.get("numbers", {}),
            },
            "review_status": "unverified",
            "label_origin": "rule_derived",
            "source": (
                "Enhancement 4 stress case; expectations pinned to this repo's "
                "EthioSIS/EIAR bands and zone overrides"
            ),
            "rationale": spec["rationale"],
            "gate": True,
        }
        if spec.get("known_gaps"):
            case["known_gaps"] = spec["known_gaps"]
        out.append(case)
    return out


def _generated_cases(count=45):
    out = []
    for index in range(count):
        zone = ZONES[index % len(ZONES)]
        crop = CROPS[(index * 3) % len(CROPS)]
        pattern = PATTERNS[index % len(PATTERNS)]
        bands = ethio.zone_advice_ranges(zone)
        edge_kind = None
        if index % 6 == 5:
            edge_kind = (
                "band_edge" if (index // 6) % 2 == 0 else "deficit_0.5"
            )
        values = {}
        for element, col in COLS.items():
            lo, hi = bands[col]
            status = pattern[ELEMENTS.index(element)]
            kind = edge_kind if col == "N" else None
            values[col] = _element_value(col, lo, hi, status, kind)
        case_input = {
            "N": values["N"], "P": values["P"], "K": values["K"],
            "ph": values["ph"], "crop": crop, "zone": zone,
        }
        statuses = _statuses(case_input)
        tags = [zone, crop, "synthetic_regression"]
        if edge_kind:
            tags.append("edge")
        out.append(
            {
                "id": f"gen_{index + 1:02d}_{zone}_{crop}",
                "kind": "generated",
                "tags": tags,
                "input": case_input,
                "expected": {
                    "statuses": statuses,
                    "must_include": {},
                    "must_not_include": {},
                    "numbers": {},
                },
                "review_status": "unverified",
                "label_origin": "rule_derived",
                "source": (
                    "Generated by tools/build_golden_set.py from "
                    "Dataset/agroecological_zones.json; statuses are "
                    "rule-derived regression pins, not field evidence"
                ),
                "rationale": (
                    f"Regression case for {zone} / {crop}"
                    + (f" ({edge_kind})" if edge_kind else "")
                    + "; expected statuses come from the deterministic engine."
                ),
                "gate": True,
            }
        )
    return out


def _distribution(cases):
    counts = {name: 0 for name in ELEMENTS}
    for case in cases:
        for element, status in case["expected"]["statuses"].items():
            counts[element] += 1
    by_status = {}
    for case in cases:
        for status in case["expected"]["statuses"].values():
            by_status[status] = by_status.get(status, 0) + 1
    return {
        "elements_evaluated": sum(counts.values()),
        "by_status": dict(sorted(by_status.items())),
        "edge_cases": sum(1 for c in cases if "edge" in c["tags"]),
        "stress_cases": sum(1 for c in cases if c["kind"] == "stress"),
        "generated_cases": sum(1 for c in cases if c["kind"] == "generated"),
    }


def build():
    cases = _stress_cases() + _generated_cases()
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate golden case id")
    return {
        "version": 1,
        "purpose": "evaluation_only",
        "not_training": True,
        "built": date.today().isoformat(),
        "description": (
            "Golden evaluation set for the LLM advisory pipeline. Never export "
            "to Dataset/*.csv and never attach training labels."
        ),
        "counts": _distribution(cases),
        "cases": cases,
    }


def main():
    payload = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    counts = payload["counts"]
    print(
        f"wrote {OUT_PATH} — {len(payload['cases'])} cases "
        f"({counts['stress_cases']} stress, {counts['generated_cases']} generated, "
        f"{counts['edge_cases']} edge)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
