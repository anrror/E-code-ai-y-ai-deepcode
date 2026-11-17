"""Delivery integration settings: stored locally, never echoed back."""

from __future__ import annotations

from pathlib import Path

from core.team_context.integrations import (
    integrations_path,
    jenkins_settings,
    k8s_settings,
    raw_integrations,
    read_integrations,
    save_integrations,
)


def test_round_trip_masks_the_token(tmp_path: Path) -> None:
    home = tmp_path / "home"
    saved = save_integrations(
        "project://p",
        {
            "jenkins": {
                "url": "https://jenkins.example.com",
                "job": "apps/api",
                "user": "ci",
                "token": "s3cret",
            },
            "k8s": {"namespace": "apps", "deployment": "api"},
        },
        home=home,
    )
    assert saved["jenkins"]["hasToken"] is True
    assert "token" not in saved["jenkins"]
    assert saved["k8s"]["namespace"] == "apps"

    raw = raw_integrations("project://p", home=home)
    assert raw["jenkins"]["token"] == "s3cret"

    jenkins = jenkins_settings("project://p", home=home)
    assert jenkins["url"] == "https://jenkins.example.com"
    assert jenkins["job"] == "apps/api"

    k8s = k8s_settings("project://p", home=home)
    assert k8s["namespace"] == "apps"
    assert k8s["deployment"] == "api"


def test_saving_without_a_token_keeps_the_stored_one(tmp_path: Path) -> None:
    home = tmp_path / "home"
    save_integrations("product://x", {"jenkins": {"url": "https://j", "token": "old"}}, home=home)
    save_integrations("product://x", {"jenkins": {"job": "a"}}, home=home)
    raw = raw_integrations("product://x", home=home)
    assert raw["jenkins"]["token"] == "old"
    assert raw["jenkins"]["job"] == "a"


def test_missing_config_is_reported_not_invented(tmp_path: Path) -> None:
    home = tmp_path / "home"
    view = read_integrations("project://none", home=home)
    assert view["jenkins"] == {}
    assert view["k8s"] == {}
    assert not integrations_path(home).exists()
