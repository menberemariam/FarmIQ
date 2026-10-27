"""Fertilizer cost estimation for saved soil reports (Phase 4).

Converts nutrient deficits from `ML.engine.analyze_soil` into primary-product
amounts and multiplies them by indicative ETB/kg prices from the
`fertilizer_prices` table (see db.py seed).

Amount math is shared with the engine on purpose:
- `_fertilizer_amount` is imported, so kg/ha figures for urea / DAP /
  potassium sulfate are literally the same function the report text uses.
- The lime and sulfur t/ha math delegates to `ethiopian_standards.lime_rate_t`
  (Ethiopian LRF when a soil_type is given, legacy generic formula
  otherwise) and `_sulfur_t`, both kept in sync with the engine's template
  renderer and cross-checked by tests/test_cost_calculator.py.

Every line additionally carries two additive fields that never change the
original math: `price_source` ("db" for priced lines) and
`product_options` — the primary product first, then Ethiopian catalog
alternatives (NPS/NPSB for P, region blends for K) with indicative
co-op/open-market ETB/kg ranges from Dataset/fertilizer_products.json.

Pure read-only cost math: no model loading, no writes.
"""

import db
import ethiopian_standards as ethio
import fertilizer_catalog
from ML.engine import _fertilizer_amount, analyze_soil

# Keep in sync with ML/engine.py `_render_template` amounts dict: both
# delegate to ethiopian_standards.lime_rate_t (Ethiopian LRF when a
# soil_type is present, byte-identical legacy formula when it is not).
def _lime_t(deficit, soil_type=None, lime_type=None):
    return ethio.lime_rate_t(deficit, soil_type, lime_type)


def _sulfur_t(deficit):
    return round(min(deficit / 0.5 * 1.5, 8.0), 1)


# Primary product per element — the first material the report tells the
# farmer to apply (alternatives named in the solution text are not costed).
PRODUCT_LABELS = {
    "urea": "Urea (46% N)",
    "dap": "DAP (18% N, 46% P2O5)",
    "potassium_sulfate": "Potassium sulfate (50% K2O)",
    "lime": "Agricultural lime (CaCO3)",
    "sulfur": "Elemental sulfur",
}

_ELEMENT_BY_COL = {"N": "Nitrogen", "P": "Phosphorus", "K": "Potassium", "ph": "pH"}
# Nutrient content used for the deficit→product conversion. The defaults
# mirror the engine; when a selection is given (catalog mode) each product's
# actual content replaces the matching entry so the cost line's kg/ha figure
# equals the amount in the report text.
_NUTRIENT_CONTENT = {"N": 0.46, "P": 0.46, "K": 0.50}
# The pre-catalog product per element — always offered as a cost option so a
# farmer can price the classic DAP/potassium-sulfate program too.
_LEGACY_PRODUCT = {"N": "urea", "P": "dap", "K": "potassium_sulfate"}

DISCLAIMER_KEY = "costDisclaimer"
DISCLAIMER = (
    "Indicative demo prices in ETB/kg — replace them with local market "
    "prices before field use."
)


def _line(element, col, status, deficit, product, amount, unit, price,
          purchase=True, options=None, price_source=None):
    factor = 1000 if unit == "t/ha" else 1
    subtotal = round(amount * price * factor, 2) if purchase else 0.0
    return {
        "element": element,
        "col": col,
        "status": status,
        "deficit": round(deficit, 2),
        "product": product,
        "product_label": _product_label(product) if product else None,
        # t/ha (lime/sulfur) keeps 2 decimals so the priced amount is
        # byte-identical to the Ethiopian LRF figure in the report text;
        # kg/ha lines stay at the classic 1 decimal.
        "amount": round(amount, 2 if unit == "t/ha" else 1),
        "unit": unit,
        "price_etb_per_kg": price if purchase else 0.0,
        "subtotal": subtotal,
        "purchase": purchase,
        "price_source": price_source if purchase else None,
        "product_options": options or [],
    }


def _product_label(product_id):
    """Label for any product: db-only names first, then catalog labels."""
    if product_id in PRODUCT_LABELS:
        return PRODUCT_LABELS[product_id]
    product = fertilizer_catalog.get_product(product_id)
    return product["label"] if product else product_id


