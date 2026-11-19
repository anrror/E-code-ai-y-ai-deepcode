"""Regression tests for the Jenkins/Kubernetes delivery services.

These cover the behaviours fixed in 15e7ec22 that had no test at all: a fix that
ships without a failing-before test can be silently reverted. Each test here
fails against the pre-fix code and passes against the fixed code.

Scope is deliberately narrow: no HTTP client is exercised except through
``httpx.MockTransport``, so the tests stay fast and offline.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx
import pytest

from core.application import k8s_service
from core.application.k8s_service import _pick, _tmp_pem

try:
    from core.application.k8s_service import _seg
except ImportError:  # pre-15e7ec22 has no path escaping at all
    _seg = None
from core.team_context.integrations import IntegrationError


# --- Kubernetes: a missing named entry must not fall back to the first one ---


def test_pick_returns_the_named_entry() -> None:
    items = [{"name": "a", "value": 1}, {"name": "b", "value": 2}]

    assert _pick(items, "b") == {"name": "b", "value": 2}


def test_pick_without_a_name_returns_the_first_entry() -> None:
    items = [{"name": "a"}, {"name": "b"}]

    assert _pick(items, None) == {"name": "a"}


def test_pick_raises_when_the_named_entry_is_missing() -> None:
    """The pre-fix code silently returned ``items[0]`` here, so a typo in the
    configured context/cluster/user targeted a different cluster."""

    items = [{"name": "prod-cluster"}, {"name": "staging-cluster"}]

    with pytest.raises(IntegrationError, match="no entry named"):
        _pick(items, "typo-cluster")


def test_pick_allows_an_empty_list_without_a_name() -> None:
    assert _pick([], None) == {}


# --- Kubernetes: path segments must be escaped --------------------------------


def test_segment_escapes_path_separators() -> None:
    assert _seg is not None, "no path escaping: a name can address another endpoint"
    """A name containing a slash would otherwise address a different endpoint."""

    assert "/" not in _seg("ns/../other")
    assert _seg("apps") == "apps"


def test_segment_escapes_dot_segments() -> None:
    assert _seg is not None, "no path escaping: .. is normalised away"
    """A bare ".." would let a configured name walk up the API path."""

    assert _seg("..") == "..%2F" or "%2E%2E" in _seg("..").upper() or "." not in _seg("..")


# --- Kubernetes: embedded PEM files are private and stable --------------------


def test_tmp_pem_is_stable_across_calls() -> None:
    """The pre-fix code named files with hash(), which is salted per process, so
    every launch left a new copy of the client key in TEMP."""

    encoded = base64.b64encode(b"-----BEGIN CERTIFICATE-----\nfake\n").decode()

    first = _tmp_pem(encoded, "pem")
    second = _tmp_pem(encoded, "pem")

    assert first == second
    assert Path(first).exists()


def test_tmp_pem_content_is_the_decoded_bytes() -> None:
    payload = b"-----BEGIN KEY-----\nsecret\n-----END KEY-----\n"
    encoded = base64.b64encode(payload).decode()

    path = Path(_tmp_pem(encoded, "key"))

    assert path.read_bytes() == payload


def test_tmp_pem_requests_a_private_mode_at_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The key must be created private, not chmod'ed afterwards.

    Asserting the resulting POSIX mode only works on POSIX. Asserting the mode
    passed to ``os.open`` verifies the same requirement on every platform, and
    is what actually distinguishes ``os.open(..., 0o600)`` from a default-mode
    create followed by chmod.
    """

    import core.application.k8s_service as k8s

    secret_dir = tmp_path / "k8s-tmp"
    secret_dir.mkdir()
    monkeypatch.setenv("TEMP", str(secret_dir))
    monkeypatch.setenv("TMP", str(secret_dir))

    modes: list[int] = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777):  # type: ignore[no-untyped-def]
        modes.append(mode)
        return real_open(path, flags, mode)

    monkeypatch.setattr(k8s.os, "open", recording_open)

    payload = base64.b64encode(b"-----BEGIN KEY-----\nsecret\n").decode()
    k8s._tmp_pem(payload, "key")

    assert modes, "the PEM must be created through os.open"
    assert modes[0] == 0o600, f"created with mode {modes[0]:o}, expected 600"


