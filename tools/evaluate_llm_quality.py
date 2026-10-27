#!/usr/bin/env python3
"""Evaluate the advisory pipeline against the Ethiopian golden set.

Default mode is deterministic and offline: it checks the pinned statuses and
advisory assertions of tests/golden/llm_cases.json without an API key.
``--use-llm`` additionally asks Groq for prose and evaluates the merged output
(cache disabled so a warmed entry cannot hide a model change).

The golden set is evaluation-only. This tool never writes a training CSV, the
training DB or a model artifact.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ML.cache import recommendation_cache  # noqa: E402
from ML.engine import (  # noqa: E402
    analyze_soil,
    llm_fact_failures,
    merge_recommendations,
    recommend_soil,
)
from ML.llm import get_llm_recommendations  # noqa: E402
from ethiopian_standards import zone_exists  # noqa: E402

DEFAULT_CASES = PROJECT_ROOT / "tests" / "golden" / "llm_cases.json"
ELEMENTS = ("Nitrogen", "Phosphorus", "Potassium", "pH")
VALID_STATUSES = ("deficient", "adequate", "ample")
INPUT_KEYS = ("N", "P", "K", "ph")

_WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u1200-\u137F']+")
_METRIC_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:kg/ha|t/ha|ppm|%)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _words(text):
    return _WORD_RE.findall(text)


def _sentences(text):
    return [part for part in re.split(r"[.!?።]+", text) if part.strip()]


def _syllables(word):
    vowels = "aeiouy"
    count = 0
    previous = False
    for char in word.lower():
        is_vowel = char in vowels
        if is_vowel and not previous:
            count += 1
        previous = is_vowel
    if word.lower().endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def flesch_kincaid_grade(text):
    """Approximate English grade level; None for empty text."""
    words = _words(text)
    if not words:
        return None
    sentences = _sentences(text) or [text]
    syllables = sum(_syllables(word) for word in words)
    grade = (0.39 * (len(words) / len(sentences))
             + 11.8 * (syllables / len(words)) - 15.59)
    return round(grade, 2)


def _ngrams(text, size):
    words = [word.lower() for word in _words(text)]
    return {
        tuple(words[index:index + size])
        for index in range(len(words) - size + 1)
    }


def ngram_novelty(candidate, baseline, size=2):
    """Percentage of candidate n-grams absent from the baseline."""
    candidate_grams = _ngrams(candidate, size)
    if not candidate_grams:
        return None
    baseline_grams = _ngrams(baseline, size)
    return round(
        100.0 * len(candidate_grams - baseline_grams) / len(candidate_grams), 1
    )


def metric_density(text):
    """Words per cited metric (kg/ha, t/ha, ppm, %); lower is denser."""
    words = len(_words(text))
    metrics = len(_METRIC_RE.findall(text))
    return round(words / max(1, metrics), 2)


def _load_cases(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("purpose") != "evaluation_only":
        raise ValueError("golden set must declare purpose=evaluation_only")
    if payload.get("not_training") is not True:
        raise ValueError("golden set must declare not_training=true")
    cases = payload.get("cases") or []
    if not cases:
        raise ValueError("golden set has no cases")
    seen = set()
    for case in cases:
        case_id = case.get("id")
        if not case_id or case_id in seen:
            raise ValueError(f"missing or duplicate case id: {case_id!r}")
        seen.add(case_id)
        if case.get("label_status") or case.get("include_in_training"):
            raise ValueError(f"{case_id}: training labels are not allowed")
        case_input = case.get("input") or {}
        for key in INPUT_KEYS:
            if key not in case_input:
                raise ValueError(f"{case_id}: input.{key} is required")
            if not isinstance(case_input[key], (int, float)):
                raise ValueError(f"{case_id}: input.{key} must be numeric")
        if not 0 <= case_input["ph"] <= 14:
            raise ValueError(f"{case_id}: input.ph out of range")
        if any(case_input[key] < 0 for key in ("N", "P", "K")):
            raise ValueError(f"{case_id}: N/P/K cannot be negative")
        zone = case_input.get("zone")
        if zone is not None and not zone_exists(zone):
            raise ValueError(f"{case_id}: unknown zone {zone!r}")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise ValueError(f"{case_id}: expected object is required")
        statuses = expected.get("statuses") or {}
        if set(statuses) != set(ELEMENTS):
            raise ValueError(f"{case_id}: expected.statuses must name all four elements")
        invalid = {
            element: status for element, status in statuses.items()
            if status not in VALID_STATUSES
        }
        if invalid:
            raise ValueError(f"{case_id}: invalid statuses {invalid!r}")
    return payload


def _element_solutions(recs):
    return {rec["element"]: rec.get("solution", "") for rec in recs}


def _check_phrases(case, recs):
    failures = []
    solutions = _element_solutions(recs)
    expected = case.get("expected") or {}
    for element, phrases in (expected.get("must_include") or {}).items():
        text = solutions.get(element, "").lower()
        for phrase in phrases:
            if phrase.lower() not in text:
                failures.append(f"{element}: missing required phrase {phrase!r}")
    for element, phrases in (expected.get("must_not_include") or {}).items():
        text = solutions.get(element, "").lower()
        for phrase in phrases:
            if phrase.lower() in text:
                failures.append(f"{element}: forbidden phrase {phrase!r}")
    for element, values in (expected.get("numbers") or {}).items():
        found = [float(raw) for raw in _NUMBER_RE.findall(solutions.get(element, ""))]
        for value in values:
            if not any(abs(number - value) <= 0.05 for number in found):
                failures.append(
                    f"{element}: expected numeric citation {value} not found"
                )
    return failures


def _status_failures(case, analysis, recs):
    expected = (case.get("expected") or {}).get("statuses") or {}
    actual = {rec["element"]: rec["status"] for rec in recs}
    computed = {item["element"]: item["status"] for item in analysis}
    failures = []
    for element in ELEMENTS:
        if expected.get(element) != actual.get(element):
            failures.append(
                f"{element}: expected status {expected.get(element)!r}, "
                f"got {actual.get(element)!r}"
            )
        if actual.get(element) != computed.get(element):
            failures.append(
                f"{element}: output status {actual.get(element)!r} contradicts "
                f"computed status {computed.get(element)!r}"
            )
    return failures


def evaluate_case(case, use_llm=False, lang="en", api_key=None):
    case_input = dict(case["input"])
    analysis = analyze_soil(**case_input)
    deterministic = recommend_soil(
        analysis, lang=lang, zone=case_input.get("zone")
    )
    recs = deterministic
    llm_meta = {"used": False, "fact_failures": [], "error": None}

    if use_llm:
        try:
            llm_recs = get_llm_recommendations(
                analysis, api_key, lang=lang
            )
        except Exception as exc:  # the helper should never raise; be defensive
            llm_recs = None
            llm_meta["error"] = type(exc).__name__
        if llm_recs:
            llm_meta["used"] = True
            llm_meta["fact_failures"] = llm_fact_failures(
                analysis, llm_recs
            )
            recs = merge_recommendations(
                llm_recs, deterministic, analysis=analysis, lang=lang
            )

    failures = _status_failures(case, analysis, recs)
    failures.extend(_check_phrases(case, recs))

    merged_text = " ".join(rec.get("solution", "") for rec in recs)
    deterministic_text = " ".join(
        rec.get("solution", "") for rec in deterministic
    )
    novelty = (
        ngram_novelty(merged_text, deterministic_text) if use_llm else None
    )
    metrics = {
        "word_count": len(_words(merged_text)),
        "avg_sentence_length": (
            round(
                len(_words(merged_text))
                / max(1, len(_sentences(merged_text))),
                2,
            )
        ),
        "flesch_kincaid": (
            flesch_kincaid_grade(merged_text) if lang == "en" else None
        ),
        "metric_density": metric_density(merged_text),
        "ngram_novelty_pct": novelty,
    }
    return {
        "id": case["id"],
        "passed": not failures,
        "failures": failures,
        "statuses": {rec["element"]: rec["status"] for rec in recs},
        "llm": llm_meta,
        "metrics": metrics,
        "tags": case.get("tags", []),
        "kind": case.get("kind", "generated"),
        "known_gaps": case.get("known_gaps", []),
    }


def _aggregate(results):
    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    per_element = {element: {"checked": 0, "passed": 0} for element in ELEMENTS}
    per_status = defaultdict(lambda: {"checked": 0, "passed": 0})
    per_tag = defaultdict(lambda: {"checked": 0, "passed": 0})
    for result in results:
        failed_elements = {
            failure.split(":", 1)[0]
            for failure in result["failures"]
            if failure.split(":", 1)[0] in ELEMENTS
        }
        for element, status in result["statuses"].items():
            per_element[element]["checked"] += 1
            if element not in failed_elements:
                per_element[element]["passed"] += 1
            per_status[status]["checked"] += 1
            if element not in failed_elements:
                per_status[status]["passed"] += 1
        for tag in result["tags"]:
            per_tag[tag]["checked"] += 1
            per_tag[tag]["passed"] += 1 if result["passed"] else 0

    def rates(mapping):
        return {
            key: {
                "checked": value["checked"],
                "passed": value["passed"],
                "rate": round(value["passed"] / value["checked"], 4)
                if value["checked"] else None,
            }
            for key, value in mapping.items()
        }

    metric_keys = (
        "word_count", "avg_sentence_length", "flesch_kincaid",
        "metric_density", "ngram_novelty_pct",
    )
    quality = {}
    for key in metric_keys:
        values = [
            result["metrics"][key] for result in results
            if result["metrics"].get(key) is not None
        ]
        quality[key] = round(sum(values) / len(values), 2) if values else None

    return {
        "cases": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else None,
        "per_element": rates(per_element),
        "per_status": rates(per_status),
        "per_tag": rates(per_tag),
        "quality": quality,
        "llm_cases": sum(1 for result in results if result["llm"]["used"]),
    }


def _print_summary(report, verbose=False):
    summary = report["summary"]
    print(
        f"Golden set: {summary['cases']} cases — pass "
        f"{summary['passed']}/{summary['cases']} "
        f"({(summary['pass_rate'] or 0) * 100:.1f}%)"
    )
    print("Per element:")
    for element, value in summary["per_element"].items():
        print(
            f"  {element:<12} {value['passed']}/{value['checked']} "
            f"({(value['rate'] or 0) * 100:.1f}%)"
        )
    print("Per status:")
    for status, value in sorted(summary["per_status"].items()):
        print(
            f"  {status:<12} {value['passed']}/{value['checked']} "
            f"({(value['rate'] or 0) * 100:.1f}%)"
        )
    print(
        "Quality (informational): "
        f"words={summary['quality']['word_count']} "
        f"avg_sentence={summary['quality']['avg_sentence_length']} "
        f"FK={summary['quality']['flesch_kincaid']} "
        f"words/metric={summary['quality']['metric_density']} "
        f"novelty={summary['quality']['ngram_novelty_pct']}"
    )
    failures = [result for result in report["results"] if not result["passed"]]
    if failures:
        print("Failures:")
        for result in failures[:None if verbose else 10]:
            print(f"  {result['id']}: {'; '.join(result['failures'])}")
        if not verbose and len(failures) > 10:
            print(f"  ... {len(failures) - 10} more (use --verbose)")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate the advisory pipeline against the Ethiopian "
                    "golden set (evaluation-only)."
    )
    parser.add_argument(
        "--cases", default=str(DEFAULT_CASES),
        help="golden cases JSON (default: tests/golden/llm_cases.json)",
    )
    parser.add_argument(
        "--use-llm", action="store_true",
        help="call Groq and evaluate the merged prose (requires GROQ_API_KEY)",
    )
    parser.add_argument(
        "--lang", choices=("en", "am", "om"), default="en",
        help="advice language (default: en)",
    )
    parser.add_argument("--json-out", help="write the full report as JSON")
    parser.add_argument(
        "--min-pass-rate", type=float, default=1.0,
        help="exit 1 below this case pass rate (default: 1.0)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = _load_cases(args.cases)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid golden set: {exc}", file=sys.stderr)
        return 2

    api_key = os.getenv("GROQ_API_KEY")
    if args.use_llm and not api_key:
        print("--use-llm requires GROQ_API_KEY", file=sys.stderr)
        return 2

    previous_cache_state = recommendation_cache.enabled
    if args.use_llm:
        recommendation_cache.enabled = False
    try:
        results = [
            evaluate_case(
                case, use_llm=args.use_llm, lang=args.lang, api_key=api_key
            )
            for case in payload["cases"]
        ]
    finally:
        recommendation_cache.enabled = previous_cache_state

    report = {
        "golden_set": {
            "path": str(Path(args.cases).resolve()),
            "purpose": payload.get("purpose"),
            "built": payload.get("built"),
            "declared_counts": payload.get("counts"),
        },
        "lang": args.lang,
        "use_llm": args.use_llm,
        "summary": _aggregate(results),
        "results": results,
    }
    _print_summary(report, verbose=args.verbose)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(f"report: {args.json_out}")
    pass_rate = report["summary"]["pass_rate"]
    return 0 if pass_rate is not None and pass_rate >= args.min_pass_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
