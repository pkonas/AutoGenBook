#!/usr/bin/env python3
"""Migrate AutoGenBook Open WebUI Function to durable capability downloads v0.3.3.

Version 0.3.2 used a public landing page which attempted to read the Open WebUI
SPA token from browser storage and then POST to a protected ticket endpoint.
Open WebUI Desktop may open that page outside the authenticated webview storage
partition.  The landing page then loads successfully, but the ticket request is
unauthenticated and returns HTTP 401.

Version 0.3.3 removes the landing/ticket dependency.  Every published output URL
contains an unguessable HMAC capability scoped to one user-owned Open WebUI file.
The route validates the capability, the database owner and the stored object,
then streams the bytes with GET/HEAD/Range support.  No Open WebUI API key,
session token or Companion token is embedded in the URL.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

VERSION = "0.3.3"
FUNCTION_ID = "autogenbook_companion"
ROOT = Path(__file__).resolve().parents[1]
V032_PATCHER = ROOT / "tools" / "apply_openwebui_authenticated_downloads_v032.py"


class PatchError(RuntimeError):
    pass


CAPABILITY_HELPERS = r'''
# ---------------------------------------------------------------------------
# Durable, browser-independent AutoGenBook downloads for Open WebUI Desktop
# ---------------------------------------------------------------------------
# A normal navigation opened by Electron may run outside the authenticated
# Open WebUI webview storage partition.  Therefore download URLs must not rely
# on localStorage, session cookies or an Authorization header.  The capability
# below is an unguessable HMAC bound to one Open WebUI file, its owner and its
# displayed filename.  The secret is persisted outside the Function source so
# links survive Function reloads, Open WebUI restarts and Companion restarts.

_AGB_DOWNLOAD_PREFIX = "/api/autogenbook/v033"
_AGB_DOWNLOAD_ROUTE_NAME = "autogenbook-capability-download-v033"
_AGB_DOWNLOAD_ROUTE_LOCK = threading.RLock()
_AGB_DOWNLOAD_SECRET_LOCK = threading.RLock()
_AGB_DOWNLOAD_SECRET_CACHE: bytes | None = None
_AGB_PUBLIC_ORIGIN: ContextVar[str] = ContextVar("autogenbook_public_origin", default="")


def _agb_request_origin(request: Any) -> str:
    if request is None:
        return ""
    try:
        return str(request.base_url).rstrip("/")
    except Exception:
        return ""


def _agb_download_secret_path() -> Path:
    explicit = str(os.environ.get("AUTOGENBOOK_OUTPUT_DOWNLOAD_SECRET_FILE") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    companion_home = str(os.environ.get("AUTOGENBOOK_COMPANION_HOME") or "").strip()
    if companion_home:
        return Path(companion_home).expanduser().resolve() / "secrets" / "openwebui-output-download.key"

    data_dir = str(os.environ.get("DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "autogenbook" / "secrets" / "openwebui-output-download.key"

    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        base = local / "Programs" / "AutoGenBook OpenWebUI" / "data"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AutoGenBook-OpenWebUI" / "data"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        base = base / "autogenbook-openwebui" / "data"
    return base / "secrets" / "openwebui-output-download.key"


def _agb_download_secret() -> bytes:
    global _AGB_DOWNLOAD_SECRET_CACHE
    with _AGB_DOWNLOAD_SECRET_LOCK:
        if _AGB_DOWNLOAD_SECRET_CACHE is not None:
            return _AGB_DOWNLOAD_SECRET_CACHE

        path = _agb_download_secret_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        value = b""
        try:
            value = bytes.fromhex(path.read_text(encoding="ascii").strip())
        except Exception:
            value = b""

        if len(value) < 32:
            candidate = secrets.token_bytes(32)
            encoded = candidate.hex() + "\n"
            try:
                descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                try:
                    existing = bytes.fromhex(path.read_text(encoding="ascii").strip())
                except Exception:
                    existing = b""
                if len(existing) >= 32:
                    value = existing
                else:
                    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
                    temporary.write_text(encoded, encoding="ascii", newline="\n")
                    try:
                        os.chmod(temporary, 0o600)
                    except OSError:
                        pass
                    temporary.replace(path)
                    value = candidate
            else:
                with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
                    stream.write(encoded)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                value = candidate

        if len(value) < 32:
            raise CompanionError("AutoGenBook could not create a persistent output capability key.")
        _AGB_DOWNLOAD_SECRET_CACHE = value
        return value


def _agb_safe_filename(value: str) -> str:
    name = Path(str(value or "artifact.bin")).name or "artifact.bin"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "artifact.bin"


def _agb_output_capability(file_id: str, user_id: str, filename: str) -> str:
    safe_name = _agb_safe_filename(filename)
    message = f"autogenbook-output-v3:{user_id}:{file_id}:{safe_name}".encode("utf-8")
    return hmac.new(_agb_download_secret(), message, hashlib.sha256).hexdigest()


def _agb_capability_url(
    file_id: str,
    user_id: str,
    filename: str,
    *,
    origin: str | None = None,
) -> str:
    safe_name = _agb_safe_filename(filename)
    capability = _agb_output_capability(str(file_id), str(user_id), safe_name)
    path = (
        f"{_AGB_DOWNLOAD_PREFIX}/output/{quote(str(file_id), safe='')}/"
        f"{quote(safe_name, safe='')}?cap={quote(capability, safe='')}"
    )
    selected_origin = str(origin if origin is not None else _AGB_PUBLIC_ORIGIN.get()).rstrip("/")
    if not selected_origin:
        selected_origin = str(
            os.environ.get("OPENWEBUI_URL")
            or os.environ.get("WEBUI_URL")
            or "http://127.0.0.1:8080"
        ).rstrip("/")
    return f"{selected_origin}{path}"


def _agb_preview_content(filename: str, download_url: str, size: int, content_type: str) -> str:
    return (
        f"# {filename}\n\n"
        "Toto je binární výstup AutoGenBooku uložený v trvalém úložišti Open WebUI. "
        "Textový náhled binárního souboru se nevytváří.\n\n"
        f"[**Stáhnout {filename}**]({download_url})\n\n"
        f"- Velikost: `{int(size)} B`\n"
        f"- Typ: `{content_type}`\n\n"
        "Odkaz je kryptograficky podepsaný pro tento jediný soubor. Neobsahuje "
        "Open WebUI API klíč, session token ani Companion token a nevyžaduje "
        "přenos přihlášení do nového okna."
    )


def _agb_file_meta(file_record: Any) -> tuple[dict[str, Any], str, str, int]:
    meta = getattr(file_record, "meta", {}) or {}
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump()
    if not isinstance(meta, dict):
        meta = {}
    filename = _agb_safe_filename(
        str(meta.get("name") or getattr(file_record, "filename", "") or "artifact.bin")
    )
    content_type = str(
        meta.get("content_type")
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    try:
        size = max(0, int(meta.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    return meta, filename, content_type, size


def _agb_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip(), flags=re.I)
    if not match or "," in range_header:
        raise ValueError("invalid range")
    raw_start, raw_end = match.groups()
    if not raw_start and not raw_end:
        raise ValueError("invalid range")
    if not raw_start:
        suffix = int(raw_end)
        if suffix <= 0:
            raise ValueError("invalid suffix")
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    if size <= 0 or start < 0 or start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)


async def _agb_maybe_await(value: Any) -> Any:
    return await value if hasattr(value, "__await__") else value


async def _agb_capability_download(
    file_id: str,
    filename: str,
    request: Request,
    cap: str = "",
) -> Any:
    from fastapi import HTTPException
    from fastapi.responses import Response, StreamingResponse
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,240}", str(file_id or "")):
        raise HTTPException(status_code=404, detail="Output not found.")
    requested_name = _agb_safe_filename(filename)
    record = await _agb_maybe_await(Files.get_file_by_id(str(file_id)))
    if record is None:
        raise HTTPException(status_code=404, detail="Output not found.")
    owner_id = str(getattr(record, "user_id", "") or "")
    expected = _agb_output_capability(str(file_id), owner_id, requested_name)
    if not owner_id or not cap or not hmac.compare_digest(str(cap), expected):
        raise HTTPException(status_code=404, detail="Output not found.")

    stored = str(getattr(record, "path", "") or "")
    if not stored:
        raise HTTPException(status_code=404, detail="Output data not found.")
    resolved = Path(stored).expanduser()
    if not resolved.is_file():
        try:
            resolved = Path(str(await asyncio.to_thread(Storage.get_file, stored)))
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Output data not found.") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Output data not found.")

    _meta, actual_name, content_type, declared_size = _agb_file_meta(record)
    if requested_name != actual_name:
        raise HTTPException(status_code=404, detail="Output not found.")
    stat = resolved.stat()
    size = int(stat.st_size)
    if declared_size and declared_size != size:
        raise HTTPException(status_code=409, detail="Stored output size does not match metadata.")

    source_hash = str(getattr(record, "hash", "") or "").strip()
    etag = f'"{source_hash}"' if source_hash else f'W/"{size:x}-{stat.st_mtime_ns:x}"'
    range_header = request.headers.get("range")
    if range_header and request.headers.get("if-range") not in (None, "", etag):
        range_header = None
    try:
        selected_range = _agb_range(range_header, size)
    except (TypeError, ValueError):
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{size}",
                "Accept-Ranges": "bytes",
                "ETag": etag,
                "Cache-Control": "private, no-store",
            },
        )

    start, end = selected_range if selected_range is not None else (0, max(0, size - 1))
    length = max(0, end - start + 1) if size else 0
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(actual_name, safe='')}",
        "Content-Length": str(length),
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    status_code = 206 if selected_range is not None else 200
    if selected_range is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method.upper() == "HEAD":
        return Response(status_code=status_code, headers=headers, media_type=content_type)

    async def iterator() -> AsyncIterator[bytes]:
        remaining = length
        with resolved.open("rb") as stream:
            stream.seek(start)
            while remaining > 0:
                block = await asyncio.to_thread(stream.read, min(1024 * 1024, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block

    return StreamingResponse(
        iterator(), status_code=status_code, headers=headers, media_type=content_type
    )


def _agb_route_is_spa(route: Any) -> bool:
    path = str(getattr(route, "path", "") or "")
    name = str(getattr(route, "name", "") or "")
    return name == "spa-static-files" or path in {"/{path:path}", "/{full_path:path}"}


def _agb_register_download_routes(app: Any = None) -> bool:
    if app is None:
        for module_name in ("open_webui.main", "open_webui.app"):
            try:
                module = __import__(module_name, fromlist=["app"])
                app = getattr(module, "app", None)
                if app is not None:
                    break
            except Exception:
                continue
    if app is None or not hasattr(app, "add_api_route"):
        return False

    stale_names = {
        "autogenbook-download-landing-v032",
        "autogenbook-download-ticket-v032",
        "autogenbook-download-stream-v032",
        "autogenbook_output_download_v032",
        _AGB_DOWNLOAD_ROUTE_NAME,
    }
    with _AGB_DOWNLOAD_ROUTE_LOCK:
        existing_names = {
            str(getattr(route, "name", "") or "") for route in app.router.routes
        }
        if _AGB_DOWNLOAD_ROUTE_NAME in existing_names and not (
            existing_names & (stale_names - {_AGB_DOWNLOAD_ROUTE_NAME})
        ):
            return True

        app.router.routes[:] = [
            route
            for route in app.router.routes
            if str(getattr(route, "name", "") or "") not in stale_names
        ]
        app.add_api_route(
            f"{_AGB_DOWNLOAD_PREFIX}/output/{{file_id}}/{{filename}}",
            _agb_capability_download,
            methods=["GET", "HEAD"],
            name=_AGB_DOWNLOAD_ROUTE_NAME,
            include_in_schema=False,
        )
        added = app.router.routes.pop()
        insertion = next(
            (index for index, route in enumerate(app.router.routes) if _agb_route_is_spa(route)),
            len(app.router.routes),
        )
        app.router.routes.insert(insertion, added)
        return True
'''


METHOD_OLD = '''    @staticmethod
    def _persistent_output_url(file_id: str) -> str:
        return _agb_landing_url(file_id)
'''

METHOD_NEW = '''    @staticmethod
    def _persistent_output_url(file_id: str, user_id: str, filename: str) -> str:
        return _agb_capability_url(file_id, user_id, filename)
'''

CALL_OLD = "        persistent_url = self._persistent_output_url(file_id)\n"
CALL_NEW = (
    "        persistent_url = self._persistent_output_url(\n"
    "            file_id, user_id, str(enriched.get(\"filename\") or \"artifact.bin\")\n"
    "        )\n"
)


def _set_version(text: str) -> str:
    updated, count = re.subn(
        r"(?m)^version:\s*[^\n]+$",
        f"version: {VERSION}",
        text,
        count=1,
    )
    if count != 1:
        raise PatchError("Function frontmatter version was not found exactly once.")
    return updated


def _load_v032_patcher():
    spec = importlib.util.spec_from_file_location("autogenbook_v032_patcher", V032_PATCHER)
    if spec is None or spec.loader is None:
        raise PatchError(f"Cannot load compatibility patcher: {V032_PATCHER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace_ticket_block(text: str) -> str:
    start_marker = (
        "# ---------------------------------------------------------------------------\n"
        "# Authenticated, durable AutoGenBook downloads for Open WebUI Desktop"
    )
    start = text.find(start_marker)
    end = text.find("\n\nclass Pipe:", start + 1)
    if start < 0 or end < 0:
        raise PatchError("The 0.3.2 landing/ticket download block was not found.")
    return text[:start] + CAPABILITY_HELPERS.strip() + text[end:]


def validate_source(source: str) -> dict[str, Any]:
    ast.parse(source)
    required = {
        "version": "version: 0.3.3",
        "capability": "_agb_output_capability",
        "stable_secret": "openwebui-output-download.key",
        "direct_route": "_agb_capability_download",
        "range": '"Accept-Ranges": "bytes"',
        "route_order": "_agb_route_is_spa",
        "absolute_card_url": '"url": persistent_url',
        "preview_content": "_agb_preview_content",
        "publisher_call": "file_id, user_id, str(enriched.get",
    }
    missing = [name for name, marker in required.items() if marker not in source]
    forbidden = [
        marker
        for marker in (
            "/api/autogenbook/download-ticket",
            "autogenbook-download-ticket-v032",
            "window.localStorage.getItem('token')",
            "_agb_landing_url(file_id)",
        )
        if marker in source
    ]
    if missing or forbidden:
        raise PatchError(f"Function contract invalid; missing={missing}, forbidden={forbidden}")
    return {
        "version": VERSION,
        "python_syntax": "passed",
        "required_markers": sorted(required),
        "forbidden_markers": forbidden,
    }


def patch_source(source: str) -> str:
    text = source.replace("\r\n", "\n")
    if "version: 0.3.3" in text and "_agb_output_capability" in text:
        validate_source(text)
        return text

    # Older 0.3.1 Functions can first be migrated by the audited 0.3.2
    # compatibility patcher.  Its result already uses a direct HMAC capability
    # route and does not need the landing/ticket replacement below.
    if "version: 0.3.1" in text and "_agb_download_landing" not in text:
        text = _load_v032_patcher().patch_source(text)

    if "_agb_download_landing" in text or "autogenbook-download-ticket-v032" in text:
        text = _replace_ticket_block(text)
        if METHOD_OLD not in text:
            raise PatchError("Persistent-output URL method anchor was not found.")
        text = text.replace(METHOD_OLD, METHOD_NEW, 1)
        if CALL_OLD not in text:
            raise PatchError("Persistent-output URL call anchor was not found.")
        text = text.replace(CALL_OLD, CALL_NEW, 1)
    elif "_autogenbook_output_capability" in text and "_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME" in text:
        # The alternate 0.3.2 branch already implements a stable direct
        # capability.  Preserve its audited implementation and only advance
        # the declared version.
        text = text.replace("version: 0.3.2", "version: 0.3.3", 1)
        ast.parse(text)
        return text
    else:
        raise PatchError("Unsupported Function source: expected AutoGenBook 0.3.1 or 0.3.2.")

    text = _set_version(text)
    text = text.replace('"autogenbook_download_broker": "v0.3.2"', '"autogenbook_download_broker": "v0.3.3-capability"')
    text = text.replace(
        "stabilní autentizovaný download broker Open WebUI bez Companion tokenu a bez expirace odkazu.",
        "stabilní podepsaný download Open WebUI bez Companion tokenu, session cookie a expirace odkazu.",
    )
    validate_source(text)
    return text


def _minimal_ticket_fixture() -> str:
    return '''"""\ntitle: AutoGenBook Companion\nversion: 0.3.2\n"""\nfrom __future__ import annotations\nimport asyncio, hashlib, hmac, json, mimetypes, os, re, secrets, sys, threading, time, uuid\nfrom contextvars import ContextVar\nfrom pathlib import Path\nfrom typing import Any, AsyncIterator\nfrom urllib.parse import quote\nfrom fastapi import Request\nclass CompanionError(RuntimeError): pass\n# ---------------------------------------------------------------------------\n# Authenticated, durable AutoGenBook downloads for Open WebUI Desktop\n# ---------------------------------------------------------------------------\n_AGB_DOWNLOAD_PREFIX = "/api/autogenbook"\ndef _agb_landing_url(file_id: str): return file_id\ndef _agb_preview_content(filename, url, size, content_type): return url\ndef _agb_file_meta(record): return {}, "x", "application/octet-stream", 1\ndef _agb_register_download_routes(app=None): return True\n\nclass Pipe:\n    @staticmethod\n    def _persistent_output_url(file_id: str) -> str:\n        return _agb_landing_url(file_id)\n    async def publish(self, file_id, user_id, enriched):\n        persistent_url = self._persistent_output_url(file_id)\n        return {"url": persistent_url}, persistent_url\n'''


def self_test() -> dict[str, Any]:
    patched = patch_source(_minimal_ticket_fixture())
    result = validate_source(patched)
    result.update(
        {
            "ticket_route_removed": "download-ticket" not in patched,
            "landing_page_removed": "_agb_download_landing" not in patched,
            "capability_route_present": "/api/autogenbook/v033/output/" in patched,
        }
    )
    if not all(
        result[key]
        for key in ("ticket_route_removed", "landing_page_removed", "capability_route_present")
    ):
        raise PatchError(f"Self-test failed: {result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    if args.input is None:
        parser.error("input Function source is required unless --self-test is used")
    source = args.input.read_text(encoding="utf-8-sig")
    patched = patch_source(source)
    output = args.output or args.input.with_name(f"AutoGenBook-OpenWebUI-Function-v{VERSION}.py")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(patched, encoding="utf-8", newline="\n")
    print(json.dumps({"version": VERSION, "output": str(output), **validate_source(patched)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"AutoGenBook Function patch failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
