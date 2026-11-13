"""Live registry of commands the agent launched inside a Session.

The Inspector shows each command with its output and can stop it. The registry
is a process-wide singleton because the agent runtime and the app server live in
the same sidecar process; entries are keyed by an opaque id and pruned after a
bounded number of finished runs.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MAX_OUTPUT_BYTES = 256 * 1024
MAX_RETAINED = 24


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

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._entries.values())
        entries.sort(key=lambda item: item.started_at, reverse=True)
        return [_view(entry) for entry in entries]

    def read(self, process_id: str, *, offset: int = 0, limit: int = 32 * 1024) -> dict[str, Any]:
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
