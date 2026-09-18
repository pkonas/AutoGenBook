#!/usr/bin/env python3
"""Production updater for AutoGenBook Open WebUI Function 0.3.2.

This orchestrator applies the 0.3.1 -> 0.3.2 source migration to the Function
currently stored in Open WebUI, hardens route ordering so the download endpoint
is placed before the SPA catch-all, validates the resulting Python source and
updates the Function through the authenticated admin API.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

VERSION = "0.3.2"
FUNCTION_ID = "autogenbook_companion"
ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "tools" / "apply_openwebui_authenticated_downloads_v032.py"


class UpdateError(RuntimeError):
    pass


def load_patcher():
    spec = importlib.util.spec_from_file_location("autogenbook_v032_patcher", PATCHER_PATH)
    if spec is None or spec.loader is None:
        raise UpdateError(f"Cannot load patcher: {PATCHER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def harden_route_order(source: str) -> str:
    """Insert the generated API route immediately before Open WebUI's SPA mount."""
    old = '''        app.add_api_route(
            _AUTOGENBOOK_DOWNLOAD_ROUTE_PATH,
            download_output,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name=_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME,
        )
        if state is not None:
            setattr(state, marker, True)
'''
    new = '''        app.add_api_route(
            _AUTOGENBOOK_DOWNLOAD_ROUTE_PATH,
            download_output,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name=_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME,
        )
        # Open WebUI mounts the SPA on "/". Routes appended after that mount are
        # swallowed by the SPA. Move our route directly before the catch-all.
        added_route = app.routes.pop()
        spa_index = next(
            (
                index
                for index, route in enumerate(app.routes)
                if getattr(route, "name", None) == "spa-static-files"
                or getattr(route, "path", None) in {"/{path:path}", "/"}
                and route.__class__.__name__ in {"Mount", "Route"}
            ),
            len(app.routes),
        )
        app.routes.insert(spa_index, added_route)
        if state is not None:
            setattr(state, marker, True)
'''
    if new in source:
        return source
    count = source.count(old)
    if count != 1:
        raise UpdateError(f"Route-order anchor mismatch: expected 1, found {count}")
    return source.replace(old, new, 1)


def validate_source(source: str) -> dict[str, Any]:
    ast.parse(source)
    required = {
        "version": "version: 0.3.2",
        "route": "_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME",
        "capability": "_autogenbook_output_capability",
        "range": "Content-Range",
        "accept_ranges": "Accept-Ranges",
        "route_order": "spa-static-files",
        "absolute_card_url": '"url": persistent_url',
        "modal_content": 'data["content"]',
        "request_threading": "request=__request__",
    }
    missing = [name for name, marker in required.items() if marker not in source]
    forbidden = [
        marker
        for marker in (
            'return f"/api/v1/files/{file_id}/content?attachment=true"',
            '"url": file_id',
        )
        if marker in source
    ]
    if missing or forbidden:
        raise UpdateError(f"Function contract invalid; missing={missing}, forbidden={forbidden}")
    return {
        "version": VERSION,
        "python_syntax": "passed",
        "required_markers": required,
        "forbidden_markers": forbidden,
    }


def normalize_key(raw: str) -> str:
    token = raw.strip().strip("\"'")
    if "=" in token and token.split("=", 1)[0].strip().casefold() in {
        "api_key", "openwebui_api_key", "webui_api_key"
    }:
        token = token.split("=", 1)[1].strip().strip("\"'")
    if token.casefold().startswith("bearer "):
        token = token[7:].strip()
    if not token or any(ord(char) < 32 for char in token):
        raise UpdateError("The API key file is empty or contains control characters")
    return token


def api_json(base_url: str, token: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=45) as response:
            text = response.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise UpdateError(f"Open WebUI returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Cannot connect to Open WebUI: {exc}") from exc
    return json.loads(text) if text else {}


def patch_source(source: str) -> str:
    if "version: 0.3.2" in source and "_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME" in source:
        result = harden_route_order(source)
        validate_source(result)
        return result
    patcher = load_patcher()
    try:
        result = patcher.patch_source(source)
    except Exception as exc:
        raise UpdateError(f"The installed Function cannot be migrated from 0.3.1: {exc}") from exc
    result = harden_route_order(result)
    validate_source(result)
    return result


def update_api(base_url: str, key_file: Path, output_dir: Path) -> dict[str, Any]:
    token = normalize_key(key_file.read_text(encoding="utf-8-sig"))
    record = api_json(base_url, token, f"/api/v1/functions/id/{FUNCTION_ID}")
    current = str(record.get("content") or "")
    if not current:
        raise UpdateError(f"Function {FUNCTION_ID} has no source content")
    patched = patch_source(current)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"AutoGenBook-OpenWebUI-Function-v{VERSION}.py"
    output.write_text(patched, encoding="utf-8", newline="\n")
    backup = output_dir / "AutoGenBook-OpenWebUI-Function-v0.3.1-backup.py"
    if not backup.exists():
        backup.write_text(current, encoding="utf-8", newline="\n")
    payload = {
        "id": FUNCTION_ID,
        "name": str(record.get("name") or "AutoGenBook Companion"),
        "content": patched,
        "meta": record.get("meta") or {"description": "AutoGenBook Companion"},
    }
    api_json(base_url, token, f"/api/v1/functions/id/{FUNCTION_ID}/update", "POST", payload)
    verify = api_json(base_url, token, f"/api/v1/functions/id/{FUNCTION_ID}")
    persisted = str(verify.get("content") or "")
    validate_source(persisted)
    return {
        "method": "openwebui_admin_api",
        "output": str(output),
        "backup": str(backup),
        "function_id": FUNCTION_ID,
        "version": VERSION,
    }


def patch_local(path: Path, output_dir: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8-sig")
    patched = patch_source(source)
    backup = path.with_suffix(path.suffix + ".v0.3.1.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(patched, encoding="utf-8", newline="\n")
    temporary.replace(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"AutoGenBook-OpenWebUI-Function-v{VERSION}.py"
    output.write_text(patched, encoding="utf-8", newline="\n")
    return {
        "method": "local_function_file",
        "source": str(path),
        "output": str(output),
        "backup": str(backup),
        "version": VERSION,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openwebui-url", default=os.environ.get("OPENWEBUI_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--webui-api-key-file", type=Path)
    parser.add_argument("--function-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        sample = '''        app.add_api_route(\n            _AUTOGENBOOK_DOWNLOAD_ROUTE_PATH,\n            download_output,\n            methods=["GET", "HEAD"],\n            include_in_schema=False,\n            name=_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME,\n        )\n        if state is not None:\n            setattr(state, marker, True)\n'''
        ordered = harden_route_order(sample)
        result = {
            "version": VERSION,
            "route_order_hardening": "spa-static-files" in ordered,
            "updater_python_syntax": "passed",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    output_dir = args.output_dir.expanduser().resolve()
    if args.function_file:
        result = patch_local(args.function_file.expanduser().resolve(), output_dir)
    else:
        key_file = args.webui_api_key_file
        if key_file is None:
            raw = input("Path to the TXT file containing an Open WebUI admin API key: ").strip().strip('"')
            key_file = Path(raw)
        result = update_api(args.openwebui_url, key_file.expanduser().resolve(), output_dir)
    report = output_dir / "AutoGenBook-OpenWebUI-v0.3.2-UPDATE-REPORT.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Restart Open WebUI Desktop, then send: outputs JOB_ID (or: výstupy JOB_ID)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as exc:
        print(f"AutoGenBook 0.3.2 update failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
