import pytest
import sqlalchemy as sa

from stocker.config import settings


@pytest.fixture(scope="session")
def engine():
    eng = sa.create_engine(settings.db_url)
    try:
        with eng.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"DB not reachable at STOCKER_DB_URL: {exc.__class__.__name__}")
    return eng
