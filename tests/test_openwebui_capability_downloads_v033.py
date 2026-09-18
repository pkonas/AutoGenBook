from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import mimetypes
import os
import re
import secrets
import sys
import threading
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, AsyncIterator
from urllib.parse import quote, urlparse

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "tools" / "apply_openwebui_capability_downloads_v033.py"


def load_patcher():
    spec = importlib.util.spec_from_file_location("autogenbook_v033_patcher_test", PATCHER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_fake_openwebui(monkeypatch: pytest.MonkeyPatch, record: Any) -> None:
    open_webui = ModuleType("open_webui")
    models = ModuleType("open_webui.models")
    files_module = ModuleType("open_webui.models.files")
    storage_package = ModuleType("open_webui.storage")
    storage_module = ModuleType("open_webui.storage.provider")

    class Files:
        @staticmethod
        async def get_file_by_id(file_id: str):
            return record if file_id == record.id else None

    class Storage:
        @staticmethod
        def get_file(path: str) -> str:
            return path

    files_module.Files = Files
    storage_module.Storage = Storage
    monkeypatch.setitem(sys.modules, "open_webui", open_webui)
    monkeypatch.setitem(sys.modules, "open_webui.models", models)
    monkeypatch.setitem(sys.modules, "open_webui.models.files", files_module)
    monkeypatch.setitem(sys.modules, "open_webui.storage", storage_package)
    monkeypatch.setitem(sys.modules, "open_webui.storage.provider", storage_module)


def execute_helpers(patcher: Any) -> dict[str, Any]:
    class CompanionError(RuntimeError):
        pass

    namespace: dict[str, Any] = {
        "asyncio": asyncio,
        "hashlib": hashlib,
        "hmac": hmac,
        "mimetypes": mimetypes,
        "os": os,
        "re": re,
        "secrets": secrets,
        "sys": sys,
        "threading": threading,
        "ContextVar": ContextVar,
        "Path": Path,
        "Any": Any,
        "AsyncIterator": AsyncIterator,
        "quote": quote,
        "Request": Request,
        "CompanionError": CompanionError,
    }
    exec(patcher.CAPABILITY_HELPERS, namespace)
    return namespace


def test_source_migration_removes_browser_ticket_flow() -> None:
    patcher = load_patcher()
    fixture = "# _agb_download_landing\n" + patcher._minimal_ticket_fixture()
    patched = patcher.patch_source(fixture)
    result = patcher.validate_source(patched)
    assert result["version"] == "0.3.3"
    assert "download-ticket" not in patched
    assert "_agb_download_landing" not in patched
    assert "/api/autogenbook/v033/output/" in patched


def test_direct_capability_download_without_browser_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patcher = load_patcher()
    secret_file = tmp_path / "secrets" / "download.key"
    monkeypatch.setenv("AUTOGENBOOK_OUTPUT_DOWNLOAD_SECRET_FILE", str(secret_file))

    payload = (b"AutoGenBook durable output\n" * 8192) + bytes(range(256))
    stored = tmp_path / "output with space.zip"
    stored.write_bytes(payload)
    record = SimpleNamespace(
        id="file-123",
        user_id="user-456",
        path=str(stored),
        filename="output with space.zip",
        hash=hashlib.sha256(payload).hexdigest(),
        meta={
            "name": "output with space.zip",
            "content_type": "application/zip",
            "size": len(payload),
        },
    )
    install_fake_openwebui(monkeypatch, record)
    ns = execute_helpers(patcher)

    app = FastAPI()

    @app.get("/{path:path}", name="spa-static-files")
    async def spa(path: str):
        return {"spa": path}

    assert ns["_agb_register_download_routes"](app) is True
    route_names = [str(getattr(route, "name", "")) for route in app.router.routes]
    assert route_names.index("autogenbook-capability-download-v033") < route_names.index("spa-static-files")
    assert not any("ticket-v032" in name or "landing-v032" in name for name in route_names)

    url = ns["_agb_capability_url"](
        record.id,
        record.user_id,
        record.filename,
        origin="http://testserver",
    )
    parsed = urlparse(url)
    assert parsed.path.startswith("/api/autogenbook/v033/output/")
    assert "download-ticket" not in url
    assert "Bearer" not in url
    assert "api_key" not in url.casefold()
    assert "companion" not in url.casefold()

    client = TestClient(app)
    response = client.get(parsed.path + "?" + parsed.query)
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-disposition"].startswith("attachment;")

    head = client.head(parsed.path + "?" + parsed.query)
    assert head.status_code == 200
    assert head.content == b""
    assert int(head.headers["content-length"]) == len(payload)

    ranged = client.get(
        parsed.path + "?" + parsed.query,
        headers={"Range": "bytes=17-116"},
    )
    assert ranged.status_code == 206
    assert ranged.content == payload[17:117]
    assert ranged.headers["content-range"] == f"bytes 17-116/{len(payload)}"

    suffix = client.get(
        parsed.path + "?" + parsed.query,
        headers={"Range": "bytes=-64"},
    )
    assert suffix.status_code == 206
    assert suffix.content == payload[-64:]

    invalid_range = client.get(
        parsed.path + "?" + parsed.query,
        headers={"Range": f"bytes={len(payload) + 1}-"},
    )
    assert invalid_range.status_code == 416
    assert invalid_range.headers["content-range"] == f"bytes */{len(payload)}"

    bad_cap = parsed.query[:-1] + ("0" if parsed.query[-1] != "0" else "1")
    denied = client.get(parsed.path + "?" + bad_cap)
    assert denied.status_code == 404

    wrong_name = parsed.path.replace("output%20with%20space.zip", "other.zip")
    denied_name = client.get(wrong_name + "?" + parsed.query)
    assert denied_name.status_code == 404

    ns["_AGB_DOWNLOAD_SECRET_CACHE"] = None
    second_url = ns["_agb_capability_url"](
        record.id,
        record.user_id,
        record.filename,
        origin="http://testserver",
    )
    assert second_url == url
    assert secret_file.is_file()
    assert len(bytes.fromhex(secret_file.read_text(encoding="ascii").strip())) >= 32


def test_deleted_output_revokes_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patcher = load_patcher()
    monkeypatch.setenv(
        "AUTOGENBOOK_OUTPUT_DOWNLOAD_SECRET_FILE",
        str(tmp_path / "download.key"),
    )
    stored = tmp_path / "report.pdf"
    stored.write_bytes(b"%PDF-test")
    record = SimpleNamespace(
        id="file-pdf",
        user_id="owner",
        path=str(stored),
        filename="report.pdf",
        hash="",
        meta={
            "name": "report.pdf",
            "content_type": "application/pdf",
            "size": stored.stat().st_size,
        },
    )
    install_fake_openwebui(monkeypatch, record)
    ns = execute_helpers(patcher)
    app = FastAPI()
    assert ns["_agb_register_download_routes"](app)
    url = ns["_agb_capability_url"](
        record.id,
        record.user_id,
        record.filename,
        origin="http://testserver",
    )
    parsed = urlparse(url)
    stored.unlink()
    response = TestClient(app).get(parsed.path + "?" + parsed.query)
    assert response.status_code == 404
