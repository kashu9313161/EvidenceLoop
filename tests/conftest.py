import pytest

from core import config
from ingestion import service


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "TEMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(service, "REPOSITORIES_DIR", tmp_path / "repositories")
    return tmp_path
