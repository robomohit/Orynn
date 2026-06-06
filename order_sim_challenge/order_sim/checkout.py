"""Checkout: reserve inventory, compute totals, commit. Releases on any failure."""

from __future__ import annotations

from .inventory import Inventory
from .models import Order
from . import pricing


def checkout(order: Order, unit_prices: dict[str, int], inventory: Inventory) -> dict:
    token = inventory.reserve(order.lines)
    try:
        sub = pricing.subtotal(order.lines, unit_prices)
        disc = pricing.discount_cents(sub, len(order.lines))
        tax = pricing.tax_cents(sub - disc)
        total = sub - disc + tax
    except Exception:
        inventory.release(token)
        raise
    inventory.commit(token)
    return {
        "subtotal": sub,
        "discount": disc,
        "tax": tax,
        "total": total,
        "reservation": token,
    }
