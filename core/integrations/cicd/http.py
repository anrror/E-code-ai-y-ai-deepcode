"""Bounded HTTP transport for CI/CD APIs.

The Jenkins controller and the Kubernetes API normally live on a private
network, so the public-address resolver in :mod:`core.network.safe_http` is not
applicable. What *is* applicable is every other boundary: the configured origin
is fixed, redirects are never followed silently, TLS verification is on unless
explicitly disabled, responses are size-bounded, and credentials never appear
in an error message.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from core.integrations.cicd.errors import (
    CicdAuthError,
    CicdError,
    CicdNotFoundError,
    CicdRequestError,
    CicdResponseTooLargeError,
)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    truncated: bool = False

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text())
        except (TypeError, ValueError) as exc:
            raise CicdRequestError("remote server returned invalid JSON") from exc


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    return (
        scheme,
        (parsed.hostname or "").lower(),
        parsed.port or (443 if scheme == "https" else 80),
    )


def _short_body(body: bytes) -> str:
    """A bounded, credential-free hint from an error response."""

    text = body.decode("utf-8", errors="replace").strip()
    text = " ".join(text.split())
    return text[:300]


class BoundedHttpClient:
    """Small async HTTP client with a fixed origin and hard response limits."""

    def __init__(
        self,
        base_url: str,
        *,
        label: str,
        verify_tls: bool = True,
        ca_file: str | None = None,
        ca_data: str | None = None,
        client_cert_file: str | None = None,
        client_key_file: str | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self.base_url = base_url
        self.label = label
        self.origin = _origin(base_url)
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._ssl = self._build_ssl(
            verify_tls=verify_tls,
            ca_file=ca_file,
            ca_data=ca_data,
            client_cert_file=client_cert_file,
            client_key_file=client_key_file,
        )

    @staticmethod
    def _build_ssl(
        *,
        verify_tls: bool,
        ca_file: str | None,
        ca_data: str | None,
        client_cert_file: str | None,
        client_key_file: str | None,
    ) -> ssl.SSLContext | bool:
        if not verify_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            context = ssl.create_default_context(cafile=ca_file)
            if ca_data:
                context.load_verify_locations(cadata=ca_data)
        if client_cert_file and client_key_file:
            context.load_cert_chain(client_cert_file, client_key_file)
        return context

    def _check_url(self, url: str) -> None:
        if _origin(url) != self.origin:
            raise CicdRequestError(
                f"{self.label} refused a request outside its configured origin"
            )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiohttp.ClientSession]:
        connector = aiohttp.TCPConnector(ssl=self._ssl)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
            # Jenkins binds a CSRF crumb to its web session, and a controller
            # addressed by IP would be rejected by the default cookie policy.
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        ) as session:
            yield session

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        data: bytes | str | None = None,
        content_type: str | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        max_bytes: int | None = None,
        truncate: bool = False,
        session: aiohttp.ClientSession | None = None,
    ) -> HttpResponse:
        """Issue one request, never following a redirect.

        Returning the response for an unexpected status would push the same
        status handling into every caller, so failures are translated here.
        """

        self._check_url(url)
        limit = self.max_response_bytes if max_bytes is None else max_bytes
        if limit < 1:
            raise ValueError("max_bytes must be positive")

        request_headers = dict(headers or {})
        payload: bytes | None = None
        if json_body is not None:
            payload = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            content_type = content_type or "application/json"
        elif data is not None:
            payload = data.encode("utf-8") if isinstance(data, str) else data
        if content_type:
            request_headers.setdefault("Content-Type", content_type)

        if session is None:
            async with self.session() as owned:
                response = await self._send(
                    owned,
                    method,
                    url,
                    params=params,
                    headers=request_headers,
                    payload=payload,
                    limit=limit,
                    truncate=truncate,
                )
        else:
            response = await self._send(
                session,
                method,
                url,
                params=params,
                headers=request_headers,
                payload=payload,
                limit=limit,
                truncate=truncate,
            )

        if response.status not in expected_statuses:
            self._raise_for_status(response)
        return response

    async def _send(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None,
        headers: Mapping[str, str],
        payload: bytes | None,
        limit: int,
        truncate: bool,
    ) -> HttpResponse:
        try:
            async with session.request(
                method,
                url,
                params=params,
                headers=headers,
                data=payload,
                allow_redirects=False,
            ) as raw:
                body, truncated = await self._read_body(
                    raw, limit=limit, truncate=truncate
                )
                return HttpResponse(
                    status=raw.status,
                    headers={str(k): str(v) for k, v in raw.headers.items()},
                    body=body,
                    url=str(raw.url),
                    truncated=truncated,
                )
        except CicdError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise CicdRequestError(
                f"{self.label} request failed; check the endpoint and network"
            ) from exc

    @staticmethod
    async def _read_body(
        response: aiohttp.ClientResponse,
        *,
        limit: int,
        truncate: bool,
    ) -> tuple[bytes, bool]:
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            body.extend(chunk)
            if len(body) > limit:
                if truncate:
                    del body[limit:]
                    return bytes(body), True
                raise CicdResponseTooLargeError(
                    f"response exceeds the configured {limit}-byte limit"
                )
        return bytes(body), False

    def _raise_for_status(self, response: HttpResponse) -> None:
        detail = _short_body(response.body)
        suffix = f": {detail}" if detail else ""
        if response.status in {401, 403}:
            raise CicdAuthError(
                f"{self.label} rejected the configured credential "
                f"(HTTP {response.status}){suffix}"
            )
        if response.status == 404:
            raise CicdNotFoundError(
                f"{self.label} resource was not found (HTTP 404){suffix}"
            )
        raise CicdRequestError(
            f"{self.label} returned HTTP {response.status}{suffix}"
        )


__all__ = ["BoundedHttpClient", "HttpResponse"]
