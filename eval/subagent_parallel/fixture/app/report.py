"""Reporting helpers. The experiment's tasks extend this module."""

from __future__ import annotations


def format_amount(amount: float) -> str:
    return f"{amount:.2f}"
