"""Minimal cart fixture for the serial-vs-parallel experiment.

Deliberately small but multi-file: the five tasks in TASKS.md all touch
``cart.py`` and ``pricing.py``, which is what makes isolated parallel merges
observable instead of theoretical.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Cart:
    items: list[dict] = field(default_factory=list)

    def add(self, sku: str, price: float, qty: int = 1) -> None:
        self.items.append({"sku": sku, "price": price, "qty": qty})

    def subtotal(self) -> float:
        return sum(item["price"] * item["qty"] for item in self.items)

    def total(self) -> float:
        return self.subtotal()
