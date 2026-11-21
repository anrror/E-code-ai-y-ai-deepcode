"""Jenkins REST client used by the CLI and the agent tools.

Only the endpoints DFcode needs are implemented: server info, jobs, builds,
console output, queueing a build, and stopping one. Every URL is built from the
configured controller origin; a queue URL returned by Jenkins is kept on that
origin even when the controller reports an internal hostname behind a proxy.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import os
import time
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from core.config import JenkinsConfig
from core.integrations.cicd.errors import (
    CicdAuthError,
    CicdConfigError,
    CicdNotFoundError,
    CicdRequestError,
)
from core.integrations.cicd.http import BoundedHttpClient, HttpResponse


class JenkinsClient:
    """One configured Jenkins controller."""

    def __init__(self, config: JenkinsConfig) -> None:
        self.config = config
        self._http = BoundedHttpClient(
            config.base_url,
            label="Jenkins",
            verify_tls=config.verify_tls,
            ca_file=config.ca_file,
            timeout_seconds=config.timeout_seconds,
            max_response_bytes=config.max_response_bytes,
        )

    async def aclose(self) -> None:
        """Symmetry with clients that own a session; each call opens its own."""

    # -- public operations ---------------------------------------------------

    async def info(self) -> dict[str, Any]:
        data = await self._get_json(
            "/api/json",
            params={
                "tree": "nodeName,version,numExecutors,quietingDown,useSecurity,url"
            },
        )
        return {
            "url": data.get("url") or self.config.base_url,
            "nodeName": data.get("nodeName"),
            "version": data.get("version"),
            "numExecutors": data.get("numExecutors"),
            "quietingDown": data.get("quietingDown"),
            "useSecurity": data.get("useSecurity"),
        }

    async def jobs(self) -> dict[str, Any]:
        data = await self._get_json(
            "/api/json",
            params={"tree": "jobs[name,url,color,buildable]"},
        )
        jobs = [
            {
                "name": job.get("name"),
                "url": job.get("url"),
                "color": job.get("color"),
                "buildable": job.get("buildable"),
            }
            for job in (data.get("jobs") or [])
            if isinstance(job, dict)
        ]
        jobs.sort(key=lambda job: str(job.get("name") or ""))
        return {"jobs": jobs}

    async def builds(self, job: str, limit: int = 10) -> dict[str, Any]:
        self._check_job(job)
        count = max(1, min(int(limit), 50))
        tree = (
            "builds[number,result,building,displayName,description,"
            f"timestamp,duration,url]{{0,{count}}}"
        )
        data = await self._get_json(
            f"{self._job_url(job)}/api/json",
            params={"tree": tree},
        )
        builds = [
            self._build_summary(item) for item in (data.get("builds") or [])
        ]
        return {"job": job, "builds": builds}

    async def build(self, job: str, number: int | None = None) -> dict[str, Any]:
        self._check_job(job)
        if number is None:
            url = f"{self._job_url(job)}/lastBuild/api/json"
        else:
            url = f"{self._job_url(job)}/{self._build_number(number)}/api/json"
        try:
            data = await self._get_json(url)
        except CicdNotFoundError as exc:
            if number is None:
                raise CicdNotFoundError(f"Jenkins job {job!r} has no builds yet") from exc
            raise
        summary = self._build_summary(data)
        summary.update(
            {
                "job": job,
                "fullDisplayName": data.get("fullDisplayName"),
                "estimatedDuration": data.get("estimatedDuration"),
                "artifacts": len(data.get("artifacts") or []),
            }
        )
        return summary

    async def logs(
        self,
        job: str,
        number: int | None = None,
        *,
        tail_bytes: int | None = None,
    ) -> dict[str, Any]:
        self._check_job(job)
        build_number = (
            await self._last_build_number(job) if number is None else number
        )
        build_number = self._build_number(build_number)
        console_url = (
            f"{self._job_url(job)}/{build_number}/consoleText"
        )
        limit = tail_bytes or self.config.max_log_bytes
        limit = max(1024, min(int(limit), self.config.max_log_bytes))
        headers = self._auth_headers()

        # Ask for the tail when the controller reports a length. Jenkins'
        # consoleText supports a byte offset; when the length is unknown (a
        # proxy strips it), Range is the fallback and a plain 200 is accepted
        # with an explicit truncation flag.
        length = await self._content_length(console_url, headers)
        truncated = False
        unknown_length = length is None
        if length is not None and length > limit:
            response = await self._http.request(
                "GET",
                console_url,
                params={"start": length - limit},
                headers=headers,
                max_bytes=limit,
                truncate=True,
            )
            text = response.text()
            truncated = response.truncated
        else:
            response = await self._http.request(
                "GET",
                console_url,
                headers={**headers, "Range": f"bytes=-{limit}"},
                expected_statuses=(200, 206),
                max_bytes=limit,
                truncate=True,
            )
            text = response.text()
            truncated = response.truncated or (
                unknown_length and response.status == 200
            )
        return {
            "job": job,
            "number": build_number,
            "text": text,
            "truncated": truncated,
            "bytes": len(text.encode("utf-8")),
        }

    async def trigger(
        self,
        job: str,
        *,
        parameters: dict[str, str] | None = None,
        wait: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self._check_job(job)
        values = {str(k): str(v) for k, v in (parameters or {}).items()}
        suffix = "/buildWithParameters" if values else "/build"
        data = urlencode(values).encode("utf-8") if values else None
        response = await self._post_with_crumb(
            f"{self._job_url(job)}{suffix}",
            data=data,
            content_type="application/x-www-form-urlencoded" if data else None,
        )
        if response.status not in {200, 201, 302}:
            raise CicdRequestError(
                f"Jenkins did not queue job {job!r} (HTTP {response.status})"
            )
        location = response.headers.get("Location")
        if not location:
            raise CicdRequestError(
                f"Jenkins queued job {job!r} but returned no queue location"
            )
        queue_url = self._localize(location)
        result: dict[str, Any] = {"job": job, "queueUrl": queue_url}
        if wait:
            budget = (
                float(timeout_seconds)
                if timeout_seconds is not None
                else self.config.build_queue_timeout_seconds
            )
            result["build"] = await self.wait_for_build(
                queue_url, timeout_seconds=budget
            )
        return result

    async def stop(self, job: str, number: int) -> dict[str, Any]:
        self._check_job(job)
        build_number = self._build_number(number)
        response = await self._post_with_crumb(
            f"{self._job_url(job)}/{build_number}/stop"
        )
        if response.status not in {200, 201, 302}:
            raise CicdRequestError(
                f"Jenkins did not stop {job}#{build_number} "
                f"(HTTP {response.status})"
            )
        return {"job": job, "number": build_number, "stopped": True}

    async def queue_item(self, queue_url: str) -> dict[str, Any]:
        self._http._check_url(queue_url)
        data = await self._get_json(
            queue_url,
            params={"tree": "executable[number,url],cancelled,why"},
        )
        return {
            "queueUrl": queue_url,
            "cancelled": bool(data.get("cancelled")),
            "why": data.get("why"),
            "executable": data.get("executable"),
        }

    async def wait_for_build(
        self,
        queue_url: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self._http._check_url(queue_url)
        deadline = time.monotonic() + timeout_seconds
        while True:
            item = await self.queue_item(queue_url)
            if item["cancelled"]:
                raise CicdRequestError(
                    f"Jenkins cancelled the queued build: {item.get('why') or 'no reason given'}"
                )
            executable = item.get("executable")
            if isinstance(executable, dict) and executable.get("number") is not None:
                return {
                    "number": executable.get("number"),
                    "url": executable.get("url"),
                }
            if time.monotonic() >= deadline:
                raise CicdRequestError(
                    "timed out waiting for Jenkins to start the build"
                )
            await asyncio.sleep(2.0)

    # -- request plumbing ----------------------------------------------------

    def _check_job(self, job: str) -> None:
        name = job.strip("/")
        if not name:
            raise CicdConfigError("a Jenkins job name is required")
        segments = name.split("/")
        if any(
            segment in {"", ".", ".."}
            or any(character.isspace() or ord(character) < 32 for character in segment)
            for segment in segments
        ):
            raise CicdConfigError(f"invalid Jenkins job name: {job!r}")
        allowed = self.config.allowed_jobs
        if allowed and not any(
            fnmatch.fnmatchcase(name, pattern)
            or fnmatch.fnmatchcase(f"/{name}", pattern)
            for pattern in allowed
        ):
            raise CicdConfigError(
                f"Jenkins job {job!r} is outside cicd.jenkins.allowedJobs"
            )

    def _job_url(self, job: str) -> str:
        segments = job.strip("/").split("/")
        return self.config.base_url + "".join(
            f"/job/{quote(segment, safe='')}" for segment in segments
        )

    @staticmethod
    def _build_number(number: Any) -> int:
        try:
            value = int(number)
        except (TypeError, ValueError) as exc:
            raise CicdConfigError("a Jenkins build number must be an integer") from exc
        if value < 1:
            raise CicdConfigError("a Jenkins build number must be positive")
        return value

    @staticmethod
    def _build_summary(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "number": data.get("number"),
            "result": data.get("result"),
            "building": data.get("building"),
            "displayName": data.get("displayName"),
            "description": data.get("description"),
            "timestamp": data.get("timestamp"),
            "duration": data.get("duration"),
            "url": data.get("url"),
        }

    def _auth_headers(self) -> dict[str, str]:
        env_name = self.config.api_token_env
        if not env_name:
            return {}
        token = os.environ.get(env_name)
        if not token:
            # Anonymous access is still useful for read-only controllers; the
            # remote system produces the authoritative denial for mutations.
            return {}
        if self.config.auth == "bearer":
            return {"Authorization": f"Bearer {token}"}
        if not self.config.username:
            raise CicdConfigError(
                "cicd.jenkins.username is required for basic authentication"
            )
        raw = f"{self.config.username}:{token}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        path = url if url.startswith(("http://", "https://")) else self._url(url)
        response = await self._http.request(
            "GET",
            path,
            params=params,
            headers=self._auth_headers(),
            expected_statuses=(200,),
        )
        data = response.json()
        if not isinstance(data, dict):
            raise CicdRequestError("Jenkins returned an unexpected JSON document")
        return data

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    async def _content_length(
        self, url: str, headers: dict[str, str]
    ) -> int | None:
        try:
            response = await self._http.request(
                "HEAD",
                url,
                headers=headers,
                expected_statuses=(200, 405),
                max_bytes=1024,
            )
        except CicdNotFoundError:
            raise
        raw = response.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    async def _post_with_crumb(
        self,
        url: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpResponse:
        headers = self._auth_headers()
        for attempt in range(2):
            async with self._http.session() as session:
                crumb = await self._fetch_crumb(session)
                try:
                    return await self._http.request(
                        "POST",
                        url,
                        headers={**headers, **crumb},
                        data=data,
                        content_type=content_type,
                        expected_statuses=(200, 201, 302),
                        session=session,
                    )
                except CicdAuthError:
                    # A Jenkins CSRF rejection is a 403 too. One fresh crumb on
                    # a fresh session distinguishes it from a bad credential;
                    # a second rejection is the real failure.
                    if attempt == 1:
                        raise
        raise CicdRequestError("Jenkins did not accept the request")

    async def _fetch_crumb(
        self, session: Any
    ) -> dict[str, str]:
        try:
            response = await self._http.request(
                "GET",
                self._url("/crumbIssuer/api/json"),
                headers=self._auth_headers(),
                expected_statuses=(200, 404),
                session=session,
            )
        except CicdNotFoundError:
            return {}
        if response.status == 404:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            return {}
        field = data.get("crumbRequestField")
        crumb = data.get("crumb")
        if not field or not crumb:
            return {}
        return {str(field): str(crumb)}

    def _localize(self, location: str) -> str:
        """Keep a controller-reported URL on the configured origin.

        A Jenkins behind a reverse proxy often advertises its internal
        hostname. Following that host would either fail or, worse, send the
        API token somewhere else; the path is still authoritative.
        """

        parsed = urlsplit(location)
        if not parsed.scheme and not parsed.netloc:
            return urljoin(self.config.base_url + "/", location)
        scheme, host, port = self._http.origin
        default_port = 443 if scheme == "https" else 80
        netloc = host if port == default_port else f"{host}:{port}"
        return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


__all__ = ["JenkinsClient"]
