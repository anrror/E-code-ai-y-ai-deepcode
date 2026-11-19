"""Kubernetes read/act operations through the API server, no kubectl required.

The kubeconfig is parsed here (token or client-certificate users only; exec
plugins such as EKS/GKE are reported as unsupported instead of guessed at).
Cluster credentials never leave this module.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
        # Falling back to the first entry would silently target a different
        # cluster than the one the user configured.
        raise IntegrationError(f"kubeconfig has no entry named {name!r}")
    return items[0] if items else {}


def _seg(value: str) -> str:
    """One URL path segment: a pod or namespace name cannot escape the path.

    ``quote(..., safe="")`` escapes separators but leaves "." and ".." intact,
    and a segment that is exactly ".." makes the HTTP client normalise it away
    — the request then addresses a different resource. Escaping the dots keeps
    the segment literal.
    """

    text = str(value)
    if text in (".", ".."):
        return text.replace(".", "%2E")
    return quote(text, safe="")


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
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise IntegrationError(f"kubeconfig tokenFile is unreadable: {exc}") from exc
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if not token and not cert:
        raise IntegrationError(
            "this kubeconfig authenticates with an exec plugin (for example EKS/GKE); "
            "add a token or client certificate user instead"
        )
    return {"server": server, "verify": verify, "cert": cert, "headers": headers, "namespace": namespace}


def _tmp_pem(encoded: str, suffix: str) -> str:
    """Materialise kubeconfig-embedded PEM data as a private temp file.

    The name must be stable across processes (hash() of a str is salted per
    process, so the old version left a new copy of the client key on every
    launch), and the file must never be readable by other users on the box.
    """

    target = Path(os.environ.get("TEMP", "/tmp")) / "deepcode-k8s"
    target.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(encoded.encode("ascii", "replace")).hexdigest()[:16]
    path = target / f"{digest}.{suffix}"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(base64.b64decode(encoded))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
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
        response = client.get(f"/api/v1/namespaces/{_seg(namespace)}/pods", params=params)
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
            f"/api/v1/namespaces/{_seg(namespace)}/pods/{_seg(pod)}/log", params=params
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
        response = client.get(f"/apis/apps/v1/namespaces/{_seg(namespace)}/deployments")
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
            f"/apis/apps/v1/namespaces/{_seg(namespace)}/deployments/{_seg(deployment)}",
            json=body,
            headers={"Content-Type": "application/strategic-merge-patch+json"},
        )
        if response.status_code >= 400:
            raise _failure(response)
    return {"deployment": deployment, "namespace": namespace, "restarted": True}


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
