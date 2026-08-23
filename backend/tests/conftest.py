"""Test harness. Env is configured before any app import so settings/engine bind to the sandbox."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="dyb-pro-tests-"))

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite+pysqlite:///{(TMP / 'test.db').as_posix()}",
        "LOCAL_ARTIFACT_DIR": str(TMP / "artifacts"),
        "CELERY_TASK_ALWAYS_EAGER": "true",
        "ALLOW_LOCAL_SIMULATION": "true",
        "DEVIN_POLL_INTERVAL_SECONDS": "0",
        # Literature passes stay offline: the suite must not depend on OpenAlex/Semantic Scholar
        # being reachable (or on their rate limits) to be deterministic.
        "DAEMON_LITERATURE_NETWORK": "false",
        "SEED_ADMIN_API_KEY": "test-admin",
        "SEED_SCIENTIST_API_KEY": "test-scientist",
        "SEED_VIEWER_API_KEY": "test-viewer",
    }
)
# Tests must never touch credentials that may exist in a developer's backend/.env file. Explicit
# empty environment values override pydantic-settings' dotenv fallback; merely popping them would
# allow the secrets to be loaded again from disk.
os.environ["DEVIN_API_KEY"] = ""
os.environ["DEVIN_ORG_ID"] = ""
os.environ["OPENAI_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Project  # noqa: E402
from app.seed import seed_all  # noqa: E402
from app.versioning import commit_design, get_branch  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def project(db):
    proj = Project(name="test project", goal="raise stability", target_sequence="")
    db.add(proj)
    db.flush()
    get_branch(db, proj.id, "main", create=True)
    db.commit()
    return proj


@pytest.fixture
def root_commit(db, project):
    commit = commit_design(
        db,
        project_id=project.id,
        sequence="MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE",
        message="root",
        label="wt",
        provider="upload",
    )
    db.commit()
    return commit


@pytest.fixture(scope="session")
def client(_schema):
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def demo_project_id(client):
    session = SessionLocal()
    try:
        proj = seed_all(session)
        session.commit()
        return proj.id
    finally:
        session.close()


def headers(role: str = "scientist") -> dict:
    return {"X-API-Key": f"test-{role}"}
