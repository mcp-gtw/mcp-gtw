from __future__ import annotations

import sys
import types

import pytest
from fastapi import FastAPI


def test_main_exposes_app() -> None:
    import mcp_gtw.main as gateway_main

    assert isinstance(gateway_main.app, FastAPI)


def test_run_configures_uvicorn_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_gtw.main as gateway_main

    captured: dict = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(app=app, **kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=fake_run))

    gateway_main.run()

    settings = gateway_main.gateway.settings
    assert captured["app"] is gateway_main.app
    assert captured["host"] == settings.host
    assert captured["port"] == settings.port
    assert captured["ws_max_size"] == settings.maximum_websocket_message_bytes
    assert captured["limit_concurrency"] == settings.maximum_concurrent_connections
    assert captured["proxy_headers"] is True
