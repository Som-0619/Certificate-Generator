import io
import zipfile

from pypdf import PdfReader

from app.models import Job, JobStatus
from app.services import generator, processor
from tests.conftest import make_payload

GOOD = [{"name": "Ada Lovelace", "email": "ada@example.com"}, {"name": "Alan Turing", "email": "alan@example.com"}]


def pdf_text(data: bytes) -> str:
    return PdfReader(io.BytesIO(data)).pages[0].extract_text()


def submit(client, payload):
    """POST a job, then fetch its final state (TestClient runs background work before returning)."""
    job_id = client.post("/jobs", json=payload).json()["id"]
    return client.get(f"/jobs/{job_id}").json()


# --- creating a job -------------------------------------------------------
def test_create_job_returns_202_and_job_id(client):
    r = client.post("/jobs", json=make_payload(GOOD))
    assert r.status_code == 202
    body = r.json()
    assert body["id"] and body["progress"]["total"] == 2
    assert body["course_title"] == "Python 101"


# --- input validation -----------------------------------------------------
def test_empty_recipient_list_rejected(client):
    assert client.post("/jobs", json=make_payload([])).status_code == 422


def test_missing_job_fields_rejected(client):
    assert client.post("/jobs", json={"recipients": GOOD}).status_code == 422


def test_too_many_recipients_rejected(client):
    many = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(51)]
    r = client.post("/jobs", json=make_payload(many))
    assert r.status_code == 422 and "Too many" in r.json()["detail"]


def test_invalid_recipients_fail_individually_not_whole_request(client):
    recipients = GOOD + [
        {"name": "   ", "email": "blank@example.com"},
        {"name": "No Email"},
        {"name": "Bad Email", "email": "not-an-email"},
    ]
    body = submit(client, make_payload(recipients))
    assert body["status"] == "completed_with_errors"
    assert body["progress"] == {"total": 5, "pending": 0, "succeeded": 2, "failed": 3, "percent_complete": 100.0}
    assert [f["position"] for f in body["failures"]] == [2, 3, 4]
    assert all(f["error"].startswith("validation:") for f in body["failures"])


def test_all_invalid_marks_job_failed(client):
    body = submit(client, make_payload([{"name": "x"}]))
    assert body["status"] == "failed" and body["download_all_url"] is None


# --- certificate generation ----------------------------------------------
def test_generated_pdf_contains_recipient_and_job_info():
    data = generator.render_certificate(
        name="Ada Lovelace", course_title="Python 101", issued_by="Acme Academy", issue_date=__import__("datetime").date(2026, 1, 15)
    )
    assert data.startswith(b"%PDF")
    text = pdf_text(data)
    for expected in ("Ada Lovelace", "Python 101", "Acme Academy", "15 January 2026"):
        assert expected in text


def test_unrenderable_characters_raise():
    import datetime

    try:
        generator.render_certificate(name="अदा", course_title="c", issued_by="i", issue_date=datetime.date.today())
    except generator.CertificateGenerationError:
        return
    raise AssertionError("expected CertificateGenerationError")


# --- job status / progress ------------------------------------------------
def test_job_status_completed_and_files_written(ctx):
    job_id = ctx.client.post("/jobs", json=make_payload(GOOD)).json()["id"]
    body = ctx.client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["progress"]["succeeded"] == 2 and body["progress"]["percent_complete"] == 100.0
    assert body["completed_at"] and body["failures"] == []
    assert len(list((ctx.storage / job_id).glob("*.pdf"))) == 2


def test_unknown_job_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_progress_while_pending(ctx):
    """A job that has been created but not processed reports pending items."""
    with ctx.factory() as db:
        from app.schemas import JobCreate

        job = processor.create_job(db, JobCreate(**make_payload(GOOD)))
    body = ctx.client.get(f"/jobs/{job.id}").json()
    assert body["status"] == "pending"
    assert body["progress"]["pending"] == 2 and body["progress"]["percent_complete"] == 0.0


