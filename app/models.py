from __future__ import annotations

from pydantic import BaseModel, Field


class JobStatus(BaseModel):
    job_id: str
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    output_file: str | None = None
    output_url: str | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    job_id: str
    status: str = "queued"
    progress: int = 0
    message: str = "Upload accepted"


class ProcessingResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str
    output_url: str | None = None
    error: str | None = None
