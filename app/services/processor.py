"""Job creation and processing.

Creation (synchronous, fast): validate each recipient and persist one row per
recipient. Invalid ones are stored as FAILED immediately.

Processing (background): render each PENDING certificate, committing after every
one so progress is visible and a crash loses at most one item.
"""
import logging
import os
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate, Recipient
from app.services import generator

log = logging.getLogger(__name__)


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'recipient'}: {e['msg']}" for e in exc.errors()
    )


def create_job(db: Session, payload: JobCreate) -> Job:
    job = Job(
        course_title=payload.course_title.strip(),
        issued_by=payload.issued_by.strip(),
        issue_date=payload.issue_date,
    )
    for position, raw in enumerate(payload.recipients):
        cert = Certificate(position=position, recipient=raw)
        try:
            clean = Recipient.model_validate(raw)
            cert.recipient = clean.model_dump()
        except ValidationError as exc:
            cert.status = CertificateStatus.FAILED
            cert.error = f"validation: {_format_validation_error(exc)}"
        job.certificates.append(cert)

    if all(c.status == CertificateStatus.FAILED for c in job.certificates):
        job.status = JobStatus.FAILED
        job.completed_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    return job


def _finalize(db: Session, job: Job) -> None:
    statuses = db.scalars(select(Certificate.status).where(Certificate.job_id == job.id)).all()
    failed = sum(s == CertificateStatus.FAILED for s in statuses)
    if failed == 0:
        job.status = JobStatus.COMPLETED
    elif failed == len(statuses):
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    job.completed_at = datetime.now(timezone.utc)
    db.commit()


def process_job(job_id: str, session_factory: sessionmaker, storage_dir: str) -> None:
    """Generate all PENDING certificates of a job. Safe to re-run (idempotent)."""
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None or job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.COMPLETED_WITH_ERRORS):
            return
        job.status = JobStatus.PROCESSING
        db.commit()

        out_dir = os.path.join(storage_dir, job.id)
        os.makedirs(out_dir, exist_ok=True)

        pending_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.position)
        ).all()

        for cert_id in pending_ids:
            cert = db.get(Certificate, cert_id)
            try:
                pdf = generator.render_certificate(
                    name=cert.recipient["name"],
                    course_title=job.course_title,
                    issued_by=job.issued_by,
                    issue_date=job.issue_date,
                )
                path = os.path.join(out_dir, f"{cert.id}.pdf")
                with open(path, "wb") as f:
                    f.write(pdf)
                cert.file_path = path
                cert.status = CertificateStatus.SUCCEEDED
            except Exception as exc:  # isolate: one bad item must not stop the job
                log.exception("certificate %s failed", cert_id)
                cert.status = CertificateStatus.FAILED
                cert.error = f"generation: {exc}"
            db.commit()

        _finalize(db, job)


def recover_unfinished_jobs(session_factory: sessionmaker, storage_dir: str) -> int:
    """On startup, resume jobs interrupted by a restart. Returns how many were resumed."""
    with session_factory() as db:
        ids = db.scalars(
            select(Job.id).where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
        ).all()
    for job_id in ids:
        process_job(job_id, session_factory, storage_dir)
    return len(ids)
