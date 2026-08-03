import os
import tempfile

database_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
database_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{database_file.name}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mtglogger.database import Base, engine  # noqa: E402
from mtglogger.main import app  # noqa: E402


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield TestClient(app)
