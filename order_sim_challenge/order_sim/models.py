from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    sku: str
    qty: int


@dataclass(frozen=True)
class InventorySku:
    sku: str
    on_hand: int


@dataclass(frozen=True)
class Order:
    order_id: str
    lines: tuple[LineItem, ...]


def validate_line(item: LineItem) -> None:
    if not (item.sku or "").strip():
        raise ValueError("sku required")
    if item.qty <= 0:
        raise ValueError("qty must be positive")
