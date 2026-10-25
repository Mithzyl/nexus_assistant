"""The local development environment must be repeatable and non-destructive."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest

from scripts import dev_env


def _busy_port() -> tuple[socket.socket, int]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server, server.getsockname()[1]


def test_config_is_written_with_an_absolute_database_and_private_mode(tmp_path, monkeypatch):
    config_path = tmp_path / "dev.env"
    database = tmp_path / "local-development.db"
    values = dev_env.config_values(db_path=database)
    dev_env.write_config(config_path, values)

    assert oct(config_path.stat().st_mode)[-3:] == "600"
    loaded = dev_env.read_config(config_path)
    assert loaded == values
    assert loaded["ASSISTANT_DATABASE_URL"].endswith(str(database))
    assert dev_env.database_path_from_url(loaded["ASSISTANT_DATABASE_URL"]) == database
    assert loaded["ASSISTANT_COMMUTE_AUTO_AI_ENABLED"] == "false"
    assert loaded["ASSISTANT_COMMUTE_PROVIDER_MODE"] == "mock"
    assert "ASSISTANT_PROVISION_PASSWORD" not in loaded


def test_relative_database_urls_are_rejected():
    with pytest.raises(ValueError):
        dev_env.database_path_from_url("sqlite+aiosqlite:///./relative.db")


def test_inspection_does_not_create_or_modify_a_database(tmp_path):
    missing = tmp_path / "missing.db"
    report = dev_env.inspect_database(missing)
    assert report["exists"] is False
    assert not missing.exists()
    assert dev_env.blocking_schema_errors(report)


@pytest.mark.asyncio
async def test_initialised_database_satisfies_the_required_schema(tmp_path):
    from scripts.migrate_sqlite import init

    database = tmp_path / "dev.db"
    await init(f"sqlite+aiosqlite:///{database}")
    report = dev_env.inspect_database(database)
    assert report["integrity"] == "ok"
    assert report["missing_tables"] == []
    assert report["missing_columns"] == {}
    assert "0006_ai_task_purpose" in report["migrations"]
    assert dev_env.blocking_schema_errors(report) == []


def test_an_empty_database_is_reported_as_not_upgraded(tmp_path):
    database = tmp_path / "empty.db"
    connection = __import__("sqlite3").connect(database)
    connection.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    report = dev_env.inspect_database(database)
    errors = dev_env.blocking_schema_errors(report)
    assert any("缺少数据表" in error for error in errors)
    assert any("user_asks" in error for error in errors)
    assert report["missing_columns"]["nexus_runs"]


@pytest.mark.asyncio
async def test_backup_and_restore_keep_the_same_records(tmp_path, monkeypatch):
    from scripts.migrate_sqlite import init

    monkeypatch.setattr(dev_env, "BACKUP_DIR", tmp_path / "backups")
    database = tmp_path / "dev.db"
    await init(f"sqlite+aiosqlite:///{database}")
    connection = __import__("sqlite3").connect(database)
    now = "2026-10-25 10:00:00"
    connection.execute(
        "INSERT INTO users (user_id,email,display_name,password_verifier,status,created_at,updated_at)"
        " VALUES ('u1','dev@example.com','开发','x','active',?,?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO nexus_runs (local_run_id,user_id,status,idempotency_key,last_event_sequence,"
        "request_text,created_at,updated_at,purpose,trigger) VALUES "
        "('run-1','u1','succeeded','key-0001',0,'你好',?,?,'chat','chat')",
        (now, now),
    )
    connection.execute(
        "INSERT INTO user_asks (ask_id,user_id,local_run_id,request_text,created_at,updated_at)"
        " VALUES ('ask-1','u1','run-1','你好',?,?)",
        (now, now),
    )
    connection.commit()
    connection.close()

    destination = tmp_path / "backups" / "snapshot.db"
    dev_env.backup_database(database, destination)
    report = dev_env.verify_backup(destination)
    assert report["integrity"] == "ok"
    assert report["counts"]["users"] == 1
    assert report["counts"]["user_asks"] == 1
    assert dev_env.schema_errors(report) == []

    comparison = dev_env.restore_check(destination, database)
    assert comparison["counts_match"] is True
    assert comparison["ids_match"] is True
    assert comparison["sample_ids"]["asks"] == ["run-1"]


@pytest.mark.asyncio
async def test_restore_replaces_the_database_only_with_yes(tmp_path, monkeypatch):
    from scripts.migrate_sqlite import init

    monkeypatch.setattr(dev_env, "BACKUP_DIR", tmp_path / "backups")
    database = tmp_path / "dev.db"
    await init(f"sqlite+aiosqlite:///{database}")
    config = tmp_path / "dev.env"
    dev_env.write_config(config, dev_env.config_values(db_path=database))
    monkeypatch.setattr(dev_env, "CONFIG_PATH", config)
    monkeypatch.setattr(dev_env, "running_processes", lambda: {})

    snapshot = tmp_path / "snapshot.db"
    dev_env.backup_database(database, snapshot)

    refused = dev_env.main(["restore", str(snapshot)])
    assert refused == 2
    assert dev_env.main(["restore", str(snapshot), "--yes"]) == 0


def test_port_probe_and_process_matching():
    server, port = _busy_port()
    try:
        assert dev_env.port_in_use(port) is True
    finally:
        server.close()
    assert dev_env.port_in_use(port) is False
    assert dev_env.command_matches(
        "api", "python -m uvicorn assistant_api.main:app --port 8093"
    )
    assert not dev_env.command_matches("api", "python -m http.server 8093")
    assert dev_env.command_matches("worker", "python -m assistant_api.commute.worker")


def test_pid_file_roundtrip_and_stale_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_env, "RUN_DIR", tmp_path / "run")
    dev_env.write_pid("api", {"name": "api", "pid": 999999, "port": 8093})
    record = dev_env.read_pid("api")
    assert record is not None and record["pid"] == 999999
    assert dev_env.pid_alive(999999) is False
    assert dev_env.running_processes() == {}


def test_heartbeat_states_are_explicit():
    now = datetime.now(UTC)
    assert dev_env.heartbeat_state(None, 60) == "not_seen"
    assert dev_env.heartbeat_state(now.isoformat(), 60) == "online"
    assert dev_env.heartbeat_state((now - timedelta(seconds=120)).isoformat(), 60) == "stale"


@pytest.mark.asyncio
async def test_start_refuses_a_busy_port_without_spawning(tmp_path, monkeypatch):
    from scripts.migrate_sqlite import init

    database = tmp_path / "dev.db"
    await init(f"sqlite+aiosqlite:///{database}")
    values = dev_env.config_values(db_path=database, api_port=18093, stub_port=18191)
    config_path = tmp_path / "dev.env"
    dev_env.write_config(config_path, values)

    monkeypatch.setattr(dev_env, "CONFIG_PATH", config_path)
    monkeypatch.setattr(dev_env, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(dev_env, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(dev_env, "running_processes", lambda: {})
    monkeypatch.setattr(dev_env, "port_in_use", lambda port: port == 18093)
    monkeypatch.setattr(
        dev_env, "subprocess", __import__("subprocess")
    )
    monkeypatch.setattr(
        dev_env.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn")
    )

    assert dev_env.main(["start", "--components", "api"]) == 1
    assert not (tmp_path / "run").exists() or list((tmp_path / "run").iterdir()) == []

@pytest.mark.asyncio
async def test_start_reports_the_component_that_fails(tmp_path, monkeypatch, capsys):
    from scripts.migrate_sqlite import init

    database = tmp_path / "dev.db"
    await init(f"sqlite+aiosqlite:///{database}")
    config_path = tmp_path / "dev.env"
    dev_env.write_config(
        config_path,
        dev_env.config_values(db_path=database, api_port=18093, stub_port=18191),
    )
    monkeypatch.setattr(dev_env, "CONFIG_PATH", config_path)
    monkeypatch.setattr(dev_env, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(dev_env, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(dev_env, "running_processes", lambda: {})
    monkeypatch.setattr(dev_env, "port_in_use", lambda port: False)
    monkeypatch.setattr(dev_env.time, "sleep", lambda seconds: None)

    class FakeProcess:
        pid = 999_999

        def poll(self):
            return 1  # already exited: the component failed to stay up

    monkeypatch.setattr(dev_env.subprocess, "Popen", lambda *a, **k: FakeProcess())

    result = dev_env.main(["start", "--components", "worker"])
    output = capsys.readouterr().out
    assert result == 1
    assert "start failed: worker" in output
    assert "stopped partially started components: worker(already exited)" in output
    assert not (tmp_path / "run" / "worker.json").exists()
