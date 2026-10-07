# Bulk Certificate Generator

A FastAPI backend that accepts one request containing many recipients, generates a PDF certificate for each from a single predefined template, tracks progress per job, and lets clients download certificates individually or as a ZIP.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default; any SQLAlchemy URL works, e.g. PostgreSQL), ReportLab (PDF), pytest.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs. Tables are created on startup.

Configuration (environment variables, all optional):

| Variable | Default | Meaning |
|---|---|---|
| `CERTGEN_DATABASE_URL` | `sqlite:///./certificates.db` | SQLAlchemy URL |
| `CERTGEN_STORAGE_DIR` | `./storage` | Where PDFs are written |
| `CERTGEN_MAX_RECIPIENTS_PER_JOB` | `5000` | Per-request cap |

## Test

```bash
pytest
```

## API

### Submit a job — `POST /jobs`

```bash
curl -X POST localhost:8000/jobs -H 'content-type: application/json' -d '{
  "course_title": "Intro to Python",
  "issued_by": "Acme Academy",
  "issue_date": "2026-01-15",
  "recipients": [
    {"name": "Ada Lovelace", "email": "ada@example.com"},
    {"name": "Alan Turing",  "email": "alan@example.com"}
  ]
}'
```

Returns `202 Accepted` with the job (including its `id`). `issue_date` is optional (defaults to today). Each recipient needs a non-blank `name` (≤100 chars) and a valid `email`.

### Check progress — `GET /jobs/{id}`

```json
{
  "id": "…",
  "status": "completed_with_errors",
  "progress": {"total": 3, "pending": 0, "succeeded": 2, "failed": 1, "percent_complete": 100.0},
  "failures": [{"position": 2, "recipient": {"name": "Bad"}, "error": "validation: email: Field required", "...": "..."}],
  "download_all_url": "/jobs/{id}/download"
}
```

Job `status`: `pending` → `processing` → `completed` (all succeeded) | `completed_with_errors` | `failed` (all failed). Poll until `percent_complete` is 100. `failures` lists every failed recipient with its position in the submitted array and the reason (`validation:` or `generation:`).

### Retrieve certificates

| Endpoint | Returns |
|---|---|
| `GET /jobs/{id}/certificates?status=succeeded&limit=100&offset=0` | Paginated list, each with a `download_url` when ready |
| `GET /jobs/{id}/certificates/{cert_id}/download` | One PDF (`409` if that certificate failed / isn't ready) |
| `GET /jobs/{id}/download` | ZIP of all successfully generated PDFs so far (`409` if none) |

```bash
curl -OJ localhost:8000/jobs/<id>/download
```

## Design decisions

**Background processing, not synchronous.** Rendering thousands of PDFs would hold an HTTP request open for a long time and risk client/proxy timeouts. `POST /jobs` instead validates and persists the batch, returns `202` immediately, and generation runs in a FastAPI `BackgroundTask`; clients poll `GET /jobs/{id}`. I chose this over Celery/RQ to keep the project runnable with zero extra infrastructure. The trade-off: work runs inside the web process (CPU-bound rendering shares it) and is not distributed. `process_job` is a plain function taking a job id, so moving to a real queue is a one-line change at the call site.

**Restart safety.** Progress is committed per certificate and `process_job` only picks up `pending` rows, so it is idempotent. On startup, unfinished jobs (`pending`/`processing`) are resumed in a background thread. Limitation: with multiple server processes, each would try to resume the same jobs; a real queue or a job lease/lock would be needed there.

**Per-recipient failure isolation.** A request-level problem (no recipients, missing course title, more than the cap) is a `422` for the whole request. A *recipient-level* problem (blank name, bad email) does **not** reject the batch: that row is stored as `failed` with the reason, and the valid rows proceed. During generation, each certificate is wrapped in its own try/except and committed independently. Every submitted recipient has a row, so `succeeded + failed + pending == total` always.

**Data model.** `jobs` (shared course/issuer/date, status, timestamps) and `certificates` (one per recipient: position, raw recipient JSON, status, error, file path). Progress counts are derived from certificate rows rather than stored counters, so they can't drift.

**Template.** One hard-coded landscape A4 PDF layout in `app/services/generator.py` (name, course, issuer, date). Long names/titles shrink to fit.

**Known limitation – fonts.** The template uses ReportLab's built-in Helvetica (Latin-1 only). Names with other scripts (e.g. Devanagari, CJK) are reported as a `generation:` failure instead of silently producing a broken PDF. Supporting them means bundling a Unicode TTF and registering it with ReportLab.

**Storage.** PDFs are on local disk under `storage/<job_id>/<cert_id>.pdf`, with paths in the DB. Swapping to S3 would only touch `processor.py` and the download endpoints.

**Not implemented (out of scope):** authentication, idempotency keys, rate limiting, email delivery, database migrations (tables are created with `create_all`).

## Layout

```
app/
  main.py            app factory, startup recovery
  config.py          env-based settings
  database.py        engine / session
  models.py          Job, Certificate
  schemas.py         request/response + per-recipient validation
  routers/jobs.py    endpoints
  services/
    generator.py     PDF template rendering
    processor.py     job creation + background processing
tests/               pytest suite (creation, validation, generation, status, failures, retrieval)
```
