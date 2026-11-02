"""A green CI result must not hide unavailable database integration tests."""
from types import SimpleNamespace

import pytest

from tests.integration import conftest as integration


@pytest.mark.parametrize("missing", ["schema", "docker", "image"])
@pytest.mark.parametrize("required", [False, True])
def test_missing_integration_prerequisites_fail_when_required(monkeypatch, tmp_path, missing, required):
    schema = tmp_path / "schema.sql"
    if missing != "schema":
        schema.touch()
    monkeypatch.setattr(integration, "SCHEMA", schema)
    monkeypatch.setattr(integration.shutil, "which", lambda name: None if missing == "docker" else "/test/docker")
    monkeypatch.setattr(integration, "docker", lambda *args: SimpleNamespace(
        returncode=1 if args[0] == "image" and missing == "image" else 0))
    options = {"--require-integration": required, "--mysql-backend": "docker"}
    config = SimpleNamespace(getoption=options.__getitem__)

    fixture = integration.mysql_server.__wrapped__(config)
    outcome = pytest.fail.Exception if required else pytest.skip.Exception
    with pytest.raises(outcome):
        next(fixture)
