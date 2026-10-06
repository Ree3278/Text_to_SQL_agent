import pytest

from app.config import DB_PATH


@pytest.fixture(scope="session", autouse=True)
def _ensure_db():
    """Tests run against the real (synthetic) database; build it if it's missing."""
    if not DB_PATH.exists():
        from data import generate

        generate.build()
