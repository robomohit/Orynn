"""Thread-safe inventory using optimistic reserve: deduct on reserve, restore on release."""

from __future__ import annotations

import threading
import uuid

from .models import LineItem


class InventoryError(Exception):
    pass


class Inventory:
    def __init__(self, stock: dict[str, int]) -> None:
        self._stock = dict(stock)
        self._tokens: dict[str, dict[str, int]] = {}
        self._lock = threading.RLock()

    def reserve(self, lines: tuple[LineItem, ...]) -> str:
        with self._lock:
            needed: dict[str, int] = {}
            for li in lines:
                needed[li.sku] = needed.get(li.sku, 0) + li.qty
            for sku, qty in needed.items():
                if self._stock.get(sku, 0) < qty:
                    raise InventoryError("short")
            for sku, qty in needed.items():
                self._stock[sku] -= qty
            tok = uuid.uuid4().hex
            self._tokens[tok] = needed
            return tok

    def commit(self, token: str) -> None:
        self._tokens.pop(token, None)

    def release(self, token: str) -> None:
        with self._lock:
            need = self._tokens.pop(token, None)
            if not need:
                return
            for sku, q in need.items():
                self._stock[sku] = self._stock.get(sku, 0) + q

    def on_hand(self, sku: str) -> int:
        return int(self._stock.get(sku, 0))
