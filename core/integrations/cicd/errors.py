"""Stable errors for the CI/CD integration.

Every transport, protocol and configuration failure becomes one of these so a
tool result can explain what happened without a traceback, and the CLI can map
it to a non-zero exit code without guessing exception types.
"""

from __future__ import annotations


class CicdError(RuntimeError):
    """Base class: a CI/CD operation could not be completed."""


class CicdConfigError(CicdError):
    """The ``cicd`` block is absent or incomplete for this operation."""


class CicdAuthError(CicdError):
    """The remote system rejected the configured credential."""


class CicdNotFoundError(CicdError):
    """The requested job, build, namespace, workload or pod does not exist."""


class CicdRequestError(CicdError):
    """The remote system rejected or failed the request."""


class CicdResponseTooLargeError(CicdRequestError):
    """A response exceeded the configured byte boundary."""


__all__ = [
    "CicdAuthError",
    "CicdConfigError",
    "CicdError",
    "CicdNotFoundError",
    "CicdRequestError",
    "CicdResponseTooLargeError",
]