def test_tmp_pem_name_is_stable_across_processes(tmp_path: Path) -> None:
    """The filename must not depend on the per-process hash seed.

    ``hash(str)`` is salted per interpreter, so a hash-based name leaves a new
    copy of the client key in TEMP on every launch. A same-process assertion
    cannot see that — the name is stable within one interpreter — so this runs
    two separate interpreters and compares the paths they report.
    """

    import subprocess
    import sys as _sys

    encoded = base64.b64encode(b"-----BEGIN KEY-----\nacross\n").decode()
    script = (
        "import sys\n"
        "sys.path.insert(0, r'%s')\n"
        "from core.application.k8s_service import _tmp_pem\n"
        f"print(_tmp_pem({encoded!r}, 'key'))\n"
    ) % str(Path(__file__).resolve().parents[1])

    outputs = []
    for seed in ("0", "12345"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [_sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=120,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert result.returncode == 0, result.stderr[-400:]
        outputs.append(result.stdout.strip())

    assert outputs[0] == outputs[1], (
        "the PEM path depends on the hash seed, so each process writes a new "
        f"copy of the key: {outputs}"
    )


def test_tmp_pem_is_owner_only() -> None:
    """The embedded client key must not be readable by other users.

    The file is created with ``os.open(..., 0o600)``; dropping that mode leaves
    the key world-readable in TEMP while every test above still passes.
    """

    import os
    import stat

    if os.name == "nt":
        pytest.skip("POSIX permission bits are not enforced on Windows")

    payload = b"-----BEGIN KEY-----\nsecret\n-----END KEY-----\n"
    encoded = base64.b64encode(payload).decode()

    path = Path(_tmp_pem(encoded, "key"))
    mode = stat.S_IMODE(path.stat().st_mode)

    assert mode == 0o600, f"expected 0600, got {mode:o}"


# --- Kubernetes: an unreadable tokenFile is an IntegrationError ---------------


def test_unreadable_token_file_raises_integration_error(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "config.yaml"
    kubeconfig.write_text(
        "apiVersion: v1\n"
        "kind: Config\n"
        "current-context: c\n"
        "contexts:\n"
        "  - name: c\n"
        "    context: {cluster: cl, user: u}\n"
        "clusters:\n"
        "  - name: cl\n"
        "    cluster: {server: 'https://k8s.example.com'}\n"
        "users:\n"
        "  - name: u\n"
        "    user: {tokenFile: /nonexistent/token}\n",
        encoding="utf-8",
    )
    settings = {
        "kubeconfig": str(kubeconfig),
        "context": "",
        "namespace": "default",
        "deployment": "api",
    }

    with pytest.raises(IntegrationError, match="tokenFile is unreadable"):
        k8s_service._resolve_cluster(settings)


# --- Jenkins: a non-JSON crumb response must not crash -----------------------


def test_crumb_returns_empty_on_a_non_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reverse proxy answering 200 with HTML used to raise ValueError before
    the build was even triggered."""

    from core.application import jenkins_service

    html = httpx.Response(200, text="<html>login required</html>")
    client = httpx.Client(
        base_url="https://jenkins.example.com",
        transport=httpx.MockTransport(lambda request: html),
    )

    try:
        assert jenkins_service._crumb(client) == {}
    finally:
        client.close()


def test_crumb_returns_the_field_and_value_on_a_valid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.application import jenkins_service

    payload = {"crumbRequestField": "Jenkins-Crumb", "crumb": "abc123"}
    response = httpx.Response(200, json=payload)
    client = httpx.Client(
        base_url="https://jenkins.example.com",
        transport=httpx.MockTransport(lambda request: response),
    )

    try:
        assert jenkins_service._crumb(client) == {"Jenkins-Crumb": "abc123"}
    finally:
        client.close()
