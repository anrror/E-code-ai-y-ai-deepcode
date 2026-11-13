"""Live registry of commands the agent launched inside a Session.

The Inspector shows each command with its output and can stop it. The registry
is a process-wide singleton because the agent runtime and the app server live in
the same sidecar process; entries are keyed by an opaque id and pruned after a
bounded number of finished runs.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_OUTPUT_BYTES = 256 * 1024
MAX_RETAINED = 24
_OWN_NAMES = {"deepcode-desktop.exe", "deepcode-app-server.exe"}
_EXTERNAL_TTL = 5.0
_LOG_TTL = 2.0
_external_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_external_scanning: set[str] = set()
_log_cache: dict[str, tuple[float, str, str]] = {}
_DEV_SERVER_NAMES = {
    "go.exe",
    "node.exe",
    "python.exe",
    "pythonw.exe",
    "java.exe",
    "dotnet.exe",
    "cargo.exe",
    "deno.exe",
    "bun.exe",
}


def _psutil():  # type: ignore[no-untyped-def]
    try:
        import psutil
    except ImportError:  # pragma: no cover - packaged builds ship psutil
        return None
    return psutil


@dataclass(slots=True)
class ProcessEntry:
    id: str
    command: str
    workspace: str
    pid: int | None
    started_at: float
    status: str = "running"
    exit_code: int | None = None
    ended_at: float | None = None
    output: bytearray = field(default_factory=bytearray)
    output_offset: int = 0
    stop: Callable[[], None] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def head(self) -> int:
        return self.output_offset + len(self.output)


class ProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, ProcessEntry] = {}
        self._finished: OrderedDict[str, None] = OrderedDict()

    def register(
        self,
        command: str,
        workspace: str,
        *,
        pid: int | None = None,
        stop: Callable[[], None] | None = None,
    ) -> ProcessEntry:
        entry = ProcessEntry(
            id=f"proc_{uuid.uuid4().hex}",
            command=command,
            workspace=workspace,
            pid=pid,
            started_at=time.time(),
            stop=stop,
        )
        with self._lock:
            self._entries[entry.id] = entry
        return entry

    def append(self, entry: ProcessEntry, chunk: bytes) -> None:
        if not chunk:
            return
        with entry.lock:
            entry.output.extend(chunk)
            trim = max(0, len(entry.output) - MAX_OUTPUT_BYTES)
            while trim < len(entry.output) and entry.output[trim] & 0xC0 == 0x80:
                trim += 1
            if trim:
                del entry.output[:trim]
                entry.output_offset += trim

    def finish(self, entry: ProcessEntry, exit_code: int | None, status: str = "exited") -> None:
        with self._lock:
            entry.status = status
            entry.exit_code = exit_code
            entry.ended_at = time.time()
            entry.stop = None
            self._finished[entry.id] = None
            while len(self._finished) > MAX_RETAINED:
                oldest, _ = self._finished.popitem(last=False)
                self._entries.pop(oldest, None)

    def list(self, workspace: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._entries.values())
        entries.sort(key=lambda item: item.started_at, reverse=True)
        views = [_view(entry) for entry in entries]
        if workspace:
            known = {entry.pid for entry in entries}
            views.extend(external_processes(workspace, known))
            views.sort(key=lambda item: item["startedAt"], reverse=True)
        return views

    def read(self, process_id: str, *, offset: int = 0, limit: int = 32 * 1024) -> dict[str, Any]:
        if process_id.startswith("os:"):
            return _read_external(process_id, offset=offset, limit=limit)
        with self._lock:
            entry = self._entries.get(process_id)
        if entry is None:
            raise KeyError(process_id)
        with entry.lock:
            head = entry.head
            start = max(offset, entry.output_offset)
            relative = start - entry.output_offset
            raw = bytes(entry.output[relative : relative + min(limit, head - start)])
        return {
            "process": _view(entry),
            "data": raw.decode("utf-8", errors="replace"),
            "offset": start,
            "nextOffset": start + len(raw),
            "availableFrom": entry.output_offset,
            "headOffset": head,
            "hasMore": start + len(raw) < head,
        }

    def stop(self, process_id: str) -> bool:
        if process_id.startswith("os:"):
            return _stop_external(process_id[3:])
        with self._lock:
            entry = self._entries.get(process_id)
        if entry is None or entry.stop is None:
            return False
        try:
            entry.stop()
        except Exception:  # noqa: BLE001 - stopping a dying process must not raise
            return False
        return True


def _view(entry: ProcessEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "command": entry.command,
        "workspace": entry.workspace,
        "pid": entry.pid,
        "status": entry.status,
        "exitCode": entry.exit_code,
        "startedAt": entry.started_at,
        "endedAt": entry.ended_at,
    }


registry = ProcessRegistry()


def external_processes(
    workspace: str, known_pids: set[int | None]
) -> list[dict[str, Any]]:
    """Processes already running in a workspace, started outside this Session.

    A server the user launched by hand (``judicial.exe``, ``go run``, a dev
    server) belongs to the same project even though the agent never spawned it.
    The scan walks every process and runs on a background thread; callers get
    the last snapshot immediately and never block an RPC.
    """

    psutil = _psutil()
    if psutil is None:
        return []
    cached = _external_cache.get(workspace)
    now = time.monotonic()
    if workspace not in _external_scanning and (
        cached is None or now - cached[0] >= _EXTERNAL_TTL
    ):
        _external_scanning.add(workspace)
        threading.Thread(
            target=_scan_external,
            args=(workspace, known_pids, psutil),
            name="deepcode-process-scan",
            daemon=True,
        ).start()
    return [dict(item) for item in (cached[1] if cached else [])]


def _scan_external(
    workspace: str, known_pids: set[int | None], psutil: Any
) -> None:
    rows: list[dict[str, Any]] = []
    try:
        root = os.path.normcase(os.path.abspath(workspace)).rstrip("\\/")
        own_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            info = proc.info
            pid = info.get("pid")
            if pid is None or pid == own_pid or pid in known_pids:
                continue
            name = (info.get("name") or "").lower()
            if name in _OWN_NAMES:
                continue
            arguments = [str(part) for part in (info.get("cmdline") or [])]
            arg_match = any(
                os.path.isabs(argument)
                and os.path.normcase(argument).startswith(root)
                for argument in arguments
            )
            cwd_match = False
            cwd = ""
            if not arg_match and name in _DEV_SERVER_NAMES:
                try:
                    cwd = proc.cwd()
                except Exception:  # noqa: BLE001 - another user's process
                    cwd = ""
                cwd_match = bool(cwd) and os.path.normcase(cwd).startswith(root)
            if not (arg_match or cwd_match):
                continue
            command = " ".join(arguments).strip() or name or f"pid {pid}"
            rows.append(
                {
                    "id": f"os:{pid}",
                    "command": command[:400],
                    "workspace": cwd or workspace,
                    "pid": pid,
                    "status": "running",
                    "exitCode": None,
                    "startedAt": float(info.get("create_time") or time.time()),
                    "endedAt": None,
                }
            )
    finally:
        _external_cache[workspace] = (time.monotonic(), rows)
        _external_scanning.discard(workspace)


def _read_external(
    process_id: str, *, offset: int, limit: int
) -> dict[str, Any]:
    """Output of a process this Session did not spawn.

    The OS gives no handle on another process's stdout, so the best available
    evidence is the newest log file the project wrote. When there is none the
    caller gets an empty window plus ``external`` so the UI can say why.
    """

    psutil = _psutil()
    raw_pid = process_id[3:]
    workspace = ""
    started = time.time()
    if psutil is not None:
        try:
            process = psutil.Process(int(raw_pid))
            workspace = process.cwd()
            started = process.create_time()
        except Exception:  # noqa: BLE001 - the process may already be gone
            workspace = ""
    log_path, data = _newest_log_tail(workspace, limit)
    view = {
        "id": process_id,
        "command": f"pid {raw_pid}",
        "workspace": workspace,
        "pid": int(raw_pid) if raw_pid.isdigit() else None,
        "status": "running",
        "exitCode": None,
        "startedAt": started,
        "endedAt": None,
    }
    return {
        "process": view,
        "data": data,
        "offset": max(0, offset),
        "nextOffset": max(0, offset) + len(data.encode("utf-8")),
        "availableFrom": 0,
        "headOffset": len(data.encode("utf-8")),
        "hasMore": False,
        "external": True,
        "source": f"logfile:{log_path}" if log_path else "",
    }


def _newest_log_tail(workspace: str, limit: int) -> tuple[str, str]:
    if not workspace or not os.path.isdir(workspace):
        return "", ""
    cached = _log_cache.get(workspace)
    if cached is not None and time.monotonic() - cached[0] < _LOG_TTL:
        return cached[1], cached[2]
    newest: tuple[float, str] | None = None
    root = Path(workspace)
    visited = 0
    for directory, _, files in os.walk(root):
        visited += 1
        if visited > 400 or len(Path(directory).relative_to(root).parts) > 2:
            continue
        for name in files:
            if not name.lower().endswith((".log", ".out", ".err")):
                continue
            candidate = os.path.join(directory, name)
            try:
                modified = os.path.getmtime(candidate)
                size = os.path.getsize(candidate)
            except OSError:
                continue
            if size == 0 or size > 8 * 1024 * 1024:
                continue
            if newest is None or modified > newest[0]:
                newest = (modified, candidate)
    if newest is None:
        _log_cache[workspace] = (time.monotonic(), "", "")
        return "", ""
    path = newest[1]
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            payload = handle.read(limit)
    except OSError:
        return "", ""
    text = payload.decode("utf-8", errors="replace")
    _log_cache[workspace] = (time.monotonic(), path, text)
    return path, text


def _stop_external(raw_pid: str) -> bool:
    psutil = _psutil()
    try:
        pid = int(raw_pid)
    except ValueError:
        return False
    if psutil is None:
        return False
    try:
        process = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    try:
        for child in process.children(recursive=True):
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        process.terminate()
        process.wait(timeout=2)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    except psutil.TimeoutExpired:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    return True
