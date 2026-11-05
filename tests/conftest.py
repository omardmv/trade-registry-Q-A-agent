import pytest

from app import config
from app.ingest.build import build


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "uzex.duckdb"
    build(config.DATA_DIR, config.DICT_DIR, path)
    return path


from app.db import Database


@pytest.fixture(scope="session")
def db(db_path):
    return Database(db_path)


@pytest.fixture(autouse=True)
def _balanced_safety_by_default(monkeypatch, request):
    """Most tests script the planner without judge replies: run them in balanced mode. Tests of max safety mark
    themselves with @pytest.mark.max_safety."""
    from app import config
    if "max_safety" not in request.keywords:
        monkeypatch.setattr(config, "SAFETY", "balanced")
