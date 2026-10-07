from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import CertificateStatus, JobStatus


class Recipient(BaseModel):
    """Validated recipient. Applied per item so one bad row doesn't reject the batch."""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("name must not be blank")
        return v


class JobCreate(BaseModel):
    course_title: str = Field(min_length=1, max_length=200)
    issued_by: str = Field(min_length=1, max_length=200)
    issue_date: date = Field(default_factory=date.today)
    # Deliberately loose: each item is validated individually (see Recipient)
    # so invalid rows become per-certificate failures instead of a 422 for all.
    recipients: list[dict] = Field(min_length=1)


class CertificateOut(BaseModel):
    id: str
    position: int
    status: CertificateStatus
    recipient: dict
    error: str | None
    download_url: str | None


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]


class JobProgress(BaseModel):
    total: int
    pending: int
    succeeded: int
    failed: int
    percent_complete: float


class JobOut(BaseModel):
    id: str
    status: JobStatus
    course_title: str
    issued_by: str
    issue_date: date
    created_at: datetime
    completed_at: datetime | None
    progress: JobProgress
    # Only failed items are inlined; successes are listed via /certificates.
    failures: list[CertificateOut]
    download_all_url: str | None
