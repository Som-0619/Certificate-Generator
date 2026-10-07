import io
import os
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateList, CertificateOut, JobCreate, JobOut, JobProgress
from app.services import processor

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _cert_out(cert: Certificate) -> CertificateOut:
    ok = cert.status == CertificateStatus.SUCCEEDED
    return CertificateOut(
        id=cert.id,
        position=cert.position,
        status=cert.status,
        recipient=cert.recipient,
        error=cert.error,
        download_url=f"/jobs/{cert.job_id}/certificates/{cert.id}/download" if ok else None,
    )


def _job_out(db: Session, job: Job) -> JobOut:
    counts = dict(
        db.execute(
            select(Certificate.status, func.count()).where(Certificate.job_id == job.id).group_by(Certificate.status)
        ).all()
    )
    succeeded = counts.get(CertificateStatus.SUCCEEDED, 0)
    failed = counts.get(CertificateStatus.FAILED, 0)
    pending = counts.get(CertificateStatus.PENDING, 0)
    total = succeeded + failed + pending
    failures = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED)
        .order_by(Certificate.position)
    ).all()
    return JobOut(
        id=job.id,
        status=job.status,
        course_title=job.course_title,
        issued_by=job.issued_by,
        issue_date=job.issue_date,
        created_at=job.created_at,
        completed_at=job.completed_at,
        progress=JobProgress(
            total=total,
            pending=pending,
            succeeded=succeeded,
            failed=failed,
            percent_complete=round(100 * (succeeded + failed) / total, 1) if total else 100.0,
        ),
        failures=[_cert_out(c) for c in failures],
        download_all_url=f"/jobs/{job.id}/download" if succeeded else None,
    )


@router.post("", response_model=JobOut, status_code=202)
def create_job(
    payload: JobCreate,
    background: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    max_n = request.app.state.max_recipients
    if len(payload.recipients) > max_n:
        raise HTTPException(422, f"Too many recipients: {len(payload.recipients)} (max {max_n} per job)")
    job = processor.create_job(db, payload)
    if job.status == JobStatus.PENDING:
        background.add_task(
            processor.process_job, job.id, request.app.state.session_factory, request.app.state.storage_dir
        )
    return _job_out(db, job)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    return _job_out(db, _get_job(db, job_id))


@router.get("/{job_id}/certificates", response_model=CertificateList)
def list_certificates(
    job_id: str,
    status: CertificateStatus | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_job(db, job_id)
    q = select(Certificate).where(Certificate.job_id == job_id)
    if status:
        q = q.where(Certificate.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(Certificate.position).limit(limit).offset(offset)).all()
    return CertificateList(total=total, limit=limit, offset=offset, items=[_cert_out(c) for c in items])


@router.get("/{job_id}/certificates/{certificate_id}/download")
def download_certificate(job_id: str, certificate_id: str, db: Session = Depends(get_db)):
    cert = db.get(Certificate, certificate_id)
    if cert is None or cert.job_id != job_id:
        raise HTTPException(404, "Certificate not found")
    if cert.status != CertificateStatus.SUCCEEDED or not cert.file_path or not os.path.exists(cert.file_path):
        raise HTTPException(409, f"Certificate is not available (status: {cert.status.value})")
    safe = "".join(ch if ch.isalnum() else "_" for ch in cert.recipient["name"])
    return FileResponse(cert.file_path, media_type="application/pdf", filename=f"certificate_{safe}.pdf")


@router.get("/{job_id}/download")
def download_all(job_id: str, db: Session = Depends(get_db)):
    """ZIP of every successfully generated certificate (available even mid-job)."""
    _get_job(db, job_id)
    certs = db.scalars(
        select(Certificate).where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCEEDED)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(409, "No certificates have been generated for this job yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
        for c in certs:
            safe = "".join(ch if ch.isalnum() else "_" for ch in c.recipient["name"])
            zf.write(c.file_path, f"{c.position + 1:04d}_{safe}.pdf")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job_id}.zip"'},
    )
