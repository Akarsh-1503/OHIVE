"""Job records and the store that owns their execution, events and disk lifetime.

Concurrency model
-----------------
SLAM is CPU-bound, so every job runs in a `ProcessPoolExecutor` child — never a thread — and
the uvicorn event loop never contends for the GIL with it. The pool is deliberately small
(`SLAM_MAX_CONCURRENT_JOBS`, default 1): a second concurrent reconstruction on an 8-vCPU host
roughly halves single-job throughput and would invalidate the published benchmark, so extra
jobs wait in FIFO order and report their `queue_position`.

Progress flows child -> parent over a `multiprocessing.Manager` queue, drained by a daemon
thread that hands events to the loop via `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import queue as queue_mod
import shutil
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import exports, runner
from app.jobs.settings import Settings
from app.models import Job, utc_now_iso

log = logging.getLogger("driftless.jobs")

PROGRESS_INTERVAL_S = 0.15  # contract: job.progress coalesced to at most one per ~150 ms
_DRAIN_POLL_S = 0.1
_SUBSCRIBER_QUEUE_MAX = 256


@dataclass
class JobRecord:
    job_id: str
    filename: str
    created_at: str
    dir: Path
    video_path: Path
    config: runner.RunnerConfig
    video: dict[str, Any]
    truncated: bool
    accepted_monotonic: float
    status: str = "queued"
    metrics: dict[str, Any] | None = None
    error: str | None = None
    queue_wait_ms: int = 0
    created_monotonic: float = field(default_factory=time.monotonic)

    def to_public(self, queue_position: int | None) -> dict[str, Any]:
        return Job(
            job_id=self.job_id,
            status=self.status,  # type: ignore[arg-type]
            filename=self.filename,
            created_at=self.created_at,
            video=self.video,  # type: ignore[arg-type]
            metrics=self.metrics,  # type: ignore[arg-type]
            error=self.error,
            queue_position=queue_position,
            truncated=self.truncated,
        ).model_dump()


class JobStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jobs: dict[str, JobRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[tuple[str, Any] | None]]] = {}
        self._terminal: dict[str, tuple[str, dict[str, Any]]] = {}
        self._pending: list[str] = []
        self._running: set[str] = set()
        self._pending_progress: dict[str, dict[str, Any]] = {}
        self._last_progress: dict[str, dict[str, Any]] = {}
        self._next_progress_at: dict[str, float] = {}
        self._flush_tasks: dict[str, asyncio.Task[None]] = {}
        self._pool: ProcessPoolExecutor | None = None
        self._manager: Any = None
        self._progress_q: Any = None
        self._drain: threading.Thread | None = None
        self._drain_stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._slots: asyncio.Semaphore | None = None
        self._janitor: asyncio.Task[None] | None = None

    # --- lifecycle ----------------------------------------------------------------------

    async def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_running_loop()
        self._slots = asyncio.Semaphore(self.settings.max_concurrent_jobs)
        # "spawn" keeps child processes free of inherited loop/socket state; it is already
        # the default on macOS and on Python 3.14+, so pin it for identical behaviour on Linux.
        ctx = multiprocessing.get_context("spawn")
        self._pool = ProcessPoolExecutor(
            max_workers=self.settings.max_concurrent_jobs, mp_context=ctx
        )
        self._manager = ctx.Manager()
        self._progress_q = self._manager.Queue()
        self._drain = threading.Thread(target=self._drain_loop, name="progress-drain", daemon=True)
        self._drain.start()
        self._janitor = asyncio.create_task(self._janitor_loop())
        log.info(
            "job store started",
            extra={
                "max_concurrent_jobs": self.settings.max_concurrent_jobs,
                "slam_backend": self.settings.slam_backend,
                "data_dir": str(self.settings.data_dir),
            },
        )

    async def shutdown(self) -> None:
        if self._janitor is not None:
            self._janitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._janitor
        for task in list(self._flush_tasks.values()):
            task.cancel()
        # Jobs that never started are cancelled outright; jobs already in a worker are given
        # the chance to finish so the pool drains cleanly rather than orphaning a child.
        for job_id in list(self._pending):
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
        running = [self._tasks[j] for j in list(self._running) if j in self._tasks]
        if running:
            await asyncio.wait(running, timeout=30)
        self._drain_stop.set()
        if self._drain is not None:
            await asyncio.to_thread(self._drain.join, 2.0)
        if self._pool is not None:
            await asyncio.to_thread(self._pool.shutdown, True)
            self._pool = None
        if self._manager is not None:
            self._manager.shutdown()
            self._manager = None

    # --- job creation -------------------------------------------------------------------

    def new_job_dir(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        job_dir = self.settings.data_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        return job_id, job_dir

    def register(
        self,
        job_id: str,
        job_dir: Path,
        video_path: Path,
        filename: str,
        video: dict[str, Any],
        config: runner.RunnerConfig,
        accepted_monotonic: float,
    ) -> JobRecord:
        record = JobRecord(
            job_id=job_id,
            filename=filename,
            created_at=utc_now_iso(),
            dir=job_dir,
            video_path=video_path,
            config=config,
            video=video,
            truncated=video["frame_count"] > config.max_frames,
            accepted_monotonic=accepted_monotonic,
        )
        self._jobs[job_id] = record
        # Enqueue synchronously so the 201 response already carries a truthful
        # queue_position; the task only moves the job from pending to running.
        self._pending.append(job_id)
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))
        return record

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        record = self._jobs.get(job_id)
        if record is None:
            return None
        return record.to_public(self.queue_position(job_id))

    def queue_position(self, job_id: str) -> int | None:
        """Jobs that must finish before this one starts. 0 = running or next up."""
        if job_id in self._running:
            return 0
        if job_id in self._pending:
            return len(self._running) + self._pending.index(job_id)
        return None

    def counts(self) -> tuple[int, int]:
        return len(self._running), len(self._pending)

    # --- execution ----------------------------------------------------------------------

    async def _run(self, job_id: str) -> None:
        record = self._jobs[job_id]
        assert self._slots is not None and self._loop is not None
        try:
            await self._slots.acquire()
        except asyncio.CancelledError:
            if job_id in self._pending:
                self._pending.remove(job_id)
            self._fail(job_id, "cancelled: server shutting down")
            raise

        if job_id in self._pending:
            self._pending.remove(job_id)
        self._running.add(job_id)
        record.queue_wait_ms = int((time.monotonic() - record.accepted_monotonic) * 1000)
        record.status = "decoding"
        self.publish(job_id, "job.stage", {"stage": "decoding", "message": "Starting"})

        task = runner.WorkerTask(
            job_id=job_id,
            video_path=str(record.video_path),
            job_dir=str(record.dir),
            config=record.config,
            accepted_monotonic=record.accepted_monotonic,
            queue_wait_ms=record.queue_wait_ms,
            job_header={
                "job_id": job_id,
                "filename": record.filename,
                "created_at": record.created_at,
                "truncated": record.truncated,
                "video": record.video,
            },
        )
        try:
            assert self._pool is not None
            summary = await self._loop.run_in_executor(
                self._pool, runner.run_job, task, self._progress_q
            )
        except Exception as exc:
            log.exception("job failed", extra={"job_id": job_id})
            self._fail(job_id, f"{type(exc).__name__}: {exc}")
        else:
            self._flush_progress(job_id)
            record.metrics = summary["metrics"]
            record.status = "completed"
            self._emit_terminal(
                job_id, "job.completed", {"job_id": job_id, "metrics": record.metrics}
            )
            log.info(
                "job completed",
                extra={
                    "job_id": job_id,
                    "wall_ms": record.metrics["wall_ms"],
                    "queue_wait_ms": record.metrics["queue_wait_ms"],
                    "map_points": record.metrics["map_points"],
                },
            )
        finally:
            self._running.discard(job_id)
            self._slots.release()

    def _fail(self, job_id: str, message: str) -> None:
        record = self._jobs.get(job_id)
        if record is None or record.status in ("completed", "failed"):
            return
        record.status = "failed"
        record.error = message
        self._emit_terminal(job_id, "job.failed", {"error": message})

    # --- event bus ----------------------------------------------------------------------

    def subscribe(self, job_id: str) -> asyncio.Queue[tuple[str, Any] | None]:
        q: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
        self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[tuple[str, Any] | None]) -> None:
        subs = self._subscribers.get(job_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            self._subscribers.pop(job_id, None)

    def terminal_event(self, job_id: str) -> tuple[str, dict[str, Any]] | None:
        return self._terminal.get(job_id)

    def publish(self, job_id: str, event: str, data: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(job_id, ())):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                # A subscriber this far behind is a stalled client; drop its oldest frame
                # rather than letting one slow reader pin memory for everyone.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait((event, data))

    def _emit_terminal(self, job_id: str, event: str, data: dict[str, Any]) -> None:
        self._terminal[job_id] = (event, data)
        self.publish(job_id, event, data)
        for q in list(self._subscribers.get(job_id, ())):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)  # sentinel: stream closes after completed/failed

    # --- worker -> loop bridge ----------------------------------------------------------

    def _drain_loop(self) -> None:
        """Daemon thread: block on the manager queue, hand each event to the loop."""
        while not self._drain_stop.is_set():
            try:
                message = self._progress_q.get(timeout=_DRAIN_POLL_S)
            except queue_mod.Empty:
                continue
            except (EOFError, OSError, BrokenPipeError):
                return
            loop = self._loop
            if loop is None or loop.is_closed():
                return
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._on_worker_event, message)

    def _on_worker_event(self, message: dict[str, Any]) -> None:
        job_id = message.get("job_id", "")
        record = self._jobs.get(job_id)
        if record is None or record.status in ("completed", "failed"):
            return
        kind, data = message.get("kind"), message.get("data", {})
        if kind == "stage":
            record.status = data["stage"]
            self._flush_progress(job_id)
            self.publish(job_id, "job.stage", data)
        elif kind == "loop_closure":
            self.publish(job_id, "job.loop_closure", data)
        elif kind == "progress":
            self._pending_progress[job_id] = data
            self._coalesce_progress(job_id)

    def _coalesce_progress(self, job_id: str) -> None:
        now = time.monotonic()
        due = self._next_progress_at.get(job_id, 0.0)
        if now >= due:
            self._flush_progress(job_id)
            return
        if job_id not in self._flush_tasks:
            self._flush_tasks[job_id] = asyncio.create_task(self._flush_later(job_id, due - now))

    async def _flush_later(self, job_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            self._flush_progress(job_id)
        finally:
            self._flush_tasks.pop(job_id, None)

    def _flush_progress(self, job_id: str) -> None:
        data = self._pending_progress.pop(job_id, None)
        if data is None:
            return
        self._next_progress_at[job_id] = time.monotonic() + PROGRESS_INTERVAL_S
        self._last_progress[job_id] = data
        self.publish(job_id, "job.progress", data)

    def last_progress(self, job_id: str) -> dict[str, Any] | None:
        """Most recent progress payload, so a late SSE subscriber isn't left at 0%."""
        return self._pending_progress.get(job_id) or self._last_progress.get(job_id)

    # --- janitor ------------------------------------------------------------------------

    async def _janitor_loop(self) -> None:
        interval = max(60.0, self.settings.job_ttl_hours * 3600.0 / 24.0)
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.reap)

    def reap(self, now: float | None = None) -> int:
        """Delete job directories past their TTL. Returns the number reaped."""
        cutoff = (now if now is not None else time.time()) - self.settings.job_ttl_hours * 3600.0
        reaped = 0
        for job_dir in sorted(self.settings.data_dir.glob("*")):
            if not job_dir.is_dir():
                continue
            job_id = job_dir.name
            if job_id in self._running or job_id in self._pending:
                continue
            try:
                if job_dir.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(job_dir, ignore_errors=True)
            for table in (
                self._jobs,
                self._tasks,
                self._terminal,
                self._subscribers,
                self._pending_progress,
                self._last_progress,
                self._next_progress_at,
            ):
                table.pop(job_id, None)  # type: ignore[attr-defined]
            reaped += 1
        if reaped:
            log.info("janitor reaped jobs", extra={"reaped": reaped})
        return reaped

    # --- artifacts ----------------------------------------------------------------------

    def artifact(self, job_id: str, name: str) -> Path | None:
        record = self._jobs.get(job_id)
        if record is None:
            return None
        path = record.dir / name
        return path if path.exists() else None

    def full_reconstruction(self, job_id: str) -> dict[str, Any] | None:
        path = self.artifact(job_id, runner.RECON_FULL)
        return exports.load_json(path) if path is not None else None
