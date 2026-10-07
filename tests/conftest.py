import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database import make_engine
from app.main import create_app


@pytest.fixture
def ctx(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app = create_app(
        session_factory=factory,
        db_engine=engine,
        storage_dir=str(tmp_path / "storage"),
        max_recipients=50,
        resume_unfinished=False,
    )
    # TestClient runs BackgroundTasks before returning, so jobs are finished
    # by the time the POST response is received.
    with TestClient(app) as client:
        yield type("Ctx", (), {"client": client, "factory": factory, "storage": tmp_path / "storage"})


@pytest.fixture
def client(ctx):
    return ctx.client


def make_payload(recipients, **over):
    body = {"course_title": "Python 101", "issued_by": "Acme Academy", "issue_date": "2026-01-15", "recipients": recipients}
    body.update(over)
    return body
