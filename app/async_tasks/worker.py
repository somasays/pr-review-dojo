"""Async worker that drains a queue of tasks and dispatches them to handlers.

Handlers are ordinary sync callables from the services layer; they run in a
thread so the event loop is never blocked. Concurrency is bounded by a
semaphore and shutdown waits for in-flight tasks.

A handler that cannot make progress yet raises `RetryAfter`, which puts the
task back on the queue once the delay has passed instead of burning an
attempt straight away.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Any]
AsyncHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class RetryAfter(Exception):
    """Raised by a handler that wants the task back after `delay` seconds."""

    def __init__(self, delay: float) -> None:
        super().__init__(f"retry after {delay}s")
        self.delay = delay


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
        on_idle: Callable[[WorkerStats], None] | None = None,
    ) -> None:
        self.queue = queue
        self.sem = asyncio.Semaphore(concurrency)
        self.max_attempts = max_attempts
        self.poll_timeout = poll_timeout
        self.on_idle = on_idle
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
            return await handler(payload)
        return await asyncio.to_thread(handler, payload)

    async def _requeue_after(self, task: Task, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.queue.put(task)

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
            except RetryAfter as exc:
                log.info("task %s asked for %.2fs", task.kind, exc.delay)
                if task.attempt < self.max_attempts:
                    self.stats.retried += 1
                    self._requeue_after(
                        Task(task.kind, task.payload, attempt=task.attempt + 1), exc.delay
                    )
                else:
                    self.stats.failed += 1
                    self.stats.errors.append(f"{task.kind}: {exc}")
            except BaseException as exc:
                log.warning("task %s attempt %d failed: %s", task.kind, task.attempt, exc)
                if task.attempt < self.max_attempts:
                    self.stats.retried += 1
                    await self.queue.put(Task(task.kind, task.payload, attempt=task.attempt + 1))
                else:
                    self.stats.failed += 1
                    self.stats.errors.append(f"{task.kind}: {exc}")

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
                    if self.on_idle is not None:
                        self.on_idle(self.stats)
                    self.stop()
                    return

        await asyncio.gather(self.run(), watch())

    def drain(self) -> None:
        """Run until idle. For scripts and the module entrypoint."""
        asyncio.get_event_loop().run_until_complete(self.run_until_idle())


def serve_admin(worker: QueueWorker, port: int = 8081) -> None:
    """Tiny control plane so an operator can drain the worker before a restart."""

    class Admin(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            worker.stop()
            self.send_response(202)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", port), Admin)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main() -> None:
    import httpx

    from app.async_tasks.handlers import build_handlers
    from app.services.config import get_settings
    from app.services.webhooks import HttpxTransport, WebhookEndpoint

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    queue: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(queue, concurrency=settings.worker_concurrency)
    build_handlers(
        worker,
        transport=HttpxTransport(httpx.AsyncClient()),
        endpoints=[WebhookEndpoint(url) for url in settings.webhook_endpoints],
        settings=settings,
    )
    serve_admin(worker)
    worker.drain()


if __name__ == "__main__":
    main()
