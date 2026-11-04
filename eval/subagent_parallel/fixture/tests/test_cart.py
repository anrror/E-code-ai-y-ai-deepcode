"""Baseline behaviour that must keep working after all five tasks."""

from __future__ import annotations

from app.cart import Cart


def test_subtotal_counts_quantity() -> None:
    cart = Cart()
    cart.add("a", 2.5, 4)
    assert cart.subtotal() == 10.0
