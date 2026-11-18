"""Delivery integrations: an external Jenkins job and Kubernetes target per layer.

Secrets live in ``~/.deepcode/integrations.yaml`` (0600) rather than in the team
store, so a team snapshot can be shared without leaking a Jenkins token or a
kubeconfig path. Nothing here is ever sent to a model: only the RPC layer reads
a token, and it never returns it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from core.config import deepcode_home

_INTEGRATIONS = "integrations.yaml"


class IntegrationError(Exception):
    """A configuration problem the caller can fix."""


def integrations_path(home: Path | None = None) -> Path:
    base = home or deepcode_home().parent
    return base / ".deepcode" / _INTEGRATIONS


def _read(home: Path | None) -> dict[str, Any]:
    path = integrations_path(home)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise IntegrationError(f"integrations.yaml is not valid YAML: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _write(home: Path | None, data: dict[str, Any]) -> None:
    path = integrations_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    # A half-written file would lose the stored Jenkins token, and the desktop
    # app reads this file on every integration RPC. Write a private temp file,
    # fsync it, then swap it in atomically. os.open with 0o600 closes the
    # window where the token sat in a world-readable file before chmod.
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_integrations(entity_id: str, home: Path | None = None) -> dict[str, Any]:
    """The public view: tokens are replaced by ``hasToken``."""

    entry = _read(home).get(entity_id) or {}
    jenkins = dict(entry.get("jenkins") or {})
    if jenkins.get("token"):
        jenkins["hasToken"] = True
        jenkins.pop("token", None)
    k8s = dict(entry.get("k8s") or {})
    return {"entityId": entity_id, "jenkins": jenkins, "k8s": k8s}


def save_integrations(
    entity_id: str, payload: dict[str, Any], home: Path | None = None
) -> dict[str, Any]:
    data = _read(home)
    entry = dict(data.get(entity_id) or {})
    jenkins = payload.get("jenkins")
    if isinstance(jenkins, dict):
        current = dict(entry.get("jenkins") or {})
        for key in ("url", "job", "user", "token"):
            value = jenkins.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if key == "token" and not text:
                continue
            current[key] = text
        entry["jenkins"] = current
    k8s = payload.get("k8s")
    if isinstance(k8s, dict):
        current = dict(entry.get("k8s") or {})
        for key in ("kubeconfig", "context", "namespace", "deployment", "labelSelector"):
            value = k8s.get(key)
            if value is None:
                continue
            current[key] = str(value).strip()
        entry["k8s"] = current
    if payload.get("clearJenkins"):
        entry.pop("jenkins", None)
    if payload.get("clearK8s"):
        entry.pop("k8s", None)
    data[entity_id] = entry
    _write(home, data)
    return read_integrations(entity_id, home)


def raw_integrations(entity_id: str, home: Path | None = None) -> dict[str, Any]:
    """Internal view with the token, for the Jenkins client only."""

    return dict(_read(home).get(entity_id) or {})


def jenkins_settings(entity_id: str, home: Path | None = None) -> dict[str, str]:
    jenkins = raw_integrations(entity_id, home).get("jenkins") or {}
    url = str(jenkins.get("url") or "").rstrip("/")
    job = str(jenkins.get("job") or "").strip("/")
    if not url or not job:
        raise IntegrationError("configure the Jenkins URL and job first")
    return {
        "url": url,
        "job": job,
        "user": str(jenkins.get("user") or ""),
        "token": str(jenkins.get("token") or ""),
    }


def k8s_settings(entity_id: str, home: Path | None = None) -> dict[str, str]:
    k8s = raw_integrations(entity_id, home).get("k8s") or {}
    namespace = str(k8s.get("namespace") or "default")
    kubeconfig = str(k8s.get("kubeconfig") or "")
    return {
        "kubeconfig": kubeconfig,
        "context": str(k8s.get("context") or ""),
        "namespace": namespace,
        "deployment": str(k8s.get("deployment") or ""),
        "labelSelector": str(k8s.get("labelSelector") or ""),
    }
