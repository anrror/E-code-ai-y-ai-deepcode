"""Kubernetes read/act operations through the API server, no kubectl required.

The kubeconfig is parsed here (token or client-certificate users only; exec
plugins such as EKS/GKE are reported as unsupported instead of guessed at).
Cluster credentials never leave this module.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.team_context.integrations import IntegrationError, k8s_settings

TIMEOUT = 20.0


def _load_kubeconfig(path_text: str) -> tuple[dict[str, Any], Path]:
    path = Path(path_text).expanduser() if path_text else Path.home() / ".kube" / "config"
    if not path.is_file():
        raise IntegrationError(f"kubeconfig not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise IntegrationError(f"kubeconfig is not valid YAML: {exc}") from exc
    return data, path


def _pick(items: list[dict[str, Any]], name: str | None) -> dict[str, Any]:
    if name:
        for item in items:
            if item.get("name") == name:
                return item
    return items[0] if items else {}


def _resolve_cluster(settings: dict[str, str]) -> dict[str, Any]:
    data, base = _load_kubeconfig(settings["kubeconfig"])
    context_name = settings["context"] or str(data.get("current-context") or "")
    context = _pick(data.get("contexts") or [], context_name).get("context") or {}
    cluster = _pick(data.get("clusters") or [], context.get("cluster")).get("cluster") or {}
    user = _pick(data.get("users") or [], context.get("user")).get("user") or {}
    namespace = settings["namespace"] or str(context.get("namespace") or "default")
    server = str(cluster.get("server") or "").rstrip("/")
    if not server:
        raise IntegrationError("the kubeconfig cluster has no server")

    verify: Any
    insecure = bool(cluster.get("insecure-skip-tls-verify"))
    ca_path = cluster.get("certificate-authority")
    if insecure:
        verify = False
    elif ca_path:
        verify = str((base.parent / ca_path).resolve()) if not os.path.isabs(ca_path) else ca_path
    elif cluster.get("certificate-authority-data"):
        verify = _tmp_pem(cluster["certificate-authority-data"], "ca")
    else:
        verify = True

    cert = None
    cert_path = user.get("client-certificate")
    key_path = user.get("client-key")
    cert_data = user.get("client-certificate-data")
    key_data = user.get("client-key-data")
    if cert_path and key_path:
        cert = (
            str((base.parent / cert_path).resolve()) if not os.path.isabs(cert_path) else cert_path,
            str((base.parent / key_path).resolve()) if not os.path.isabs(key_path) else key_path,
        )
    elif cert_data and key_data:
        cert = (_tmp_pem(cert_data, "crt"), _tmp_pem(key_data, "key"))

    headers = {}
    token = user.get("token")
    if not token and user.get("tokenFile"):
        token_path = Path(str(user["tokenFile"])).expanduser()
        token = token_path.read_text(encoding="utf-8").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if not token and not cert:
        raise IntegrationError(
            "this kubeconfig authenticates with an exec plugin (for example EKS/GKE); "
            "add a token or client certificate user instead"
        )
    return {"server": server, "verify": verify, "cert": cert, "headers": headers, "namespace": namespace}


def _tmp_pem(encoded: str, suffix: str) -> str:
    if not hasattr(_tmp_pem, "_dir"):
        target = Path(os.environ.get("TEMP", "/tmp")) / "deepcode-k8s"
        target.mkdir(parents=True, exist_ok=True)
        _tmp_pem._dir = target  # type: ignore[attr-defined]
    directory: Path = _tmp_pem._dir  # type: ignore[attr-defined]
    path = directory / f"{abs(hash(encoded))}.{suffix}"
    if not path.exists():
        path.write_bytes(base64.b64decode(encoded))
    return str(path)


def _client(resolved: dict[str, Any]) -> httpx.Client:
    return httpx.Client(
        base_url=resolved["server"],
        verify=resolved["verify"],
        cert=resolved["cert"],
        headers=resolved["headers"],
        timeout=TIMEOUT,
    )


def _failure(response: httpx.Response) -> IntegrationError:
    detail = f"HTTP {response.status_code}"
    try:
        body = response.json()
        message = body.get("message")
        if message:
            detail += f": {message}"
    except ValueError:
        pass
    return IntegrationError(f"Kubernetes {detail}")


def _settings(entity_id: str, home: Any) -> dict[str, str]:
    return k8s_settings(entity_id, home)


def pods(entity_id: str, home: Any = None) -> dict[str, Any]:
    settings = _settings(entity_id, home)
    resolved = _resolve_cluster(settings)
    namespace = resolved["namespace"]
    params: dict[str, str] = {}
    if settings["labelSelector"]:
        params["labelSelector"] = settings["labelSelector"]
    with _client(resolved) as client:
        response = client.get(f"/api/v1/namespaces/{namespace}/pods", params=params)
        if response.status_code >= 400:
            raise _failure(response)
        items = response.json().get("items") or []
    rows = []
    for item in items:
        status = item.get("status") or {}
        containers = status.get("containerStatuses") or []
        rows.append(
            {
                "name": item.get("metadata", {}).get("name", ""),
                "phase": status.get("phase", "Unknown"),
                "ready": sum(1 for c in containers if c.get("ready")),
                "containers": [c.get("name") for c in containers],
                "restarts": sum(int(c.get("restartCount") or 0) for c in containers),
                "startedAt": status.get("startTime"),
            }
        )
    return {"namespace": namespace, "pods": rows}


def logs(
    entity_id: str,
    pod: str,
    *,
    container: str | None = None,
    tail: int = 200,
    home: Any = None,
) -> dict[str, Any]:
    if not pod:
        raise IntegrationError("pod is required")
    settings = _settings(entity_id, home)
    resolved = _resolve_cluster(settings)
    namespace = resolved["namespace"]
    params = {"tailLines": str(max(1, min(tail, 2000)))}
    if container:
        params["container"] = container
    with _client(resolved) as client:
        response = client.get(
            f"/api/v1/namespaces/{namespace}/pods/{pod}/log", params=params
        )
        if response.status_code >= 400:
            raise _failure(response)
        text = response.text
    if len(text) > 200_000:
        text = text[-200_000:]
    return {"namespace": namespace, "pod": pod, "text": text}


def deployments(entity_id: str, home: Any = None) -> dict[str, Any]:
    settings = _settings(entity_id, home)
    resolved = _resolve_cluster(settings)
    namespace = resolved["namespace"]
    with _client(resolved) as client:
        response = client.get(f"/apis/apps/v1/namespaces/{namespace}/deployments")
        if response.status_code >= 400:
            raise _failure(response)
        items = response.json().get("items") or []
    return {
        "namespace": namespace,
        "deployments": [
            {
                "name": item.get("metadata", {}).get("name", ""),
                "ready": (item.get("status") or {}).get("readyReplicas") or 0,
                "replicas": (item.get("spec") or {}).get("replicas") or 0,
            }
            for item in items
        ],
    }


def restart(entity_id: str, home: Any = None) -> dict[str, Any]:
    settings = _settings(entity_id, home)
    deployment = settings["deployment"]
    if not deployment:
        raise IntegrationError("configure the deployment name first")
    resolved = _resolve_cluster(settings)
    namespace = resolved["namespace"]
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": _now_iso()
                    }
                }
            }
        }
    }
    with _client(resolved) as client:
        response = client.patch(
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}",
            json=body,
            headers={"Content-Type": "application/strategic-merge-patch+json"},
        )
        if response.status_code >= 400:
            raise _failure(response)
    return {"deployment": deployment, "namespace": namespace, "restarted": True}


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
