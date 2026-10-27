"""Ethiopian fertilizer product catalog (specs, application rules, prices).

Loads ``Dataset/fertilizer_products.json`` — the N-P-K-S/micronutrient
compositions of the fertilizers available in Ethiopia (NPS, NPSB, Urea,
DAP, region blends), their application restrictions, decision rules,
target regions/crops, and indicative 50 kg-bag price ranges (co-op vs
open market), sourced from the EthioSIS atlas, the ISFM manual and the
Fertilizer & Soil Health Roadmap 2026-2035.

Read-only. Consumers:
  * ``fertilizer_cost.py`` — alternative product options per element
  * ``GET /api/fertilizer/products`` — full spec sheet for clients

Nothing here changes the engine's primary product recommendations; the
default cost lines and report text are untouched.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "Dataset" / "fertilizer_products.json"

# Phase 2 master switch. Default off: recommendations, report text, and cost
# lines stay byte-identical to the legacy DAP/potassium-sulfate behavior.
# Set FERT_PRODUCT_CATALOG=on to let the engine pick Ethiopian catalog
# products (NPS / NPSB / region blends) per the EthioSIS decision rules.
PRODUCT_CATALOG_ENV = "FERT_PRODUCT_CATALOG"
_OFF_VALUES = ("", "off", "0", "false", "no")

# EthioSIS decision-rule thresholds (ppm, soil test).
SULFUR_CRITICAL_PPM = 20.0   # S < 20 -> NPS/NPSB supplies sulfur
BORON_SUFFICIENT_PPM = 0.5   # B < 0.5 -> boron deficient -> NPSB
BORON_TOXIC_PPM = 2.0        # B > 2.0 -> toxicity risk -> never NPSB

# K-leached target regions (Formula 3/5 blend areas) and tuber/perennial
# target crops from the product matrix — matched case-insensitively.
BLEND_REGION_TOKENS = ("gojjam", "gamo", "sidama", "western", "southwestern",
                       "south-western")
BLEND_CROP_TOKENS = ("potato", "cassava", "enset", "avocado", "mango", "yam")

# Elements the recommendation engine works with (P = P2O5, K = K2O — the
# fertilizer-grade convention already used by the engine's nutrient contents).
ENGINE_ELEMENTS = ("N", "P", "K")

REQUIRED_TOP_KEYS = ("version", "currency", "bag_kg", "products")
REQUIRED_PRODUCT_KEYS = (
    "id", "abbr", "name", "label", "nutrients_pct", "serves",
    "application", "prices",
)


class CatalogError(ValueError):
    """The fertilizer catalog JSON is missing or malformed."""


def bag_to_etb_per_kg(bag_price, bag_kg=50):
    """50 kg-bag price (ETB) -> per-kg price (ETB), 2 decimals."""
    if bag_kg <= 0:
        raise ValueError("bag_kg must be positive")
    return round(float(bag_price) / float(bag_kg), 2)


def validate_catalog(data):
    """Raise CatalogError when the catalog dict misses required fields.

    Checks structure only — values are trusted to come from the reviewed
    source documents (EthioSIS atlas / ISFM manual / Roadmap 2026-2035).
    """
    if not isinstance(data, dict):
        raise CatalogError("catalog root must be an object")
    missing = [k for k in REQUIRED_TOP_KEYS if k not in data]
    if missing:
        raise CatalogError(f"catalog missing keys: {', '.join(missing)}")
    if not isinstance(data["products"], list) or not data["products"]:
        raise CatalogError("catalog 'products' must be a non-empty list")
    seen_ids = set()
    for index, product in enumerate(data["products"]):
        where = f"products[{index}]"
        if not isinstance(product, dict):
            raise CatalogError(f"{where} must be an object")
        missing = [k for k in REQUIRED_PRODUCT_KEYS if k not in product]
        if missing:
            raise CatalogError(
                f"{where} ({product.get('id', '?')}) missing: "
                f"{', '.join(missing)}"
            )
        pid = product["id"]
        if pid in seen_ids:
            raise CatalogError(f"duplicate product id: {pid}")
        seen_ids.add(pid)
        pct = product["nutrients_pct"]
        for element in ENGINE_ELEMENTS:
            if element not in pct:
                raise CatalogError(f"{pid} nutrients_pct missing {element}")
            value = pct[element]
            if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise CatalogError(
                    f"{pid} nutrients_pct.{element} must be 0-100, got {value!r}"
                )
        serves = product["serves"]
        if not isinstance(serves, list) or not serves:
            raise CatalogError(f"{pid} 'serves' must be a non-empty list")
        bad = [e for e in serves if e not in ENGINE_ELEMENTS]
        if bad:
            raise CatalogError(f"{pid} serves unknown elements: {bad}")
        prices = product["prices"]
        for key in ("coop_per_bag", "market_per_bag"):
            rng = prices.get(key)
            if (not isinstance(rng, list) or len(rng) != 2
                    or not all(isinstance(v, (int, float)) and v > 0
                               for v in rng)
                    or rng[0] > rng[1]):
                raise CatalogError(
                    f"{pid} prices.{key} must be [min, max] with 0 < min <= max"
                )
        application = product["application"]
        for key in ("timing", "method", "restrictions"):
            if key not in application:
                raise CatalogError(f"{pid} application missing '{key}'")
    return data


@lru_cache(maxsize=1)
def load_catalog():
    """Validated catalog dict (cached). Raises CatalogError if malformed."""
    if not CATALOG_PATH.exists():
        raise CatalogError(f"catalog file not found: {CATALOG_PATH}")
    try:
        with open(CATALOG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog is not valid JSON: {exc}") from exc
    return validate_catalog(data)


def _enrich(product, bag_kg):
    """Add derived fields: nutrient fractions (0-1) and per-kg price ranges."""
    out = dict(product)
    out["nutrients"] = {
        element: round(float(pct) / 100.0, 6)
        for element, pct in product["nutrients_pct"].items()
    }
    coop = [bag_to_etb_per_kg(v, bag_kg)
            for v in product["prices"]["coop_per_bag"]]
    market = [bag_to_etb_per_kg(v, bag_kg)
              for v in product["prices"]["market_per_bag"]]
    out["price_etb_per_kg"] = {
        "min": min(coop[0], market[0]),
        "max": max(coop[1], market[1]),
        "coop_min": coop[0],
        "coop_max": coop[1],
        "market_min": market[0],
        "market_max": market[1],
    }
    return out


@lru_cache(maxsize=1)
def get_products():
    """Catalog products with derived nutrient fractions and per-kg prices."""
    data = load_catalog()
    bag_kg = float(data.get("bag_kg", 50))
    return [_enrich(product, bag_kg) for product in data["products"]]


def get_product(product_id):
    """One enriched product by id, or None when unknown."""
    for product in get_products():
        if product["id"] == product_id:
            return product
    return None


def products_supplying(element):
    """Products whose primary role ('serves') includes this engine element.

    'P' matches P2O5 sources, 'K' matches K2O sources — the fertilizer-grade
    convention used across the engine and cost estimator.
    """
    if element not in ENGINE_ELEMENTS:
        return []
    return [p for p in get_products() if element in p["serves"]]


def price_range_etb_per_kg(product_id):
    """(min, max) indicative ETB/kg for a product, or None when unknown."""
    product = get_product(product_id)
    if product is None:
        return None
    prices = product["price_etb_per_kg"]
    return prices["min"], prices["max"]


# ---------------------------------------------------------------------------
# Product selection (Phase 2, FERT_PRODUCT_CATALOG flag)
# ---------------------------------------------------------------------------


def catalog_mode_enabled():
    """True when FERT_PRODUCT_CATALOG is set to on/1/true/yes (default off)."""
    value = os.getenv(PRODUCT_CATALOG_ENV, "").strip().lower()
    return value not in _OFF_VALUES


def legacy_selection():
    """The pre-catalog product map — what the engine has always used."""
    return {"N": "urea", "P": "dap", "K": "potassium_sulfate"}


def region_targets_k(region):
    """True when a region string matches the K-leached blend target areas."""
    if not region:
        return False
    text = str(region).lower()
    return any(token in text for token in BLEND_REGION_TOKENS)


def crop_targets_k(crop):
    """True when a crop is a tuber/enset/perennial targeted by region blends."""
    if not crop:
        return False
    text = str(crop).lower().strip()
    return any(token == text for token in BLEND_CROP_TOKENS)


def nutrient_content(col, product_id):
    """Fraction (0-1) of this engine element in this product.

    Catalog products use their JSON nutrients; legacy products fall back to
    the engine's standard contents (urea 46% N, DAP 46% P2O5, K2SO4 50% K2O).
    """
    legacy = {"N": 0.46, "P": 0.46, "K": 0.50}
    product = get_product(product_id)
    if product is not None:
        return product["nutrients"].get(col, legacy[col])
    return legacy[col]


def resolve_selection(crop=None, sulfur_ppm=None, boron_ppm=None, region=None):
    """Product map {N, P, K -> product_id} for one analysis.

    Flag off (default) → the legacy trio, always — sulfur/boron/region are
    recorded by the caller but change nothing.

    Flag on (FERT_PRODUCT_CATALOG=on) → Ethiopian catalog products:
      P: NPSB when the soil test shows boron deficiency (B < 0.5 ppm and not
         in the toxicity range), otherwise NPS (DAP is phased out for
         macro-use since 2014-15 to prevent sulfur starvation). Sulfur does
         not flip the product — NPS already supplies 7% S either way; it is
         reported through the micronutrient block.
      K: region blend (Formula 3/5) when the region matches the K-leached
         target areas or the crop is a tuber/enset/perennial target;
         potassium sulfate otherwise.
      N: urea in both modes (the national top-dress nitrogen).
    """
    if not catalog_mode_enabled():
        return legacy_selection()
    selection = {"N": "urea", "P": "nps", "K": "potassium_sulfate"}
    if (boron_ppm is not None
            and 0 <= boron_ppm < BORON_SUFFICIENT_PPM):
        selection["P"] = "npsb"
    if region_targets_k(region) or crop_targets_k(crop):
        selection["K"] = "blended"
    return selection
