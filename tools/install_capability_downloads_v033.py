#!/usr/bin/env python3
"""Install or update AutoGenBook Open WebUI capability downloads v0.3.3.

The updater reads the Function currently stored in Open WebUI, creates a local
backup, applies the deterministic 0.3.3 migration and verifies the persisted
Function source through the admin API.  The API key is read from a referenced
TXT file and is never copied into the project, command output or update report.
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

VERSION = "0.3.3"
FUNCTION_ID = "autogenbook_companion"
ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "tools" / "apply_openwebui_capability_downloads_v033.py"


class UpdateError(RuntimeError):
    pass


def load_patcher():
    spec = importlib.util.spec_from_file_location("autogenbook_v033_patcher", PATCHER_PATH)
    if spec is None or spec.loader is None:
        raise UpdateError(f"Cannot load patcher: {PATCHER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_key(raw: str) -> str:
    token = raw.strip().strip("\"'")
    if "=" in token and token.split("=", 1)[0].strip().casefold() in {
        "api_key",
        "openwebui_api_key",
        "webui_api_key",
    }:
        token = token.split("=", 1)[1].strip().strip("\"'")
    if token.casefold().startswith("bearer "):
        token = token[7:].strip()
    if not token or any(ord(char) < 32 for char in token):
        raise UpdateError("The API-key TXT file is empty or contains control characters.")
    return token


def api_json(
    base_url: str,
    token: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        with opener.open(request, timeout=60) as response:
            text = response.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise UpdateError(f"Open WebUI returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Cannot connect to Open WebUI: {exc}") from exc
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise UpdateError("Open WebUI returned an invalid JSON response.") from exc


def validate_persisted(source: str, patcher: Any) -> dict[str, Any]:
    ast.parse(source)
    if "version: 0.3.3" not in source:
        raise UpdateError("Open WebUI did not persist Function version 0.3.3.")
    if "_agb_output_capability" in source:
        return patcher.validate_source(source)
    required = (
        "_autogenbook_output_capability",
        "openwebui-output-download.key",
        "Content-Range",
        "Accept-Ranges",
    )
    missing = [marker for marker in required if marker not in source]
    if missing:
        raise UpdateError(f"Persisted Function does not satisfy the 0.3.3 capability contract: {missing}")
    if "download-ticket" in source:
        raise UpdateError("Persisted Function still contains the failed ticket authentication flow.")
    return {"version": VERSION, "python_syntax": "passed", "compatibility_contract": "direct-capability"}


def patch_source(source: str, patcher: Any) -> str:
    try:
        patched = patcher.patch_source(source)
    except Exception as exc:
        raise UpdateError(f"The installed AutoGenBook Function cannot be migrated: {exc}") from exc
    validate_persisted(patched, patcher)
    return patched


def write_outputs(current: str, patched: str, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    backup = output_dir / "AutoGenBook-OpenWebUI-Function-before-v0.3.3.py"
    if not backup.exists():
        backup.write_text(current, encoding="utf-8", newline="\n")
    output = output_dir / f"AutoGenBook-OpenWebUI-Function-v{VERSION}.py"
    output.write_text(patched, encoding="utf-8", newline="\n")
    return backup, output


def update_api(base_url: str, key_file: Path, output_dir: Path, patcher: Any) -> dict[str, Any]:
    if not key_file.is_file():
        raise UpdateError(f"API-key TXT file does not exist: {key_file}")
    token = normalize_key(key_file.read_text(encoding="utf-8-sig"))
    record = api_json(base_url, token, f"/api/v1/functions/id/{FUNCTION_ID}")
    current = str(record.get("content") or "")
    if not current:
        raise UpdateError(f"Function {FUNCTION_ID} has no source content.")
    patched = patch_source(current, patcher)
    backup, output = write_outputs(current, patched, output_dir)
    payload = {
        "id": FUNCTION_ID,
        "name": str(record.get("name") or "AutoGenBook Companion"),
        "content": patched,
        "meta": record.get("meta") or {"description": "AutoGenBook Companion"},
    }
    api_json(base_url, token, f"/api/v1/functions/id/{FUNCTION_ID}/update", "POST", payload)
    verify = api_json(base_url, token, f"/api/v1/functions/id/{FUNCTION_ID}")
    persisted = str(verify.get("content") or "")
    validation = validate_persisted(persisted, patcher)
    return {
        "method": "openwebui_admin_api",
        "function_id": FUNCTION_ID,
        "version": VERSION,
        "output": str(output),
        "backup": str(backup),
        "api_key_source": str(key_file.resolve()),
        "api_key_copied": False,
        "validation": validation,
    }


def patch_local(path: Path, output_dir: Path, patcher: Any) -> dict[str, Any]:
    if not path.is_file():
        raise UpdateError(f"Function source does not exist: {path}")
    current = path.read_text(encoding="utf-8-sig")
    patched = patch_source(current, patcher)
    backup, output = write_outputs(current, patched, output_dir)
    local_backup = path.with_suffix(path.suffix + ".before-v0.3.3.bak")
    if not local_backup.exists():
        shutil.copy2(path, local_backup)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(patched, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return {
        "method": "local_function_file",
        "version": VERSION,
        "source": str(path),
        "output": str(output),
        "backup": str(backup),
        "local_backup": str(local_backup),
        "validation": validate_persisted(patched, patcher),
    }


def self_test(patcher: Any) -> dict[str, Any]:
    # The production 0.3.2 Function contains _agb_download_landing.  The small
    # fixture intentionally keeps only the anchors needed by the migration, so
    # add the marker as a comment to exercise the same branch deterministically.
    fixture = "# _agb_download_landing\n" + patcher._minimal_ticket_fixture()
    patched = patcher.patch_source(fixture)
    validation = patcher.validate_source(patched)
    result = {
        "version": VERSION,
        "updater_python_syntax": "passed",
        "validation": validation,
        "ticket_route_removed": "download-ticket" not in patched,
        "landing_page_removed": "_agb_download_landing" not in patched,
        "capability_route_present": "/api/autogenbook/v033/output/" in patched,
        "api_key_logged": False,
    }
    if not all(result[key] for key in ("ticket_route_removed", "landing_page_removed", "capability_route_present")):
        raise UpdateError(f"Patcher self-test failed: {result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openwebui-url", default=os.environ.get("OPENWEBUI_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--webui-api-key-file", type=Path)
    parser.add_argument("--function-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    patcher = load_patcher()
    if args.self_test:
        print(json.dumps(self_test(patcher), ensure_ascii=False, indent=2))
        return 0

    output_dir = args.output_dir.expanduser().resolve()
    if args.function_file:
        result = patch_local(args.function_file.expanduser().resolve(), output_dir, patcher)
    else:
        key_file = args.webui_api_key_file
        if key_file is None:
            raw = input("Path to TXT file containing an Open WebUI ADMIN API key: ").strip().strip('"')
            if not raw:
                raise UpdateError("The API-key TXT path is required for automatic Function update.")
            key_file = Path(raw)
        result = update_api(args.openwebui_url, key_file.expanduser().resolve(), output_dir, patcher)

    report = output_dir / f"AutoGenBook-OpenWebUI-v{VERSION}-UPDATE-REPORT.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Fully restart Open WebUI Desktop, then send: vystupy JOB_ID")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as exc:
        print(f"AutoGenBook {VERSION} update failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
