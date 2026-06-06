"""CLI: python -m order_sim.cli --orders <path.json>"""

from __future__ import annotations

import argparse
import json

from .checkout import checkout
from .inventory import Inventory
from .models import LineItem, Order


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", required=True)
    args = parser.parse_args()

    with open(args.orders) as f:
        data = json.load(f)

    prices: dict[str, int] = data["prices"]
    stock_raw: dict[str, int] = data.get("stock", {})
    stock: dict[str, int] = {sku: stock_raw.get(sku, 100) for sku in prices}

    inv = Inventory(stock)
    order = Order(
        data["order_id"],
        tuple(LineItem(li["sku"], li["qty"]) for li in data["lines"]),
    )
    result = checkout(order, prices, inv)
    result["order_id"] = order.order_id
    print(json.dumps(result))


if __name__ == "__main__":
    main()
