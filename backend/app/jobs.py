import asyncio
import contextlib
import logging
import secrets
import shutil
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.converter import Converter, check_space
from app.models import ConversionError, ErrorDetail, JobStatus, Stage

logger = logging.getLogger("sounddrop")
# Admission only rejects when storage is already nearly full. The converter reserves space for
# the actual job once the video's duration and size are known.
ADMISSION_RESERVE = 16 * 1024**2


class Job:
    def __init__(self, url: str, bitrate: int, directory: Path):
        self.id = secrets.token_urlsafe(32)
        self.url = url
        self.directory = directory / self.id
        self.file: Path | None = None
        self.readers = 0
        self.status = JobStatus(
            id=self.id, stage=Stage.QUEUED, bitrate=bitrate, created_at=datetime.now(UTC)
        )

    def update(self, **changes) -> None:
        self.status = self.status.model_copy(update=changes)


class JobManager:
    def __init__(self, settings: Settings, converter: Converter | None = None):
        self.settings = settings
        self.converter = converter or Converter(settings)
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=settings.max_queue)
        self.rates: dict[str, deque[float]] = {}
        self.tasks: list[asyncio.Task] = []
        self.accepting = True

    async def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        # Only app-owned directories are cleaned; jobs intentionally do not survive restarts.
        for path in self.settings.data_dir.iterdir():
            if path.is_dir() and len(path.name) == 43:
                shutil.rmtree(path, ignore_errors=True)
        self.tasks = [asyncio.create_task(self.worker()), asyncio.create_task(self.sweeper())]

    async def close(self) -> None:
        self.accepting = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for job in self.jobs.values():
            if job.status.stage != Stage.READY:
                shutil.rmtree(job.directory, ignore_errors=True)

    def prune_rates(self) -> None:
        threshold = time.monotonic() - 3600
        for ip, events in list(self.rates.items()):
            while events and events[0] <= threshold:
                events.popleft()
            if not events:
                del self.rates[ip]

    def submit(self, url: str, bitrate: int, ip: str) -> Job:
        self.prune_rates()
        if len(self.rates.get(ip, [])) >= self.settings.hourly_limit:
            raise ConversionError(
                "rate_limited", "You've reached the hourly limit. Please try again later."
            )
        if not self.accepting or self.queue.full():
            raise ConversionError(
                "queue_full", "The conversion queue is full. Please retry in a few minutes."
            )
        if len(self.jobs) >= 1000 or len(self.rates) >= 10000:
            raise ConversionError("queue_full", "The server is busy. Please retry later.")
        check_space(self.settings, ADMISSION_RESERVE)
        job = Job(url, bitrate, self.settings.data_dir)
        self.jobs[job.id] = job
        self.queue.put_nowait(job)
        self.rates.setdefault(ip, deque()).append(time.monotonic())
        return job

    def get(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job and job.status.expires_at and job.status.expires_at <= datetime.now(UTC):
            return None
        return job

    def status(self, job: Job) -> JobStatus:
        position = None
        if job.status.stage == Stage.QUEUED:
            waiting = [j.id for j in self.jobs.values() if j.status.stage == Stage.QUEUED]
            position = waiting.index(job.id) + 1
        return job.status.model_copy(update={"queue_position": position})

    async def worker(self) -> None:
        while True:
            job = await self.queue.get()
            started = time.monotonic()
            try:
                check_space(self.settings, ADMISSION_RESERVE)
                job.directory.mkdir(mode=0o700)
                async with asyncio.timeout(self.settings.timeout):
                    job.file = await self.converter.convert(
                        job.url, job.status.bitrate, job.directory, job.update
                    )
                job.update(
                    stage=Stage.READY,
                    progress=100,
                    expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.ttl),
                )
                logger.info("conversion_complete elapsed_seconds=%.1f", time.monotonic() - started)
            except asyncio.CancelledError:
                shutil.rmtree(job.directory, ignore_errors=True)
                raise
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    error = ErrorDetail(
                        code="timeout", message="Conversion took too long. Try a shorter video."
                    )
                elif isinstance(exc, ConversionError):
                    error = ErrorDetail(code=exc.code, message=exc.message)
                else:
                    error = ErrorDetail(
                        code="conversion_failed", message="Conversion failed. Please retry later."
                    )
                job.update(
                    stage=Stage.FAILED,
                    progress=None,
                    error=error,
                    expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.ttl),
                )
                shutil.rmtree(job.directory, ignore_errors=True)
                logger.warning("conversion_failed code=%s", error.code)
            finally:
                self.queue.task_done()

    def cleanup(self) -> None:
        now = datetime.now(UTC)
        for job_id, job in list(self.jobs.items()):
            if job.status.expires_at and job.status.expires_at <= now and job.readers == 0:
                shutil.rmtree(job.directory, ignore_errors=True)
                del self.jobs[job_id]
        self.prune_rates()

    async def sweeper(self) -> None:
        while True:
            await asyncio.sleep(30)
            with contextlib.suppress(OSError):
                self.cleanup()
