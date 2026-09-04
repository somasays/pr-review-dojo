"""Async worker that drains a queue of tasks and dispatches them to handlers.

Handlers are ordinary sync callables from the services layer; they run in a
thread so the event loop is never blocked. Concurrency is bounded by a
semaphore and shutdown waits for in-flight tasks.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Any]
AsyncHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Task:
    kind: str
    payload: dict[str, Any]
    attempt: int = 1


@dataclass
class WorkerStats:
    processed: int = 0
    failed: int = 0
    retried: int = 0
    errors: list[str] = field(default_factory=list)


class QueueWorker:
    def __init__(
        self,
        queue: asyncio.Queue[Task],
        concurrency: int = 4,
        max_attempts: int = 3,
        poll_timeout: float = 0.1,
        task_timeout: float = 30.0,
    ) -> None:
        self.queue = queue
        self.sem = asyncio.Semaphore(concurrency)
        self.max_attempts = max_attempts
        self.poll_timeout = poll_timeout
        self.task_timeout = task_timeout
        self.handlers: dict[str, Handler | AsyncHandler] = {}
        self.stats = WorkerStats()
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task[None]] = set()

    def register(self, kind: str, handler: Handler | AsyncHandler) -> None:
        self.handlers[kind] = handler

    def stop(self) -> None:
        self._stop.set()

    async def _invoke(self, handler: Handler | AsyncHandler, payload: dict[str, Any]) -> Any:
        if asyncio.iscoroutinefunction(handler):
            call = handler(payload)
        else:
            call = asyncio.to_thread(handler, payload)
        return await asyncio.wait_for(call, timeout=self.task_timeout)

    async def _retry_or_fail(self, task: Task, exc: BaseException) -> None:
        if task.attempt < self.max_attempts:
            self.stats.retried += 1
            await self.queue.put(Task(task.kind, task.payload, attempt=task.attempt + 1))
        else:
            self.stats.failed += 1
            self.stats.errors.append(f"{task.kind}: {exc}")

    async def _handle(self, task: Task) -> None:
        async with self.sem:
            handler = self.handlers.get(task.kind)
            if handler is None:
                self.stats.failed += 1
                self.stats.errors.append(f"no handler for {task.kind}")
                return
            try:
                await self._invoke(handler, task.payload)
                self.stats.processed += 1
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                # A sync handler keeps running in its thread after the timeout, so a
                # retry would repeat its side effects. Record it and move on.
                log.warning(
                    "task %s attempt %d exceeded %.1fs", task.kind, task.attempt, self.task_timeout
                )
                self.stats.failed += 1
                self.stats.errors.append(f"{task.kind}: timed out after {self.task_timeout:.1f}s")
            except Exception as exc:
                log.warning("task %s attempt %d failed: %s", task.kind, task.attempt, exc)
                await self._retry_or_fail(task, exc)

    async def run(self) -> None:
        """Poll until stop() is called and the queue is drained."""
        while not (self._stop.is_set() and self.queue.empty()):
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=self.poll_timeout)
            except TimeoutError:
                continue
            t = asyncio.create_task(self._handle(task))
            self._inflight.add(t)
            t.add_done_callback(self._inflight.discard)
            t.add_done_callback(lambda _t: self.queue.task_done())
        if self._inflight:
            await asyncio.gather(*self._inflight)

    async def run_until_idle(self, idle_after: float = 0.3) -> None:
        """Convenience for scripts: stop once the queue has been empty for a while."""

        async def watch() -> None:
            while True:
                await asyncio.sleep(idle_after)
                if self.queue.empty() and not self._inflight:
                    self.stop()
                    return

        await asyncio.gather(self.run(), watch())
