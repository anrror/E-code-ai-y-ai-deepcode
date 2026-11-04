"""Routing categories must be complete targets.

A category is ``(model, connection)``. If one half is omitted the provider
resolver fills it from ``agents.implementation.*`` — a different model or
provider than the caller asked for. That is a silent misroute, so it is
rejected when the session is built.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_setup import build_routing_categories
from core.config import AgentsConfig, ConfigError


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


def test_category_without_connection_is_rejected():
    config = AgentsConfig.model_validate(
        {"categories": {"fast": {"model": "deepseek-flash"}}}
    )

    with pytest.raises(ConfigError) as excinfo:
        build_routing_categories(config)

    assert "fast" in str(excinfo.value)
    assert "connection" in str(excinfo.value)


def test_category_without_model_is_rejected():
    config = AgentsConfig.model_validate(
        {"categories": {"fast": {"connection": "my-ds"}}}
    )

    with pytest.raises(ConfigError) as excinfo:
        build_routing_categories(config)

    assert "model" in str(excinfo.value)


def test_no_categories_means_no_routing():
    assert build_routing_categories(AgentsConfig()) == {}
