"""Pricing rules. The experiment's tasks extend this module."""

from __future__ import annotations


def discount_for(subtotal: float) -> float:
    """Volume discount in currency units (0 until task 1 is implemented)."""

    return 0.0


def apply_discount(subtotal: float) -> float:
    return subtotal - discount_for(subtotal)
