"""Pricing: subtotal, discount tiers, and tax using Decimal arithmetic."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .models import LineItem


def subtotal(lines: tuple[LineItem, ...], unit_prices: dict[str, int]) -> int:
    t = 0
    for li in lines:
        t += unit_prices[li.sku] * li.qty
    return int(t)


def discount_cents(subtotal_cents: int, line_count: int) -> int:
    if line_count <= 2:
        return 0
    if line_count <= 5:
        return int((Decimal(subtotal_cents) * Decimal("0.05")).to_integral_value(rounding=ROUND_DOWN))
    return int((Decimal(subtotal_cents) * Decimal("0.10")).to_integral_value(rounding=ROUND_DOWN))


def tax_cents(taxable_cents: int) -> int:
    return int(
        (Decimal(taxable_cents) * Decimal("0.0825")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
