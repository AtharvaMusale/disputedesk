from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from disputedesk.config import settings
from disputedesk.rag import index
from disputedesk.tools.app import create_app

needs_db = pytest.mark.skipif(not Path(settings.duckdb_path).exists(), reason="run make load && make synth")


@pytest.fixture(scope="session")
def chroma_path(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("chroma"))
    index.build(chroma_path=path)
    return path


@pytest.fixture(scope="module")
def client(tmp_path_factory, chroma_path):
    actions = str(tmp_path_factory.mktemp("actions") / "actions.db")
    with TestClient(create_app(actions_path=actions, chroma_path=chroma_path)) as c:
        yield c


@pytest.fixture(scope="module")
def db():
    con = duckdb.connect(settings.duckdb_path, read_only=True)
    yield con
    con.close()
