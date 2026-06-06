from __future__ import annotations

import threading
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import pytest

from order_sim.inventory import Inventory, InventoryError
from order_sim.models import LineItem, Order
from order_sim import pricing


def _ref_tax_cents(taxable_cents: int) -> int:
    return int(
        (Decimal(taxable_cents) * Decimal("0.0825")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _ref_discount_cents(subtotal_cents: int, line_count: int) -> int:
    if line_count <= 2:
        return 0
    if line_count <= 5:
        return int((Decimal(subtotal_cents) * Decimal("0.05")).to_integral_value(rounding=ROUND_DOWN))
    return int((Decimal(subtotal_cents) * Decimal("0.10")).to_integral_value(rounding=ROUND_DOWN))


def test_tax_matches_decimal_reference():
    for taxable in (0, 1, 3, 6, 13, 100, 999):
        assert pricing.tax_cents(taxable) == _ref_tax_cents(taxable)


def test_discount_tier_boundaries():
    sub = 10_000
    assert pricing.discount_cents(sub, 2) == _ref_discount_cents(sub, 2)
    assert pricing.discount_cents(sub, 3) == _ref_discount_cents(sub, 3)
    assert pricing.discount_cents(sub, 5) == _ref_discount_cents(sub, 5)
    assert pricing.discount_cents(sub, 6) == _ref_discount_cents(sub, 6)


def test_concurrent_last_unit_only_one_reserve():
    inv = Inventory({"A": 1})
    lines = (LineItem("A", 1),)
    ok = {"n": 0}
    lock = threading.Lock()

    def try_reserve():
        try:
            inv.reserve(lines)
            with lock:
                ok["n"] += 1
        except InventoryError:
            pass

    t1 = threading.Thread(target=try_reserve)
    t2 = threading.Thread(target=try_reserve)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert ok["n"] == 1


def test_subtotal_basic():
    lines = (LineItem("A", 2), LineItem("B", 1))
    prices = {"A": 100, "B": 50}
    assert pricing.subtotal(lines, prices) == 250


def test_checkout_reduces_stock_and_totals():
    from order_sim.checkout import checkout

    inv = Inventory({"A": 10})
    order = Order("o1", (LineItem("A", 2), LineItem("A", 1)))  # 3 lines same sku ok
    unit = {"A": 1000}
    out = checkout(order, unit, inv)
    sub = pricing.subtotal(order.lines, unit)
    disc = pricing.discount_cents(sub, len(order.lines))
    tax = pricing.tax_cents(sub - disc)
    assert out["subtotal"] == sub
    assert out["discount"] == disc
    assert out["tax"] == tax
    assert out["total"] == sub - disc + tax
    assert inv.on_hand("A") == 7
