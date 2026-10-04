import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.types import Receive, Scope, Send

from app.config import Settings
from app.converter import Converter, tools_ready
from app.jobs import Job, JobManager
from app.models import ConversionError, ConversionRequest, JobStatus, Stage
from app.urls import download_filename, normalize_url


class JobFileResponse(FileResponse):
    def __init__(self, job: Job):
        self.job = job
        super().__init__(
            job.file,
            media_type="audio/mpeg",
            filename=download_filename(job.status.title or "audio"),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.job.readers += 1
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.job.readers -= 1


def create_app(settings: Settings | None = None, converter: Converter | None = None) -> FastAPI:
    settings = settings or Settings()
    manager = JobManager(settings, converter)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("sounddrop").info(
            "ytdlp_access cookies=%s proxy=%s",
            "on" if settings.cookies_file else "off",
            "on" if settings.proxy else "off",
        )
        await manager.start()
        yield
        await manager.close()

    app = FastAPI(title="Sounddrop API", version="1.0.0", lifespan=lifespan)
    app.state.manager = manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
        expose_headers=["Content-Disposition"],
    )

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ConversionError)
    async def conversion_error(request: Request, exc: ConversionError):
        status = {"rate_limited": 429, "queue_full": 503, "storage_full": 503}.get(exc.code, 422)
        headers = {"Retry-After": "3600" if status == 429 else "60"} if status in {429, 503} else {}
        return JSONResponse(
            status_code=status,
            content={
                "detail": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
            headers=headers,
        )

    @app.get("/api/health")
    async def health():
        dependencies = tools_ready()
        ready = (
            all(dependencies.values())
            and manager.accepting
            and all(not task.done() for task in manager.tasks)
        )
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "unavailable",
                "tools": dependencies,
                "limits": {
                    "max_duration_seconds": settings.max_duration,
                    "file_ttl_seconds": settings.ttl,
                },
            },
        )

    @app.post("/api/jobs", status_code=202, response_model=JobStatus)
    async def submit(payload: ConversionRequest, request: Request):
        url = normalize_url(payload.url)
        if not all(tools_ready().values()):
            raise HTTPException(
                503,
                detail={
                    "code": "tools_unavailable",
                    "message": "The conversion server is not ready yet.",
                },
            )
        # Uvicorn's explicitly configured trusted proxy handling supplies request.client.
        # Never trust arbitrary X-Forwarded-For headers in application code.
        client_ip = request.client.host if request.client else "unknown"
        return manager.status(manager.submit(url, payload.bitrate, client_ip))

    def get_job(job_id: str) -> Job:
        job = manager.get(job_id)
        if not job:
            raise HTTPException(
                404,
                detail={
                    "code": "job_missing",
                    "message": "This job expired or the server restarted. Start a new conversion.",
                },
            )
        return job

    @app.get("/api/jobs/{job_id}", response_model=JobStatus)
    async def status(job_id: str):
        return manager.status(get_job(job_id))

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str):
        job = get_job(job_id)
        if job.status.stage != Stage.READY:
            raise HTTPException(
                409, detail={"code": "not_ready", "message": "The MP3 is not ready yet."}
            )
        if not job.file or not job.file.is_file():
            raise HTTPException(
                404,
                detail={"code": "job_missing", "message": "This download is no longer available."},
            )
        return JobFileResponse(job)

    return app


app = create_app()
