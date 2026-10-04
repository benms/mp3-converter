from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Stage(StrEnum):
    QUEUED = "queued"
    INSPECTING = "inspecting"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    READY = "ready"
    FAILED = "failed"


class ConversionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    bitrate: Literal[128, 192, 320] = 192


class ErrorDetail(BaseModel):
    code: str
    message: str


class JobStatus(BaseModel):
    id: str
    stage: Stage
    bitrate: int
    progress: float | None = None
    title: str | None = None
    duration: float | None = None
    queue_position: int | None = None
    created_at: datetime
    expires_at: datetime | None = None
    error: ErrorDetail | None = None


class ConversionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
