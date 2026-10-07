import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import jobs
from app.services import processor


def create_app(
    session_factory=SessionLocal,
    db_engine=engine,
    storage_dir: str = settings.storage_dir,
    max_recipients: int = settings.max_recipients_per_job,
    resume_unfinished: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(db_engine)
        if resume_unfinished:
            # Background thread so a large backlog doesn't delay startup.
            threading.Thread(
                target=processor.recover_unfinished_jobs, args=(session_factory, storage_dir), daemon=True
            ).start()
        yield

    app = FastAPI(title="Bulk Certificate Generator", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.storage_dir = storage_dir
    app.state.max_recipients = max_recipients
    app.include_router(jobs.router)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()
