import pytest

from app import config
from app.ingest.build import build


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "uzex.duckdb"
    build(config.DATA_DIR, config.DICT_DIR, path)
    return path
