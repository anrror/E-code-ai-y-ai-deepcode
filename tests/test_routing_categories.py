"""Routing categories are complete, closed targets.

A category is ``(model, connection)``. If one half is omitted the provider
resolver would fill it from ``agents.implementation.*`` — a different model or
provider than the caller asked for. The schema requires both and rejects
unknown keys, so a misspelled field cannot silently look effective.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_setup import build_routing_categories
from core.config import AgentsConfig


def test_complete_category_maps_to_its_target():
    config = AgentsConfig.model_validate(
        {
            "categories": {
                "fast": {"model": "deepseek-flash", "connection": "my-ds"},
                "hard": {"model": "deepseek-v4-pro", "connection": "my-ds"},
            }
        }
    )

    assert build_routing_categories(config) == {
        "fast": ("deepseek-flash", "my-ds"),
        "hard": ("deepseek-v4-pro", "my-ds"),
    }


def test_category_without_connection_is_rejected_by_the_schema():
    with pytest.raises(ValidationError) as excinfo:
        AgentsConfig.model_validate(
            {"categories": {"fast": {"model": "deepseek-flash"}}}
        )

    assert "connection" in str(excinfo.value)


def test_category_without_model_is_rejected_by_the_schema():
    with pytest.raises(ValidationError) as excinfo:
        AgentsConfig.model_validate({"categories": {"fast": {"connection": "my-ds"}}})

    assert "model" in str(excinfo.value)


def test_unknown_category_key_is_rejected():
    with pytest.raises(ValidationError) as excinfo:
        AgentsConfig.model_validate(
            {
                "categories": {
                    "fast": {
                        "model": "deepseek-flash",
                        "connection": "my-ds",
                        "temperature": 0.2,
                    }
                }
            }
        )

    assert "temperature" in str(excinfo.value)


def test_no_categories_means_no_routing():
    assert build_routing_categories(AgentsConfig()) == {}
