"""AgentControl — the coordination state for model-driven delegation (C2).

Faithful to the reference agent's multi-agent design, adapted to DeepCode:

- **spawn is non-blocking** — it starts a sub-agent as a background task and
  returns its id immediately; several run *concurrently*, bounded by a per-session
  limit (``max_threads``);
- **results flow through the active Turn inbox** — application-backed sessions
  use the same atomic input boundary as user steering; direct CLI sessions use
  a local compatibility mailbox owned by that Agent runtime;
- **wait_agent parks on mailbox activity** (:meth:`wait_for_activity`) rather
  than joining a future — so the parent can keep working and collect results as
  they arrive.

Isolation is DeepCode's own guarantee: each sub-agent may run in its own git
worktree whose result is merged back with 3-way-merge conflict detection; base
git ops are serialised (``_git_lock``) while the sub-agents build in parallel.

Depth is capped at one: a sub-agent session is built with ``allow_spawn=False``
so it gets no delegation tools and cannot recurse.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from core.agent_runtime.injections import SubagentMessage, TurnInputSink
from core.harness.permissions import PermissionMode

if TYPE_CHECKING:
    from core.compat.runtime import DeepCodeRuntime
    from core.domain.execution_profile import ExecutionProfile
    from core.domain.execution_security import ExecutionSecurityProfile

# Max sub-agents running at once. A small fan-out (3-5 independent subtasks) is
# the common case, so the default fits it without forcing a wait-and-retry,
# while still bounding concurrent model sessions.
MAX_CONCURRENT_SUBAGENTS = 5


def _slug(name: str) -> str:
    """A stable id/dedup key from a subtask name (like the reference task_name)."""
    return re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-") or "agent"


_RUNNING = "running"
# A native sub-agent that completed (or was softly interrupted in) a turn
# parks here alive: its conversation continues on the next send_message.
# External-CLI children never reach it — they answer once and are done.
_IDLE = "idle"
_DONE = "done"
_FAILED = "failed"
# A dependency-ordered child waits here without a task handle. It is *not*
# counted against max_threads until it actually starts, so a fan-out graph can
# be declared in one step without bumping into the fan-out limit.
_QUEUED = "queued"
# A dependency failed or was cancelled before this child could start. Blocked is
# deliberately distinct from failed: the child did no work, so it is not the
# child's error, and the parent is told which dependency broke it.
_BLOCKED = "blocked"
_CANCELLED = "cancelled"
_TERMINAL = frozenset({_DONE, _FAILED, _CANCELLED})
# A dependency is satisfied once the child has produced a result — for a
# conversational native child that is "idle after its first turn", not "exited".
_SATISFIED = frozenset({_DONE, _IDLE})


class AgentLimitError(RuntimeError):
    """Too many sub-agents are already running concurrently."""


class DuplicateAgentError(AgentLimitError):
    """A sub-agent is already running this exact task (a re-spawn is redundant)."""


class UnknownCategoryError(AgentLimitError):
    """A spawn named a routing category the session does not define.

    Subclasses :class:`AgentLimitError` on purpose: the delegation tools already
    translate that type into a model-facing error string, and a silently ignored
    category would put the child on the wrong (usually more expensive) model.
    """


# Sub-agent execution backends. "native" builds an in-process AgentSession;
# the rest delegate the task to an external CLI (see external_backend).
# External backends are separate products with their own credentials and
# policy: they take a self-contained text task plus the workspace cwd and
# return only the final answer — no parent context, no send_message channel.
# Requests that need either are rejected loud at spawn time, never
# accepted-then-ignored.
NATIVE_BACKEND = "native"


@dataclass
class SubAgent:
    id: str
    task: str
    isolate: bool = True
    backend: str = NATIVE_BACKEND
    # Optional per-child composition (native backend only): an extra
    # system-prompt section, a narrowing tool allowlist, and an object-rooted
    # JSON Schema the child must satisfy through the forced capture tool.
    persona: str | None = None
    tool_names: tuple[str, ...] | None = None
    output_schema: dict | None = field(default=None, repr=False)
    # Routing target resolved at spawn time (None = inherit the parent's).
    # ``category`` is kept for the transcript; model/connection are the
    # effective overrides the child profile is built from.
    category: str | None = None
    model: str | None = None
    connection_id: str | None = None
    # Ids of sub-agents that must reach a satisfied state before this one runs.
    deps: tuple[str, ...] = ()
    # False when this child ran isolated and its changes did NOT land in the
    # base worktree (conflict/blocked). A dependent must not start on a
    # workspace that is missing its predecessor's changes.
    merge_ok: bool = True
    # An isolated child whose turn was interrupted has changes in its worktree
    # that are NOT in the base yet. Dependents must wait for the next turn to
    # merge them instead of starting on a workspace that lacks them.
    merge_pending: bool = False
    status: str = _RUNNING
    result: str = ""
    seed_history: list = field(default_factory=list, repr=False)
    inbox: list[tuple[str, str]] = field(
        default_factory=list,
        repr=False,
    )  # (message id, parent payload)
    inbox_sequence: int = 0
    dedup_key: str = ""  # stable key so a re-worded re-spawn is caught
    handle: asyncio.Task | None = field(default=None, repr=False)
    # Conversational state (native backend): the current turn's inner task —
    # the soft-interrupt target — and the queue an idle child parks on until
    # the parent sends a follow-up.
    turn_task: asyncio.Task | None = field(default=None, repr=False)
    wake: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    completed_turns: int = 0
    # Set whenever the child settles (a turn finished, it parked idle, or it
    # reached a terminal state); cleared when a new turn starts. The waiting
    # primitive for "this child has something new" now that a conversational
    # child's task handle outlives every individual turn.
    settled: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def running(self) -> bool:
        return self.status == _RUNNING

    @property
    def idle(self) -> bool:
        return self.status == _IDLE


def _format_result_message(sub: SubAgent) -> str:
    """The mailbox envelope a finished sub-agent posts to the parent."""
    return (
        f"Message Type: RESULT\n"
        f"Agent: {sub.id}\n"
        f"Status: {sub.status}\n"
        f"Payload:\n{sub.result}"
    )


class AgentControl:
    """Per-parent-session registry, concurrency limit, and result mailbox."""

    def __init__(
        self,
        workspace: str,
        model: str | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
        max_threads: int = MAX_CONCURRENT_SUBAGENTS,
        permission_mode: PermissionMode = PermissionMode.FULL_AUTO,
        execution_security_profile: ExecutionSecurityProfile | None = None,
        approval_callback: Any | None = None,
        runtime: DeepCodeRuntime | None = None,
        active_turn_id_provider: Any | None = None,
        runtime_input_sink: TurnInputSink | None = None,
        context_note_sink: Any | None = None,
        transcript_sink: Any | None = None,
        project_trusted: bool = False,
        project_hook_approval: Any | None = None,
        # How many dependency-ordered children may wait without running. A
        # graph that is declared faster than it drains is a memory leak and an
        # O(n^2) pump, so the queue is bounded like the fan-out.
        max_queued: int | None = None,
        # Routing targets: category name -> (model, connection_id). Empty keeps
        # the old single-model behaviour exactly.
        categories: Mapping[str, tuple[str | None, str | None]] | None = None,
        # Builds the child's immutable ExecutionProfile from a (model,
        # connection_id) override. Required for routing: a bare model name is
        # ignored by build_agent_session whenever a profile is present, so the
        # whole profile (context window, max output, provider) must be rebuilt.
        model_resolver: Callable[..., ExecutionProfile] | None = None,
    ) -> None:
        self._workspace = workspace
        self._model = model
        self._execution_profile = execution_profile
        self._max_threads = max(1, max_threads)
        self._max_queued = max(
            1, max_queued if max_queued is not None else self._max_threads * 2
        )
        # True while a Turn teardown is cancelling children. Result delivery
        # must not touch the Turn mailbox then: there is no Turn to inject into,
        # and its reserve() can wait while a mailbox is starting.
        self._cancelling = False
        self._permission_mode = permission_mode
        self._execution_security_profile = execution_security_profile
        self._approval_callback = approval_callback
        self._runtime = runtime
        self._active_turn_id_provider = active_turn_id_provider
        self._runtime_input_sink = runtime_input_sink
        # Canonical-history fallback for results that lose the delivery race
        # (see _post). Same callable the runner uses for mid-turn notes.
        self._context_note_sink = context_note_sink
        # Per-child transcript writer (see _dump_transcript); host-provided.
        self._transcript_sink = transcript_sink
        self._project_trusted = project_trusted
        # Sub-agents run the parent's project hooks under the same approval.
        self._project_hook_approval = project_hook_approval
        self._categories = dict(categories or {})
        self._model_resolver = model_resolver
        self._local_runtime_id = f"local-agent-runtime-{uuid.uuid4().hex}"
        self._agents: dict[str, SubAgent] = {}
        self._seq = 0
        self._mailbox: list[tuple[str, str, str]] = []
        self._mailbox_sequence = 0
        self._activity = asyncio.Event()
        self._git_lock = asyncio.Lock()
        self._history_provider = None  # set to the parent session's history()

    def set_history_provider(self, provider) -> None:
        """Wire the parent session's ``history()`` so fork_turns can inherit
        the parent's context. Called once, after the session is built."""
        self._history_provider = provider

    # -- introspection ---------------------------------------------------------

    def active_count(self) -> int:
        return sum(1 for a in self._agents.values() if a.running)

    def get(self, agent_id: str) -> SubAgent | None:
        return self._agents.get(agent_id)

    def all(self) -> list[SubAgent]:
        return list(self._agents.values())

    def interrupt(self, agent_id: str) -> str:
        """Stop a running sub-agent's CURRENT turn.

        dsh's keepInbox rule, in DeepCode form: a native child is not killed —
        only its in-flight turn is cancelled, and it parks idle with its
        conversation intact, ready for a redirecting ``send_message``. An
        external-CLI child has no turn boundary to stop at, so interrupting it
        remains a whole-run cancellation.
        """
        sub = self._agents.get(agent_id)
        if sub is None:
            return f"no such agent: {agent_id}"
        if sub.status == _QUEUED:
            sub.status = _CANCELLED
            sub.result = "cancelled before start"
            self._post(sub.id, _format_result_message(sub))
            sub.settled.set()
            self._pump()
            return f"{agent_id} was queued and has been cancelled"
        if sub.idle:
            return (
                f"{agent_id} is already idle — send_message to give it a new direction"
            )
        if not sub.running:
            return f"{agent_id} already finished ({sub.status})"
        if sub.backend == NATIVE_BACKEND and sub.turn_task is not None:
            if sub.turn_task.cancelling() == 0:
                sub.turn_task.cancel()
            return (
                f"interrupt requested for {agent_id}; it parks idle with its "
                "conversation intact — send_message to redirect it"
            )
        if sub.handle is not None and sub.handle.cancelling() == 0:
            sub.handle.cancel()
        return f"interrupt requested for {agent_id}"

    def send_message(self, agent_id: str, message: str) -> str:
        """Queue a message to a running or parked-idle sub-agent.

        A running child receives it at its next step; a parked one resumes with
        it as the next turn's input. A child that is running but not parked
        (external backend, or already finished) cannot take one.
        """
        sub = self._agents.get(agent_id)
        if sub is None:
            return f"no such agent: {agent_id}"
        if sub.backend != NATIVE_BACKEND:
            # External CLIs take one task and answer once; there is no inbox
            # on their side, so accepting the message would silently drop it.
            return (
                f"Error: {agent_id} runs on the {sub.backend} backend, which "
                "accepts no messages after start — its result will arrive on "
                "its own, or interrupt_agent it."
            )
        if not message.strip():
            return "Error: message is empty."
        waiting = [
            other
            for other in self._agents.values()
            if sub.id in other.deps and other.status in (_QUEUED, _RUNNING)
        ]
        if waiting:
            return (
                f"Error: {agent_id} still has dependent task(s) waiting "
                f"({', '.join(other.id for other in waiting)}); waking it now "
                "would run its next turn concurrently with them. Wait for them "
                "first."
            )
        if sub.status == _QUEUED:
            return (
                f"{agent_id} has not started yet — it is waiting on its "
                "dependencies and will run once they finish"
            )
        if sub.idle:
            # A parked child resumes with the message as its next turn's
            # input, on top of its accumulated conversation.
            sub.wake.put_nowait(message.strip())
            return f"delivered to {agent_id}; it resumes with your message"
        if not sub.running:
            return f"{agent_id} already finished ({sub.status}); cannot deliver"
        sub.inbox_sequence += 1
        sub.inbox.append(
            (
                f"parent:{sub.id}:{sub.inbox_sequence}",
                message.strip(),
            )
        )
        return f"delivered to {agent_id}"

    def _make_inbox_drainer(self, sub: SubAgent):
        """The injection_callback for a sub-agent: drains its send_message inbox
        into its own turn (mirrors the parent's drain_injections)."""

        async def drain(limit: int | None = None) -> list[SubagentMessage]:
            if not sub.inbox:
                return []
            take = sub.inbox if limit is None else sub.inbox[:limit]
            sub.inbox = sub.inbox[len(take) :]
            return [
                SubagentMessage(
                    message_id=message_id,
                    target_turn_id=sub.id,
                    agent_id="parent",
                    payload=payload,
                )
                for message_id, payload in take
            ]

        return drain

    # -- spawn -----------------------------------------------------------------

    def spawn(
        self,
        task: str,
        *,
        name: str | None = None,
        isolate: bool = True,
        fork_turns: str | int = "none",
        backend: str = NATIVE_BACKEND,
        persona: str | None = None,
        tools: list[str] | tuple[str, ...] | None = None,
        output_schema: dict | None = None,
        category: str | None = None,
        model: str | None = None,
        connection_id: str | None = None,
        depends_on: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        """Start a sub-agent in the background and return its id (non-blocking).

        ``name`` is a short stable label for the subtask; it is the dedup key, so
        re-spawning the same-named subtask while it runs is refused even if the
        task text was reworded. Without a name the (normalized) task text is the
        key. ``fork_turns`` inherits the parent's context: ``"none"`` (fresh),
        ``"all"`` (the whole conversation), or an int N (the last N turns) — only
        user messages and the parent's final answers carry over. Raises
        :class:`DuplicateAgentError` when a matching subtask is already running,
        :class:`AgentLimitError` when the concurrency limit is reached.
        """
        if backend != NATIVE_BACKEND:
            from core.harness.agents.external_backend import (
                ExternalBackendError,
                resolve_backend,
                resolve_executable,
            )

            # dsh's capability rule: a request needing a capability the
            # backend lacks fails loud — external CLIs never inherit parent
            # conversation, and take no composition (they run their own
            # product with its own prompt, tools, and output conventions).
            # Silently dropping any of these would mislead the model.
            if fork_turns != "none":
                raise AgentLimitError(
                    f"the {backend} backend does not inherit parent context; "
                    "use fork_turns='none' and write a self-contained task"
                )
            unsupported = [
                label
                for label, value in (
                    ("persona", persona),
                    ("tools", tools),
                    ("output_schema", output_schema),
                    ("category", category),
                    ("model", model),
                    ("connection_id", connection_id),
                )
                if value is not None
            ]
            if unsupported:
                raise AgentLimitError(
                    f"the {backend} backend does not support "
                    f"{', '.join(unsupported)} — external CLIs run their own "
                    "product configuration"
                )
            try:
                resolve_executable(resolve_backend(backend))
            except ExternalBackendError as exc:
                raise AgentLimitError(str(exc)) from exc
        if tools is not None:
            tool_names = tuple(str(t).strip() for t in tools if str(t).strip())
            if not tool_names:
                raise AgentLimitError(
                    "'tools' must name at least one tool when provided — an "
                    "empty allowlist would spawn a sub-agent that can do "
                    "nothing"
                )
        else:
            tool_names = None
        if output_schema is not None:
            from core.harness.agents.structured_result import (
                SchemaError,
                validate_output_schema,
            )

            try:
                output_schema = validate_output_schema(output_schema)
            except SchemaError as exc:
                raise AgentLimitError(str(exc)) from exc
        # Routing is resolved here, at the boundary, so a bad category fails the
        # caller instead of silently running the child on the parent's model.
        category_model: str | None = None
        category_connection: str | None = None
        if category is not None:
            target = self._categories.get(category)
            if target is None:
                available = ", ".join(sorted(self._categories)) or "none configured"
                raise UnknownCategoryError(
                    f"unknown routing category {category!r} — available: {available}"
                )
            category_model, category_connection = target
        routed_model = model or category_model
        routed_connection = connection_id or category_connection
        # Dependencies are validated now, not when the graph reaches them: a
        # typo in a dependency id would otherwise surface as a silent block.
        deps: tuple[str, ...] = ()
        if depends_on:
            normalized = tuple(
                str(dep).strip() for dep in depends_on if str(dep).strip()
            )
            unknown_deps = [dep for dep in normalized if dep not in self._agents]
            if unknown_deps:
                available = ", ".join(sorted(self._agents)) or "none spawned yet"
                raise AgentLimitError(
                    "unknown dependency id(s) "
                    f"{', '.join(unknown_deps)} — dependencies must already be "
                    f"spawned; available: {available}"
                )
            deps = normalized
        # Dedup against any prior subtask with this key that is still running OR
        # already succeeded — re-spawning finished work is the exact waste a real
        # model produced (it re-spawned modules it had already built). A FAILED
        # subtask may be retried.
        key = _slug(name) if name else " ".join(task.split()).lower()
        for existing in self._agents.values():
            # A failed, cancelled or blocked subtask may be retried; anything
            # else (done, idle, running, queued) is already handled.
            if existing.dedup_key == key and existing.status not in (
                _FAILED,
                _CANCELLED,
                _BLOCKED,
            ):
                verb = "is already handling" if existing.running else "already handled"
                tail = (
                    "its result will come back on its own; call wait_agent"
                    if existing.running
                    else "its output is already in the workspace; read it — do not re-spawn"
                )
                raise DuplicateAgentError(
                    f"{existing.id} {verb} this subtask — {tail}."
                )
        if not deps and self.active_count() >= self._max_threads:
            raise AgentLimitError(
                f"at most {self._max_threads} sub-agents can run at once; call "
                "wait_agent to collect a finished one before spawning more"
            )
        if deps:
            queued = sum(
                1 for existing in self._agents.values() if existing.status == _QUEUED
            )
            if queued >= self._max_queued:
                raise AgentLimitError(
                    f"at most {self._max_queued} sub-agents can wait on "
                    "dependencies at once; call wait_agent to collect finished "
                    "work before spawning more"
                )
        self._seq += 1
        base = _slug(name) if name else f"agent-{self._seq}"
        # A retry keeps the failed attempt visible (its transcript is evidence)
        # but redirects dependents that were pinned to it, otherwise they stay
        # blocked on a dead id forever.
        previous = self._agents.get(base)
        if previous is None or previous.status not in (_FAILED, _CANCELLED, _BLOCKED):
            # A retry may be addressed by name even when the failed attempt got
            # a suffixed id, and an unnamed retry has no id-stable base at all:
            # match the previous attempt by the same dedup key.
            previous = next(
                (
                    existing
                    for existing in self._agents.values()
                    if existing.dedup_key == key
                    and existing.status in (_FAILED, _CANCELLED, _BLOCKED)
                ),
                None,
            )
        retry_of = previous.id if previous is not None else None
        agent_id = base if base not in self._agents else f"{base}-{self._seq}"
        sub = SubAgent(
            id=agent_id,
            task=task,
            isolate=isolate,
            backend=backend,
            persona=(persona or "").strip() or None,
            tool_names=tool_names,
            output_schema=output_schema,
            category=category,
            model=routed_model,
            connection_id=routed_connection,
            deps=deps,
            status=_QUEUED if deps else _RUNNING,
            seed_history=self._fork_history(fork_turns),
            dedup_key=key,
        )
        self._agents[agent_id] = sub
        if retry_of is not None:
            self._redirect_blocked_dependents(retry_of, agent_id)
        if not deps:
            sub.handle = asyncio.ensure_future(self._run(sub))
        else:
            # Start immediately if the dependencies already finished, otherwise
            # park until its turn comes.
            self._pump()
        return agent_id

    def busy_ids(self) -> tuple[str, ...]:
        """Ids that are running or waiting on a dependency (not terminal)."""
        return tuple(
            sub.id
            for sub in self._agents.values()
            if sub.status in (_RUNNING, _QUEUED)
        )

    def _redirect_blocked_dependents(self, old_id: str, new_id: str) -> None:
        """Point children blocked on a retried dependency at the new attempt.

        Cascades: a child revived here may itself have blocked children (a
        chain ``a -> b -> c``), and leaving them blocked would strand the graph
        even though the root recovered.
        """
        revived = {old_id}
        changed = True
        while changed:
            changed = False
            for sub in self._agents.values():
                if sub.status != _BLOCKED:
                    continue
                if not any(dep in revived for dep in sub.deps):
                    continue
                # The parent may already have re-spawned this dependent after
                # the blocked notice (same name → same key). Reviving the old
                # copy too would run the work twice.
                if any(
                    other is not sub
                    and other.dedup_key == sub.dedup_key
                    and other.status not in (_BLOCKED, _FAILED, _CANCELLED)
                    for other in self._agents.values()
                ):
                    continue
                sub.deps = tuple(new_id if dep == old_id else dep for dep in sub.deps)
                sub.status = _QUEUED
                sub.result = ""
                sub.settled = asyncio.Event()  # the blocked notice was already sent
                revived.add(sub.id)
                changed = True

    def _pump(self) -> None:
        """Start every queued child whose dependencies are satisfied.

        Called after a spawn, after a child settles, and after cancellation.
        Scheduling stays inside the existing session loop — no new thread, no
        second semaphore: the running count is still bounded by ``max_threads``.
        """
        progressed = True
        while progressed:
            progressed = False
            for sub in list(self._agents.values()):
                if sub.status != _QUEUED:
                    continue
                dependency_states = [self._agents[dep].status for dep in sub.deps]
                satisfied = all(state in _SATISFIED for state in dependency_states)
                pending_merge = any(
                    state in _SATISFIED and self._agents[dep].merge_pending
                    for dep, state in zip(sub.deps, dependency_states, strict=True)
                )
                if not satisfied:
                    broken = [
                        dep
                        for dep, state in zip(sub.deps, dependency_states, strict=True)
                        if state in _TERMINAL or state == _BLOCKED
                    ]
                    if broken:
                        sub.status = _BLOCKED
                        sub.result = (
                            "blocked: dependency "
                            + ", ".join(f"{dep} ({self._agents[dep].status})" for dep in broken)
                            + "; the dependent task never started"
                        )
                        self._post(sub.id, _format_result_message(sub))
                        sub.settled.set()
                        progressed = True
                    continue
                if pending_merge:
                    # The dependency parked idle mid-turn without merging. Its
                    # worktree changes are not in the base yet, so starting now
                    # would build on stale state. Wait for its next turn.
                    continue
                # A predecessor whose isolated merge did not land leaves the
                # base without its changes; starting now would build on stale
                # state and silently lose work.
                unclean = [
                    dep
                    for dep, state in zip(sub.deps, dependency_states, strict=True)
                    if state in _SATISFIED and not self._agents[dep].merge_ok
                ]
                if unclean:
                    sub.status = _BLOCKED
                    sub.result = (
                        "blocked: dependency "
                        + ", ".join(unclean)
                        + " finished but its isolated changes did not merge "
                        "(conflict or blocked merge); reconcile the conflict, "
                        "then re-spawn this dependent"
                    )
                    self._post(sub.id, _format_result_message(sub))
                    sub.settled.set()
                    progressed = True
                    continue
                if self.active_count() >= self._max_threads:
                    continue
                sub.status = _RUNNING
                sub.handle = asyncio.ensure_future(self._run(sub))
                progressed = True

    def _fork_history(self, fork_turns: str | int) -> list:
        """The filtered slice of parent history a forked sub-agent inherits."""
        if fork_turns == "none" or self._history_provider is None:
            return []
        history = self._history_provider() or []
        kept = [
            dict(m)
            for m in history
            if m.get("role") == "user"
            or (
                m.get("role") == "assistant"
                and m.get("content")
                and not m.get("tool_calls")
            )
        ]
        if fork_turns == "all":
            return kept
        try:
            n = int(fork_turns)
        except (TypeError, ValueError) as exc:
            raise AgentLimitError(
                "fork_turns must be 'none', 'all', or a positive integer"
            ) from exc
        boundaries = [i for i, m in enumerate(kept) if m.get("role") == "user"]
        if n <= 0 or len(boundaries) <= n:
            return kept
        return kept[boundaries[len(boundaries) - n] :]

    async def _run(self, sub: SubAgent) -> None:
        try:
            if sub.backend != NATIVE_BACKEND:
                # External CLIs answer once: run, deliver, done.
                if sub.isolate:
                    sub.result = await self._run_isolated(sub)
                else:
                    sub.result = await self._run_task_in(sub, self._workspace)
                sub.status = _DONE
            else:
                # Native children are conversational: _converse posts each
                # turn's result itself and only ever exits by cancellation
                # or failure, both handled below.
                await self._converse(sub)
        except asyncio.CancelledError:
            sub.status = _FAILED
            sub.result = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - a sub-agent failure is data
            sub.status = _FAILED
            sub.result = f"error: {exc}"
        finally:
            if sub.status not in (_RUNNING, _IDLE):
                self._post(sub.id, _format_result_message(sub))
            sub.settled.set()  # release anyone awaiting this child
            # A terminal child frees a slot and may unblock its dependents.
            self._pump()

    async def _converse(self, sub: SubAgent) -> None:
        """A native child's whole life: turns alternating with idle parking.

        dsh's continuable-subagent shape in DeepCode form. Each turn runs
        through the single-turn seam (``_run_subagent``), which extends
        ``sub.seed_history`` with the finished conversation so the next turn
        continues it. Between turns the child parks on ``sub.wake`` until the
        parent sends a follow-up. The loop never returns normally: it ends by
        the parent's hard cancel (Turn teardown) or by a turn failure, both
        of which propagate to ``_run``.

        Soft interrupts land here: ``interrupt`` cancels only ``turn_task``,
        so the ``await`` below raises CancelledError while THIS task was not
        itself cancelled — distinguishable via ``cancelling()``, the same
        probe the kernel uses — and the child parks instead of dying.
        """
        workspace = self._workspace
        worktree = None
        if sub.isolate:
            from core.team.worktree import WorktreeManager

            worktree = WorktreeManager(self._workspace)
            async with self._git_lock:
                worktree.ensure_base()
                workspace = worktree.create(sub.id)
        try:
            while True:
                sub.status = _RUNNING
                sub.settled.clear()
                sub.turn_task = asyncio.ensure_future(
                    self._run_subagent(sub, workspace)
                )
                try:
                    result = await sub.turn_task
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling() > 0:
                        raise  # the parent tore the whole child down
                    if worktree is not None:
                        # This turn never reached its merge: whatever it wrote
                        # is still only in the worktree. Dependents must wait
                        # for the next turn instead of starting on the base.
                        sub.merge_pending = True
                    sub.result = (
                        "(turn interrupted; the sub-agent is parked idle with "
                        "its conversation intact — send_message to redirect it)"
                    )
                else:
                    if worktree is not None:
                        async with self._git_lock:
                            merge = worktree.merge(sub.id)
                            sub.merge_ok = merge.clean
                            sub.merge_pending = False
                        result = self._describe_merge(merge, result)
                    sub.completed_turns += 1
                    sub.result = result
                finally:
                    sub.turn_task = None
                sub.status = _IDLE
                sub.settled.set()
                # The first completed turn is what a dependent waits for.
                self._pump()
                self._post(sub.id, _format_result_message(sub))
                if sub.seed_history:
                    # Refresh the transcript now that the turn's status is
                    # settled — the in-turn dump necessarily wrote "running".
                    self._write_transcript(sub, sub.seed_history)
                sub.task = await sub.wake.get()
        finally:
            if worktree is not None:
                async with self._git_lock:
                    worktree.cleanup(sub.id)
                    self._remove_empty_worktree_root(worktree)

    @staticmethod
    def _describe_merge(merge, summary: str) -> str:
        if merge.clean:
            return f"(isolated, merged cleanly)\n{summary}"
        if merge.conflicts:
            return (
                f"(isolated, NOT merged — conflicts in "
                f"{', '.join(merge.conflicts)}; reconcile manually)\n{summary}"
            )
        return f"(isolated, merge blocked: {merge.detail})\n{summary}"

    @staticmethod
    def _remove_empty_worktree_root(worktree) -> None:
        """Last isolated agent out removes the now-empty shared dir."""
        try:
            root = worktree.worktrees_root
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass

    async def _run_isolated(self, sub: SubAgent) -> str:
        """One-shot isolated run (external backends; natives use _converse)."""
        from core.team.worktree import WorktreeManager

        wt = WorktreeManager(self._workspace)
        async with self._git_lock:
            wt.ensure_base()
            tree = wt.create(sub.id)
        try:
            summary = await self._run_task_in(sub, tree)
            async with self._git_lock:
                merge = wt.merge(sub.id)
                sub.merge_ok = merge.clean
        finally:
            async with self._git_lock:
                wt.cleanup(sub.id)
                self._remove_empty_worktree_root(wt)
        return self._describe_merge(merge, summary)

    async def _run_task_in(self, sub: SubAgent, workspace: str) -> str:
        """Dispatch one sub-agent's task to its backend in ``workspace``.

        Both execution modes (shared workspace and isolated worktree) funnel
        through here, so a backend never needs to know about isolation — it
        only ever sees a directory to work in.
        """
        if sub.backend != NATIVE_BACKEND:
            from core.harness.agents.external_backend import run_external_subagent

            return await run_external_subagent(sub.backend, sub.task, workspace)
        return await self._run_subagent(sub, workspace)

    async def _run_subagent(self, sub: SubAgent, workspace: str) -> str:
        from core.agent_setup import SYSTEM_PROMPT, build_agent_session
        from core.events import UserInput

        # Per-child composition. The persona and the capture contract ride the
        # system prompt as ADDITIONAL sections — the base prompt stays, so the
        # child keeps the ordinary tool discipline it was trained on.
        prompt_sections = [SYSTEM_PROMPT]
        if sub.persona:
            prompt_sections.append(f"## Persona\n{sub.persona}")
        capture = None
        extra_tools: tuple = ()
        if sub.output_schema is not None:
            from core.harness.agents.structured_result import (
                StructuredResultCapture,
            )

            capture = StructuredResultCapture(sub.output_schema)
            extra_tools = (capture.make_tool(),)
            prompt_sections.append(capture.prompt_addendum())
        tool_filter = None
        if sub.tool_names is not None:
            from core.harness.agents.structured_result import CAPTURE_TOOL_NAME

            allowed = frozenset(sub.tool_names)
            if capture is not None:
                # The allowlist must never lock out the mandatory submission
                # channel — that would be a self-contradictory composition.
                allowed |= {CAPTURE_TOOL_NAME}

            def tool_filter(names: tuple[str, ...]) -> tuple[str, ...]:
                return tuple(n for n in names if n in allowed)

        child_profile = self._execution_profile
        if child_profile is not None and (sub.model or sub.connection_id):
            if self._model_resolver is None:
                raise RuntimeError(
                    "sub-agent model routing needs a model resolver; this "
                    "session was built without one"
                )
            # Rebuild the whole profile, not just its model id: context window,
            # max output and provider all belong to the routed model, and the
            # compaction gate reads them.
            child_profile = self._model_resolver(
                # Omitted fields inherit the PARENT profile, not the global
                # implementation phase: resolve_selection() falls back to
                # agents.implementation.*, which is a different model when the
                # parent Turn pinned its own.
                connection_id=sub.connection_id or child_profile.connection_id,
                model=sub.model or child_profile.model_id,
                reasoning_effort=child_profile.reasoning_effort,
                phase="implementation",
            )
        session, _model, _engine = build_agent_session(
            workspace=workspace,
            model=(
                child_profile.model_id
                if child_profile is not None
                else (sub.model or self._model)
            ),
            connection_id=(
                child_profile.connection_id
                if child_profile is not None
                else sub.connection_id
            ),
            system_prompt="\n\n".join(prompt_sections),
            execution_profile=child_profile,
            allow_spawn=False,  # depth cap: sub-agents cannot spawn again
            injection_callback=self._make_inbox_drainer(sub),
            agent_context=(sub.id, "subagent"),  # fires SubagentStart/Stop hooks
            approval_callback=self._approval_callback,
            permission_mode_override=(
                self._permission_mode
                if self._execution_security_profile is None
                else None
            ),
            execution_security_profile=self._execution_security_profile,
            runtime=self._runtime,
            project_trusted=self._project_trusted,
            project_hook_approval=self._project_hook_approval,
            extra_tools=extra_tools,
            tool_filter=tool_filter,
        )
        # Label every LLM call this child makes so cost reporting can attribute
        # it to a routing category instead of guessing from the model name.
        from core.observability.context import pop_agent_path, set_agent_path

        label = sub.category or (
            "explicit" if (sub.model or sub.connection_id) else "inherit"
        )
        path_token = set_agent_path(f"{label}:{sub.id}")
        try:
            if sub.seed_history:
                # fork_turns seed on the first turn; on follow-up turns this
                # is the child's own accumulated conversation, which is what
                # makes the resumed turn a continuation rather than a restart.
                session.load_history(sub.seed_history)
            final = ""
            async for event in session.run_stream(UserInput(text=sub.task)):
                if event.msg.type == "task_complete":
                    final = event.msg.final_text or ""
            # Carry the finished conversation forward for the next turn.
            history = getattr(session, "history", None)
            if history is not None:
                sub.seed_history = [dict(m) for m in history]
            if capture is not None:
                structured = capture.render()
                if structured is None:
                    # dsh's rule: a structured delegation without a submission
                    # is a failure, not a prose answer quietly standing in.
                    raise RuntimeError(
                        "sub-agent finished without submitting a structured "
                        f"result; its last message: {final.strip()[:400]!r}"
                    )
                return structured
            return final.strip() or "(sub-agent produced no summary)"
        finally:
            self._dump_transcript(sub, session)
            await session.aclose()
            pop_agent_path(path_token)

    def _dump_transcript(self, sub: SubAgent, session: Any) -> None:
        """Report the child's full conversation to the host's transcript sink.

        The autopsy channel (dsh children are session-backed; DeepCode's were
        invisible): every finished or failed turn hands the host the complete
        message list, so a parent Session can keep a per-child transcript its
        user can actually inspect. Absent sink or failure never disturbs the
        run — visibility must not break the thing it observes.
        """
        if self._transcript_sink is None:
            return
        history = getattr(session, "history", None)
        if history is None:
            return
        self._write_transcript(sub, history)

    def _write_transcript(self, sub: SubAgent, messages: Any) -> None:
        if self._transcript_sink is None:
            return
        try:
            self._transcript_sink(
                sub.id,
                sub.task,
                sub.status,
                [dict(m) for m in messages],
            )
        except Exception:  # noqa: BLE001 - observability must not kill the run
            logger.exception("sub-agent transcript could not be written: {}", sub.id)

    # -- mailbox ---------------------------------------------------------------

    def _post(self, agent_id: str, message: str) -> None:
        """Publish a child result without letting delivery kill the child.

        During teardown the Turn mailbox is skipped: there is no Turn to inject
        into, and its ``reserve`` may wait while a mailbox starts. The canonical
        note sink still receives the message.
        """
        self._mailbox_sequence += 1
        message_id = f"subagent:{agent_id}:{self._mailbox_sequence}"
        target_turn_id = None
        if self._active_turn_id_provider is not None:
            try:
                target_turn_id = self._active_turn_id_provider()
            except Exception:  # noqa: BLE001 - delivery must not die here
                logger.exception("could not read the active Turn for {}", agent_id)
        if self._runtime_input_sink is not None:
            delivered = False
            if target_turn_id and not self._cancelling:
                try:
                    delivered = bool(
                        self._runtime_input_sink(
                            SubagentMessage(
                                message_id=message_id,
                                target_turn_id=target_turn_id,
                                agent_id=agent_id,
                                payload=message,
                            )
                        )
                    )
                except Exception:  # noqa: BLE001 - delivery must not die here
                    logger.exception(
                        "sub-agent result could not be injected: {}", agent_id
                    )
            if not delivered and self._context_note_sink is not None:
                # The result lost the active-Turn race (the Turn closed before
                # the sub-agent finished, or the mailbox refused it). Append
                # it straight to the canonical Session as a between-turns
                # message: the next acquire reloads visible history and the
                # model sees it at the start of its next Turn — instead of
                # the result surviving only on SubAgent.result, unread.
                try:
                    self._context_note_sink(
                        message, "subagent", already_in_history=False
                    )
                except Exception:  # noqa: BLE001 - persistence must not kill _run
                    logger.exception(
                        "late sub-agent result could not be persisted: {}",
                        agent_id,
                    )
        else:
            self._mailbox.append(
                (
                    message_id,
                    agent_id,
                    message,
                )
            )
        self._activity.set()

    async def wait_for_activity(self, timeout: float | None) -> str:
        """Park until a sub-agent posts to the mailbox, or timeout. Returns a
        short outcome; the messages themselves reach the model via injection."""
        if self._mailbox:
            return "One or more sub-agents have results waiting."
        if self.active_count() == 0:
            queued = sum(1 for sub in self._agents.values() if sub.status == _QUEUED)
            blocked = sum(1 for sub in self._agents.values() if sub.status == _BLOCKED)
            if queued or blocked:
                return (
                    f"No sub-agent is running: {queued} queued, {blocked} blocked "
                    "by a failed or cancelled dependency."
                )
            return "No sub-agents are running."
        self._activity.clear()
        # Re-check after clearing: a result posted in the check→clear window set
        # the event we just reset, but its message is safe in the mailbox.
        if self._mailbox:
            return "One or more sub-agents have results waiting."
        try:
            await asyncio.wait_for(self._activity.wait(), timeout=timeout)
        except TimeoutError:
            return "Wait timed out; sub-agents are still running."
        return "One or more sub-agents finished."

    async def drain_injections(
        self,
        limit: int | None = None,
    ) -> list[SubagentMessage]:
        """Pop pending mailbox messages as user-role injections for the parent's
        next turn. Wired as the run's ``injection_callback``."""
        if not self._mailbox:
            return []
        target_turn_id = (
            self._active_turn_id_provider()
            if self._active_turn_id_provider is not None
            else self._local_runtime_id
        )
        if not target_turn_id:
            return []
        take = self._mailbox if limit is None else self._mailbox[:limit]
        self._mailbox = self._mailbox[len(take) :]
        if not self._mailbox:
            self._activity.clear()
        return [
            SubagentMessage(
                message_id=message_id,
                target_turn_id=target_turn_id,
                agent_id=agent_id,
                payload=payload,
            )
            for message_id, agent_id, payload in take
        ]

    async def close(self) -> None:
        """Release all running sub-agents before session teardown returns."""

        await self.cancel_running()

    async def cancel_running(self) -> None:
        """Cancel and join every child task owned by the active parent Turn.

        Cancellation is not complete when :meth:`asyncio.Task.cancel` returns;
        child cleanup runs while the resulting ``CancelledError`` unwinds.  The
        parent therefore joins each task so no sub-agent can keep using tools,
        network access, or its worktree after the Turn becomes terminal.

        Queued children have no task handle, so they are terminated here
        explicitly — otherwise a Turn teardown would silently leave them alive
        in the graph and a later spawn could start them.
        """

        self._cancelling = True
        try:
            for sub in self._agents.values():
                if sub.status == _QUEUED:
                    sub.status = _CANCELLED
                    sub.result = "cancelled before start"
                    self._post(sub.id, _format_result_message(sub))
                    sub.settled.set()
            handles = tuple(
                sub.handle
                for sub in self._agents.values()
                if sub.handle is not None and not sub.handle.done()
            )
            for handle in handles:
                # A second cancel while the child is unwinding would interrupt
                # its AgentSession.aclose() finally block. One cancellation
                # request is sufficient; all callers still join below.
                if handle.cancelling() == 0:
                    handle.cancel()
            if handles:
                await asyncio.gather(*handles, return_exceptions=True)
        finally:
            self._cancelling = False
