"""A small Jenkins client: status, trigger with parameters, console text."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from core.team_context.integrations import IntegrationError, jenkins_settings

TIMEOUT = 20.0
JOB_QUOTE = quote


def _client(settings: dict[str, str]) -> httpx.Client:
    auth = None
    if settings["user"]:
        auth = (settings["user"], settings["token"])
    return httpx.Client(
        base_url=settings["url"],
        auth=auth,
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"Accept": "application/json"},
    )


def _job_path(job: str) -> str:
    parts = [part for part in job.strip("/").split("/") if part]
    return "/" + "/".join(f"job/{JOB_QUOTE(part, safe='')}" for part in parts)


def _error(response: httpx.Response) -> IntegrationError:
    detail = f"HTTP {response.status_code}"
    if response.status_code == 401:
        detail += " (Jenkins rejected the credentials)"
    elif response.status_code == 404:
        detail += " (job not found)"
    return IntegrationError(f"Jenkins {detail}: {response.text[:200]}")


def _crumb(client: httpx.Client) -> dict[str, str]:
    try:
        response = client.get("/crumbIssuer/api/json")
    except httpx.HTTPError:
        return {}
    if response.status_code != 200:
        return {}
    payload = response.json()
    field = payload.get("crumbRequestField")
    crumb = payload.get("crumb")
    return {field: crumb} if field and crumb else {}


def status(entity_id: str, home: Any = None) -> dict[str, Any]:
    settings = jenkins_settings(entity_id, home)
    with _client(settings) as client:
        response = client.get(f"{_job_path(settings['job'])}/api/json")
        if response.status_code >= 400:
            raise _error(response)
        payload = response.json()
    last = payload.get("lastBuild") or {}
    build_number = last.get("number")
    build_info: dict[str, Any] = {}
    if build_number:
        with _client(settings) as client:
            info = client.get(
                f"{_job_path(settings['job'])}/{build_number}/api/json"
            )
            if info.status_code < 400:
                build_info = info.json()
    return {
        "job": payload.get("displayName") or settings["job"],
        "url": payload.get("url"),
        "building": bool(payload.get("inQueue")) or bool(build_info.get("building")),
        "lastBuild": {
            "number": build_number,
            "result": build_info.get("result"),
            "timestamp": build_info.get("timestamp"),
            "duration": build_info.get("duration"),
        },
    }


def trigger(entity_id: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    settings = jenkins_settings(entity_id, home=None)
    params = {key: str(value) for key, value in (params or {}).items()}
    with _client(settings) as client:
        crumb = _crumb(client)
        path = f"{_job_path(settings['job'])}/build"
        if params:
            response = client.post(
                f"{_job_path(settings['job'])}/buildWithParameters",
                data=params,
                headers=crumb,
            )
        else:
            response = client.post(path, headers=crumb)
        if response.status_code >= 400:
            raise _error(response)
        location = response.headers.get("Location", "")
    return {
        "queued": True,
        "queueUrl": location,
        "buildNumber": _build_from_location(location),
    }


def _build_from_location(location: str) -> int | None:
    marker = "/job/"
    if marker not in location:
        return None
    tail = location.rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def console(entity_id: str, build: int | None = None) -> dict[str, Any]:
    settings = jenkins_settings(entity_id, home=None)
    number = build
    if number is None:
        info = status(entity_id)
        number = info["lastBuild"]["number"]
    if not number:
        raise IntegrationError("this job has no build yet")
    with _client(settings) as client:
        response = client.get(f"{_job_path(settings['job'])}/{number}/consoleText")
        if response.status_code >= 400:
            raise _error(response)
        text = response.text
    if len(text) > 200_000:
        text = text[-200_000:]
    return {"build": number, "text": text}