def _catalog_primary_price(product_id):
    """Midpoint of the catalog ETB/kg range for a catalog-priced product."""
    price_range = fertilizer_catalog.price_range_etb_per_kg(product_id)
    if price_range is None:
        return 0.0
    return round((price_range[0] + price_range[1]) / 2.0, 2)


def _product_options(col, primary_product, primary_price, deficit, amount,
                     unit, prices=None):
    """Candidate products for one nutrient line.

    Starts with the primary product (db-priced when available, catalog
    midpoint otherwise), then appends: the legacy product for this element
    when the primary was swapped (so DAP / potassium sulfate stay priceable
    in catalog mode), and every other catalog product serving the element.
    Catalog options use the indicative co-op/open-market ETB/kg range;
    totals become min-max ranges instead of a single figure. Purely
    additive: the line's own product, amount, and subtotal never change.
    """
    if col not in _NUTRIENT_CONTENT:
        return []
    factor = 1000 if unit == "t/ha" else 1
    prices = prices or {}

    def entry(product_id, content, entry_amount, pmin, pmax, primary, source):
        return {
            "product": product_id,
            "label": _product_label(product_id),
            "primary": primary,
            "nutrient_content": content,
            "amount": round(entry_amount, 1),
            "unit": unit,
            "price_etb_per_kg_min": pmin,
            "price_etb_per_kg_max": pmax,
            "subtotal_min": round(entry_amount * pmin * factor, 2),
            "subtotal_max": round(entry_amount * pmax * factor, 2),
            "price_source": source,
        }

    def priced(product_id):
        """(min, max, source) for one product — db single price wins.

        A missing db row for a classic product keeps the pre-existing 0.0
        behavior; only catalog-only products (NPS/NPSB/blended, never seeded
        into fertilizer_prices) fall back to their catalog range.
        """
        row = prices.get(product_id)
        if row is not None:
            price = float(row["price_etb_per_kg"])
            return price, price, "db"
        if product_id in PRODUCT_LABELS:
            return 0.0, 0.0, "db"
        price_range = fertilizer_catalog.price_range_etb_per_kg(product_id)
        if price_range is None:
            return None
        return price_range[0], price_range[1], "catalog"

    included = [primary_product]
    pmin, pmax, source = priced(primary_product)
    options = [entry(
        primary_product, fertilizer_catalog.nutrient_content(col, primary_product),
        amount, pmin, pmax, True, source,
    )]
    legacy = _LEGACY_PRODUCT[col]
    if legacy != primary_product and legacy not in included:
        included.append(legacy)
        legacy_priced = priced(legacy)
        if legacy_priced is not None:
            lpmin, lpmax, lsource = legacy_priced
            options.append(entry(
                legacy, fertilizer_catalog.nutrient_content(col, legacy),
                _fertilizer_amount(deficit,
                                   fertilizer_catalog.nutrient_content(col, legacy)),
                lpmin, lpmax, False, lsource,
            ))
    for alt in fertilizer_catalog.products_supplying(col):
        if alt["id"] in included:
            continue
        price_range = fertilizer_catalog.price_range_etb_per_kg(alt["id"])
        if price_range is None:
            continue
        included.append(alt["id"])
        alt_amount = _fertilizer_amount(deficit, alt["nutrients"][col])
        options.append(entry(
            alt["id"], alt["nutrients"][col], alt_amount,
            price_range[0], price_range[1], False, "catalog",
        ))
    return options