# --- individual failure handling -----------------------------------------
def test_one_generation_failure_does_not_stop_others(ctx, monkeypatch):
    real = generator.render_certificate

    def flaky(**kw):
        if kw["name"] == "Boom":
            raise RuntimeError("renderer exploded")
        return real(**kw)

    monkeypatch.setattr(generator, "render_certificate", flaky)
    recipients = [GOOD[0], {"name": "Boom", "email": "boom@example.com"}, GOOD[1]]
    body = submit(ctx.client, make_payload(recipients))
    assert body["status"] == "completed_with_errors"
    assert body["progress"]["succeeded"] == 2 and body["progress"]["failed"] == 1
    assert body["failures"][0]["position"] == 1
    assert "renderer exploded" in body["failures"][0]["error"]


def test_non_latin_name_reported_as_generation_failure(client):
    recipients = GOOD + [{"name": "अदा", "email": "ada@example.com"}]
    body = submit(client, make_payload(recipients))
    assert body["progress"]["succeeded"] == 2
    assert body["failures"][0]["error"].startswith("generation:")


def test_reprocessing_is_idempotent_and_resumes(ctx):
    with ctx.factory() as db:
        from app.schemas import JobCreate

        job_id = processor.create_job(db, JobCreate(**make_payload(GOOD))).id
    assert processor.recover_unfinished_jobs(ctx.factory, str(ctx.storage)) == 1
    processor.process_job(job_id, ctx.factory, str(ctx.storage))  # no-op second time
    assert ctx.client.get(f"/jobs/{job_id}").json()["status"] == "completed"
    assert len(list((ctx.storage / job_id).glob("*.pdf"))) == 2


# --- retrieving certificates ---------------------------------------------
def test_list_and_download_single_certificate(client):
    job_id = client.post("/jobs", json=make_payload(GOOD + [{"name": "x"}])).json()["id"]
    listing = client.get(f"/jobs/{job_id}/certificates").json()
    assert listing["total"] == 3
    ok = [c for c in listing["items"] if c["status"] == "succeeded"]
    assert len(ok) == 2
    r = client.get(ok[0]["download_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "Ada Lovelace" in pdf_text(r.content)


def test_list_filter_and_pagination(client):
    job_id = client.post("/jobs", json=make_payload(GOOD + [{"name": "x"}])).json()["id"]
    failed = client.get(f"/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    assert failed["total"] == 1
    page = client.get(f"/jobs/{job_id}/certificates", params={"limit": 1, "offset": 1}).json()
    assert [c["position"] for c in page["items"]] == [1]


def test_download_failed_certificate_conflict(client):
    job_id = client.post("/jobs", json=make_payload(GOOD + [{"name": "x"}])).json()["id"]
    failed = client.get(f"/jobs/{job_id}").json()["failures"][0]
    assert failed["download_url"] is None
    assert client.get(f"/jobs/{job_id}/certificates/{failed['id']}/download").status_code == 409


def test_download_certificate_wrong_job_404(client):
    a = client.post("/jobs", json=make_payload(GOOD)).json()["id"]
    b = client.post("/jobs", json=make_payload(GOOD)).json()["id"]
    cert_id = client.get(f"/jobs/{a}/certificates").json()["items"][0]["id"]
    assert client.get(f"/jobs/{b}/certificates/{cert_id}/download").status_code == 404


def test_download_zip_contains_only_successes(client):
    job_id = client.post("/jobs", json=make_payload(GOOD + [{"name": "x"}])).json()["id"]
    r = client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["0001_Ada_Lovelace.pdf", "0002_Alan_Turing.pdf"]


def test_zip_409_when_nothing_generated(client):
    job_id = client.post("/jobs", json=make_payload([{"name": "x"}])).json()["id"]
    assert client.get(f"/jobs/{job_id}/download").status_code == 409
