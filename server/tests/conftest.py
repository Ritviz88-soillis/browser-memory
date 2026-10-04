"""Point the tests at a throwaway database BEFORE config is imported, so they
never touch the real memory file."""

import os
import tempfile
from pathlib import Path

import pytest

_TEST_DATABASE = Path(tempfile.mkdtemp(prefix="browser-memory-tests-")) / "memory.db"
os.environ["MEMORY_DB_PATH"] = str(_TEST_DATABASE)


@pytest.fixture
def memory():
    """A fresh, empty database for one test."""

    import db

    db.close()
    for leftover in _TEST_DATABASE.parent.glob("memory.db*"):
        leftover.unlink()
    db.init()
    yield db
    db.close()