def estimate(N, P, K, ph, crop=None, size_ha=None, sulfur_ppm=None,
             boron_ppm=None, region=None, variety=None, zone=None, ec_e=None,
             esp=None, soil_type=None, lime_type=None, season=None,
             texture_class=None, soc_pct=None, soc_class=None, woreda=None):
    """Cost lines for one soil sample.

    Returns {currency, lines[], total_per_ha, size_ha?, total_for_plot?,
    disclaimer, disclaimer_key, crop}. `lines` always has one entry per
    element (Nitrogen, Phosphorus, Potassium, pH); elements inside the ideal
    band (or above it, except pH) are `purchase: false` zero-cost lines.

    sulfur_ppm / boron_ppm / region only matter when FERT_PRODUCT_CATALOG
    is on: they flow through fertilizer_catalog.resolve_selection so the
    priced product (NPS/NPSB/region blend) is exactly the one the report
    text tells the farmer to buy. Flag off (default) → legacy DAP /
    potassium sulfate, unchanged.

    `variety` (name/alias) selects the explicit crop x region x variety bands
    from Dataset/variety_bands.json, taking precedence over the zone and crop
    bands. `season` (meher/belg) applies the N-only seasonal modifier on top;
    P/K/pH keep the variety/zone/crop baseline. `zone` (agroecological zone
    id) otherwise switches the deficits to the Ethiopian zone bands, so the
    cost lines match the zone-aware analysis exactly. With both, the variety
    bands drive the deficits and the zone's overrides still apply.
    `ec_e` / `esp` reproduce the arid-zone salinity pause: above the zone's
    ECe threshold every nutrient line becomes `purchase: false` (gypsum /
    leaching is reclamation, not a priced nutrient line).
    `soil_type` / `lime_type` select the Ethiopian LRF lime rate (Part 1c);
    the pH line then prices the TOTAL program (all seasons) — the 5.0 t/ha
    single-application ceiling is a scheduling rule shown in the report's
    lime_plan, not a cost cap. Absent soil_type → legacy lime formula.
    `texture_class` / `soc_pct` / `soc_class` / `woreda` are the opt-in
    Enhancement E conditioning inputs; they flow through analyze_soil so the
    cost lines use the same conditioned bands as the advice text.
    """
    analysis = analyze_soil(N, P, K, ph, crop=crop, zone=zone,
                            ec_e=ec_e, esp=esp, soil_type=soil_type,
                            lime_type=lime_type, variety=variety,
                            region=region, season=season,
                            texture_class=texture_class, soc_pct=soc_pct,
                            soc_class=soc_class, woreda=woreda)
    prices = {row["product"]: row for row in db.list_fertilizer_prices()}
    selection = fertilizer_catalog.resolve_selection(
        crop=crop, sulfur_ppm=sulfur_ppm, boron_ppm=boron_ppm, region=region)

    lines = []
    for item in analysis:
        col = item["col"]
        status = item["status"]
        element = _ELEMENT_BY_COL[col]
        if item.get("suppressed"):
            # Salinity pause: fertilizer would burn roots — no purchase.
            lines.append(_line(element, col, status, 0.0,
                               None, 0.0, "kg/ha", 0.0, purchase=False))
            continue
        if status == "adequate":
            lines.append(_line(element, col, status, item["deficit"],
                               None, 0.0, "kg/ha", 0.0, purchase=False))
            continue
        if status == "ample" and col != "ph":
            lines.append(_line(element, col, status, item["deficit"],
                               None, 0.0, "kg/ha", 0.0, purchase=False))
            continue

        if col == "ph":
            if status == "deficient":  # acidic soil
                product, unit, amount = "lime", "t/ha", _lime_t(
                    item["deficit"], item.get("soil_type"),
                    item.get("lime_type"))
            else:  # alkaline soil
                product, unit, amount = "sulfur", "t/ha", _sulfur_t(item["deficit"])
        else:
            product, unit = selection[col], "kg/ha"
            amount = _fertilizer_amount(
                item["deficit"],
                fertilizer_catalog.nutrient_content(col, product))

        row = prices.get(product)
        if row is not None:
            price, price_source = float(row["price_etb_per_kg"]), "db"
        elif product in PRODUCT_LABELS:
            price, price_source = 0.0, "db"
        else:
            price, price_source = _catalog_primary_price(product), "catalog"
        options = _product_options(col, product, price, item["deficit"],
                                   amount, unit, prices=prices)
        lines.append(_line(element, col, status, item["deficit"],
                           product, amount, unit, price, options=options,
                           price_source=price_source))

    total_per_ha = round(sum(line["subtotal"] for line in lines), 2)
    result = {
        "currency": "ETB",
        "lines": lines,
        "total_per_ha": total_per_ha,
        "size_ha": size_ha,
        "total_for_plot": (
            round(total_per_ha * size_ha, 2) if size_ha else None
        ),
        "crop": crop,
        "disclaimer": DISCLAIMER,
        "disclaimer_key": DISCLAIMER_KEY,
    }
    return result
