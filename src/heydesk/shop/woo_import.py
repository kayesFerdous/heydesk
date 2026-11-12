"""A linked WooCommerce store's products as ours. Woo's fields are the same in every store,
so they're matched here. Pure."""

import html
import re
from typing import Any

from heydesk.shop.csv_import import parse_money
from heydesk.shop.products import Product, merge_variant_rows

SIZE, COLOUR = {"size"}, {"colour", "color"}  # attribute names, in any case
WOO_DEFAULT_CATEGORY = "Uncategorized"  # Woo's own default category: ours is "Products"


def products_from_woo(items: list[dict[str, Any]]) -> tuple[list[Product], dict[str, Any]]:
    """Woo's published products as ours, and each code's Woo ids for writing its order lines:
    `{code: {"id": 11, "variations": {"M|White": 15}}}`. A variable product's variations are
    its variants; variations that cost differently are one product per price, as rows
    are grouped. What can't be bought (no price, a grouped or external product) is left
    out. Raises ValueError for a product that doesn't fit ours."""
    products: list[Product] = []
    ids: dict[str, Any] = {}
    for item in items:
        if not item.get("price") or item.get("type") not in ("simple", "variable"):
            continue
        code = (item.get("sku") or f"woo-{item['id']}")[:50]
        base = {
            "code": code,
            "name": plain_text(item["name"])[:200],
            "description": woo_about(item),
            "category": woo_category(item),
            "sizes": woo_options(item["attributes"], SIZE),
            "colours": woo_options(item["attributes"], COLOUR),
            "photos": [i["src"] for i in item.get("images") or [] if i.get("src")][:3],
        }
        if item["type"] == "simple":
            products.append(
                Product.model_validate(base | woo_prices(item) | {"stock": woo_stock(item)})
            )
            ids[code] = {"id": item["id"], "variations": {}}
            continue
        found, by_price = woo_variations(base, item)
        cheapest = min(by_price)
        for price, product in sorted(by_price.items()):
            if price != cheapest:  # another price is another product: "Linen Panjabi (XXL)"
                named = ", ".join(product.sizes or product.colours)
                product = product.model_copy(
                    update={
                        "code": f"{code}-{price // 100}"[:50],
                        "name": f"{product.name} ({named})",
                    }
                )
            products.append(product)
            ids[product.code] = {"id": item["id"], "variations": found[price]}
    return products, ids


def woo_variations(
    base: dict[str, Any], item: dict[str, Any]
) -> tuple[dict[int, dict[str, int]], dict[int, Product]]:
    """A variable product's variations: a row each, grouped by price. Returns, per
    price, the variation ids by "size|colour" and the product."""
    rows: list[Product] = []
    found: dict[int, dict[str, int]] = {}
    for v in item.get("variations", []):
        if not v.get("price"):
            continue
        size = woo_option(v["attributes"], SIZE)
        colour = woo_option(v["attributes"], COLOUR)
        row = base | woo_prices(v) | {"stock": woo_stock(v)}
        row |= {"sizes": [size] if size else base["sizes"]}
        row |= {"colours": [colour] if colour else base["colours"]}
        rows.append(Product.model_validate(row))
        found.setdefault(rows[-1].price, {})[f"{size}|{colour}"] = v["id"]
    by_price = {p.price: p for p in merge_variant_rows(rows)}
    if not by_price:
        raise ValueError(f"{base['name']}: none of its variations has a price")
    return found, by_price


def woo_ids(ids: dict[str, Any], line: dict[str, Any]) -> tuple[int, int | None]:
    """An order line's Woo product and variation ids. A variation for any size or colour is
    written "|White" or "M|". Raises KeyError when the product isn't in the store any more."""
    product = ids[line["code"]]
    size, colour = line["size"], line["colour"]
    keys = [f"{size}|{colour}", f"|{colour}", f"{size}|", "|"]
    variation = next((product["variations"][k] for k in keys if k in product["variations"]), None)
    if product["variations"] and variation is None:
        raise KeyError(line["code"])
    return product["id"], variation


def woo_prices(item: dict[str, Any]) -> dict[str, int | None]:
    price = parse_money(item["price"])
    regular = parse_money(item["regular_price"]) if item.get("regular_price") else 0
    return {"price": price, "was_price": regular if regular > price else None}


def woo_stock(item: dict[str, Any]) -> int | None:
    """Woo's count, never below 0 (Woo lets it go under on backorder); a count it doesn't keep
    is 0 when it says out of stock, otherwise not counted."""
    if item.get("manage_stock") is True and item.get("stock_quantity") is not None:
        return max(0, int(item["stock_quantity"]))
    return 0 if item.get("stock_status") == "outofstock" else None


def woo_options(attributes: list[dict[str, Any]], names: set[str]) -> list[str]:
    """A product's sizes or colours: the options of its attribute with one of these names."""
    found = next((a for a in attributes if a["name"].strip().lower() in names), None)
    return [plain_text(o) for o in found["options"]] if found else []


def woo_option(attributes: list[dict[str, Any]], names: set[str]) -> str:
    """A variation's size or colour; "" when it's any of them."""
    found = next((a for a in attributes if a["name"].strip().lower() in names), None)
    return plain_text(found["option"]) if found else ""


def woo_about(item: dict[str, Any]) -> str:
    """Its short description, else its description, as plain text, and its other attributes
    as a line: "Fabric: Linen"."""
    said = plain_text(item.get("short_description") or item.get("description") or "")
    other = [
        f"{plain_text(a['name'])}: {', '.join(plain_text(o) for o in a['options'])}"
        for a in item.get("attributes", [])
        if a["name"].strip().lower() not in SIZE | COLOUR and a.get("options")
    ]
    return " ".join(p if p.endswith(".") else p + "." for p in [said, *other] if p)[:500]


def woo_category(item: dict[str, Any]) -> str:
    names = [
        plain_text(c["name"])
        for c in item.get("categories", [])
        if c["name"] != WOO_DEFAULT_CATEGORY
    ]
    return names[0][:100] if names else "Products"


def plain_text(html_text: str) -> str:
    """Woo's HTML as one line of plain text: "<p>Soft <b>linen</b>.</p>" → "Soft linen."."""
    words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html_text)).split())
    return re.sub(r" ([.,;:!?])", r"\1", words)  # a tag ended right before it
