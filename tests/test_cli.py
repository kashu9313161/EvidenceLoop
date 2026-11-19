"""Command line: serve's start-up checks."""

import socket

import pytest

from cli import main as cli_main


def test_a_busy_port_gives_a_clear_message(monkeypatch):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert cli_main.port_in_use("127.0.0.1", port)
        monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("should not start"))
        with pytest.raises(SystemExit) as exit_info:
            cli_main.main(["serve", "--port", str(port)])
    assert "already in use" in str(exit_info.value)
    assert f"--port {port + 1}" in str(exit_info.value)


def test_serving_on_a_named_host_allows_that_name(monkeypatch, capsys):
    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: started.update(host=host))
    monkeypatch.setattr(cli_main, "port_in_use", lambda host, port: False)
    monkeypatch.delenv("EVIDENCELOOP_ALLOWED_HOSTS", raising=False)
    cli_main.main(["serve", "--host", "192.168.1.20", "--port", "8123"])
    assert started == {"host": "192.168.1.20"}
    import os
    assert os.environ.pop("EVIDENCELOOP_ALLOWED_HOSTS") == "192.168.1.20"
    output = capsys.readouterr()
    assert "no login" in output.err
    assert "http://192.168.1.20:8123" in output.out
