#!/usr/bin/env python3
"""Patch AutoGenBook Open WebUI Function 0.3.1 to durable download contract 0.3.2.

The update fixes two independent Open WebUI behaviours:

* a normal browser navigation to ``/api/v1/files/<id>/content`` does not carry
  the bearer token stored by the SPA, so a Markdown link opened in a new tab
  returns ``Not authenticated``;
* output records created with ``process=false`` have no extracted text, so the
  standard file modal renders ``No content`` unless a useful preview is stored.

Version 0.3.2 registers a narrow, capability-protected download route on the
Open WebUI application itself.  The URL contains a HMAC capability for one
file only; it never contains an Open WebUI API key, session token or Companion
bearer token.  The output is still registered as a normal user-owned Open WebUI
file and its database row gets a non-empty explanatory ``data.content`` value.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

VERSION = "0.3.2"
FUNCTION_ID = "autogenbook_companion"
OUTPUT_NAME = f"AutoGenBook-OpenWebUI-Function-v{VERSION}.py"


class PatchError(RuntimeError):
    pass


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected exactly one source anchor, found {count}")
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S | re.M)
    if count == 0 and replacement in text:
        return text
    if count != 1:
        raise PatchError(f"{label}: expected exactly one regex anchor, found {count}")
    return updated


DOWNLOAD_HELPERS = r'''
_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME = "autogenbook_output_download_v032"
_AUTOGENBOOK_DOWNLOAD_ROUTE_PATH = "/autogenbook-output/{file_id}/{filename}"
_AUTOGENBOOK_DOWNLOAD_SECRET_LOCK = threading.RLock()
_AUTOGENBOOK_DOWNLOAD_SECRET_CACHE: bytes | None = None
_AUTOGENBOOK_DOWNLOAD_ROUTE_ERROR = ""


def _autogenbook_download_secret_file() -> Path:
    explicit = os.environ.get("AUTOGENBOOK_OUTPUT_DOWNLOAD_SECRET_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    data_dir = os.environ.get("DATA_DIR", "").strip()
    if data_dir:
        base = Path(data_dir).expanduser().resolve() / "autogenbook"
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        base = local / "Programs" / "AutoGenBook OpenWebUI" / "data"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AutoGenBook-OpenWebUI" / "data"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        base = base / "autogenbook-openwebui" / "data"
    return base / "secrets" / "openwebui-output-download.key"


def _autogenbook_download_secret() -> bytes:
    global _AUTOGENBOOK_DOWNLOAD_SECRET_CACHE
    with _AUTOGENBOOK_DOWNLOAD_SECRET_LOCK:
        if _AUTOGENBOOK_DOWNLOAD_SECRET_CACHE is not None:
            return _AUTOGENBOOK_DOWNLOAD_SECRET_CACHE
        path = _autogenbook_download_secret_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            value = bytes.fromhex(path.read_text(encoding="ascii").strip())
        except Exception:
            value = b""
        if len(value) < 32:
            value = secrets.token_bytes(32)
            encoded = value.hex() + "\n"
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
                    temporary.write_text(encoded, encoding="ascii")
                    try:
                        os.chmod(temporary, 0o600)
                    except OSError:
                        pass
                    temporary.replace(path)
            else:
                with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
                    stream.write(encoded)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
        _AUTOGENBOOK_DOWNLOAD_SECRET_CACHE = value
        return value


def _autogenbook_output_capability(file_id: str, user_id: str) -> str:
    message = f"autogenbook-output-v1:{user_id}:{file_id}".encode("utf-8")
    return hmac.new(_autogenbook_download_secret(), message, hashlib.sha256).hexdigest()


def _autogenbook_parse_range(value: str, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("Only one bytes range is supported")
    raw = value[6:].strip()
    start_text, separator, end_text = raw.partition("-")
    if not separator:
        raise ValueError("Malformed range")
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("Invalid suffix range")
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    if size <= 0 or start < 0 or end < start or start >= size:
        raise ValueError("Unsatisfiable range")
    return start, min(end, size - 1)


def _autogenbook_file_chunks(path: Path, start: int, length: int, chunk_size: int = 1024 * 1024):
    remaining = length
    with path.open("rb") as stream:
        stream.seek(start)
        while remaining > 0:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _autogenbook_register_download_route() -> bool:
    global _AUTOGENBOOK_DOWNLOAD_ROUTE_ERROR
    try:
        from open_webui.main import app
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage
    except Exception as exc:
        _AUTOGENBOOK_DOWNLOAD_ROUTE_ERROR = f"Open WebUI download route import failed: {exc}"
        return False

    state = getattr(app, "state", None)
    marker = "_autogenbook_output_download_v032_registered"
    if state is not None and bool(getattr(state, marker, False)):
        return True
    for route in getattr(app, "routes", []):
        if getattr(route, "name", None) == _AUTOGENBOOK_DOWNLOAD_ROUTE_NAME:
            if state is not None:
                setattr(state, marker, True)
            return True

    async def download_output(
        file_id: str,
        filename: str,
        request: Request,
        uid: str = "",
        cap: str = "",
    ):
        expected = _autogenbook_output_capability(str(file_id), str(uid))
        if not uid or not cap or not hmac.compare_digest(expected, str(cap)):
            raise HTTPException(status_code=404, detail="Output not found")

        record = await Files.get_file_by_id(str(file_id))
        if record is None or str(getattr(record, "user_id", "") or "") != str(uid):
            raise HTTPException(status_code=404, detail="Output not found")
        stored = str(getattr(record, "path", "") or "")
        if not stored:
            raise HTTPException(status_code=404, detail="Output data not found")
        try:
            local = await asyncio.to_thread(Storage.get_file, stored)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"Output data not found: {exc}") from exc
        path = Path(local)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Output data not found")

        stat = path.stat()
        size = int(stat.st_size)
        meta = getattr(record, "meta", {}) or {}
        if hasattr(meta, "model_dump"):
            meta = meta.model_dump()
        if not isinstance(meta, dict):
            meta = {}
        download_name = Path(str(meta.get("name") or getattr(record, "filename", None) or filename)).name
        content_type = str(meta.get("content_type") or mimetypes.guess_type(download_name)[0] or "application/octet-stream")
        source_hash = str(getattr(record, "hash", "") or "")
        etag_value = source_hash or hashlib.sha256(
            f"{file_id}:{size}:{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()
        etag = f'"{etag_value}"'
        common = {
            "Accept-Ranges": "bytes",
            "ETag": etag,
            "Cache-Control": "private, max-age=0, must-revalidate",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name, safe='')}",
        }
        range_header = request.headers.get("range", "")
        if_range = request.headers.get("if-range", "")
        if range_header and if_range and if_range not in {etag, etag_value}:
            range_header = ""
        try:
            byte_range = _autogenbook_parse_range(range_header, size)
        except (TypeError, ValueError):
            headers = {**common, "Content-Range": f"bytes */{size}", "Content-Length": "0"}
            return Response(status_code=416, headers=headers)

        if byte_range is None:
            start, end, status_code = 0, max(0, size - 1), 200
        else:
            start, end = byte_range
            status_code = 206
        length = max(0, end - start + 1) if size else 0
        headers = {**common, "Content-Length": str(length)}
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if request.method.upper() == "HEAD":
            return Response(status_code=status_code, headers=headers, media_type=content_type)
        return StreamingResponse(
            _autogenbook_file_chunks(path, start, length),
            status_code=status_code,
            headers=headers,
            media_type=content_type,
        )

    try:
        app.add_api_route(
            _AUTOGENBOOK_DOWNLOAD_ROUTE_PATH,
            download_output,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name=_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME,
        )
        if state is not None:
            setattr(state, marker, True)
        _AUTOGENBOOK_DOWNLOAD_ROUTE_ERROR = ""
        return True
    except Exception as exc:
        _AUTOGENBOOK_DOWNLOAD_ROUTE_ERROR = f"Open WebUI download route registration failed: {exc}"
        return False
'''


NEW_URL_METHOD = r'''    @staticmethod
    def _persistent_output_url(
        file_id: str,
        user_id: str,
        filename: str,
        request: Any,
    ) -> str:
        if not _autogenbook_register_download_route():
            raise CompanionError(
                _AUTOGENBOOK_DOWNLOAD_ROUTE_ERROR
                or "Open WebUI output download route is unavailable."
            )
        base_url = str(getattr(request, "base_url", "") or "").rstrip("/")
        if not base_url:
            base_url = str(
                os.environ.get("OPENWEBUI_URL")
                or os.environ.get("WEBUI_URL")
                or ""
            ).rstrip("/")
        if not base_url:
            raise CompanionError(
                "Open WebUI base URL is unavailable; a durable output link cannot be published."
            )
        capability = _autogenbook_output_capability(file_id, user_id)
        safe_name = quote(Path(filename).name or "artifact.bin", safe="")
        safe_id = quote(file_id, safe="")
        safe_user = quote(user_id, safe="")
        return (
            f"{base_url}/autogenbook-output/{safe_id}/{safe_name}"
            f"?uid={safe_user}&cap={capability}"
        )

    @staticmethod
    def _output_content_message(filename: str, persistent_url: str) -> str:
        return (
            f"Výstupní soubor AutoGenBooku: **{filename}**\n\n"
            f"[Stáhnout původní soubor]({persistent_url})\n\n"
            "Tento záznam uchovává binární výstup v úložišti Open WebUI. "
            "Náhled extrahovaného textu se pro vygenerované soubory nezpracovává."
        )
'''


NEW_FILE_PAYLOAD = r'''    @staticmethod
    def _file_payload(item: Any, persistent_url: str) -> dict[str, Any]:
        """Return a native Open WebUI assistant file with a durable capability URL."""
        raw = item.model_dump(exclude={"path"}) if hasattr(item, "model_dump") else dict(item)
        meta = raw.get("meta") or {}
        if hasattr(meta, "model_dump"):
            meta = meta.model_dump()
        elif not isinstance(meta, dict):
            meta = {}
        raw["meta"] = meta

        file_id = str(raw.get("id") or "")
        filename = str(meta.get("name") or raw.get("filename") or "artifact.bin")
        content_type = str(meta.get("content_type") or "application/octet-stream")
        try:
            size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        data = raw.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        data["status"] = "completed"
        if not str(data.get("content") or "").strip():
            data["content"] = Pipe._output_content_message(filename, persistent_url)
        raw["data"] = data

        return {
            "type": "file",
            "id": file_id,
            # FileItemModal opens absolute HTTP URLs directly.  A plain file ID
            # would be rewritten to the bearer-protected /api/v1/files route.
            "url": persistent_url,
            "name": filename,
            "filename": filename,
            "status": "uploaded",
            "size": size,
            "content_type": content_type,
            "collection_name": str(meta.get("collection_name") or ""),
            "file": raw,
            "meta": meta,
            "content": data["content"],
            "source": "autogenbook",
            "download_url": persistent_url,
            "persistent_url": persistent_url,
        }

'''


def patch_source(source: str) -> str:
    text = source.replace("\r\n", "\n")
    if "version: 0.3.2" in text and "_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME" in text:
        ast.parse(text)
        return text
    if "version: 0.3.1" not in text:
        raise PatchError("The input is not the expected AutoGenBook Function 0.3.1")
    required = (
        "PERSIST_OUTPUTS_TO_OPENWEBUI",
        "def _persistent_output_url",
        "def _file_payload",
        "def _publish_artifact_to_openwebui",
        "def _attach_artifacts",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise PatchError("Missing v0.3.1 durable-output markers: " + ", ".join(missing))

    text = replace_once(text, "version: 0.3.1", "version: 0.3.2", "frontmatter version")
    text = replace_once(text, "import hashlib\n", "import hashlib\nimport hmac\n", "hmac import")
    text = replace_once(text, "import mimetypes\n", "import mimetypes\nimport secrets\n", "secrets import")
    text = replace_once(text, "from pathlib import Path\n", "from pathlib import Path\nfrom urllib.parse import quote\n", "quote import")
    text = replace_once(
        text,
        "import httpx\nfrom pydantic import BaseModel, Field\n",
        "import httpx\nfrom fastapi import HTTPException, Request\nfrom fastapi.responses import Response, StreamingResponse\nfrom pydantic import BaseModel, Field\n",
        "FastAPI imports",
    )
    text = replace_once(
        text,
        "class Pipe:\n",
        DOWNLOAD_HELPERS.strip("\n") + "\n\n\nclass Pipe:\n",
        "download helpers",
    )
    text = replace_once(
        text,
        "        self._bridge_cache: dict[str, Any] | None = None\n",
        "        self._bridge_cache: dict[str, Any] | None = None\n        _autogenbook_register_download_route()\n",
        "route registration",
    )
    text = replace_regex_once(
        text,
        r"    @staticmethod\n    def _persistent_output_url\(file_id: str\) -> str:\n        return f\"/api/v1/files/\{file_id\}/content\?attachment=true\"\n",
        NEW_URL_METHOD,
        "persistent URL method",
    )
    text = replace_regex_once(
        text,
        r"    @staticmethod\n    def _file_payload\(item: Any, persistent_url: str\) -> dict\[str, Any\]:\n.*?(?=    async def _stream_companion_artifact\()",
        NEW_FILE_PAYLOAD,
        "file payload method",
    )
    text = replace_once(
        text,
        "        event_emitter: Any,\n    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:\n",
        "        event_emitter: Any,\n        request: Any = None,\n    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:\n",
        "publisher request argument",
    )
    text = replace_once(
        text,
        "        file_id = self._output_file_id(user_id, enriched)\n        persistent_url = self._persistent_output_url(file_id)\n",
        "        file_id = self._output_file_id(user_id, enriched)\n        original_name = str(enriched.get(\"filename\") or \"artifact.bin\")\n        persistent_url = self._persistent_output_url(\n            file_id, user_id, original_name, request\n        )\n",
        "capability URL creation",
    )
    text = replace_once(
        text,
        "        original_name = str(enriched.get(\"filename\") or \"artifact.bin\")\n        storage_name = self._output_storage_name(file_id, original_name, upload_dir)\n",
        "        storage_name = self._output_storage_name(file_id, original_name, upload_dir)\n",
        "duplicate output name",
    )
    text = replace_once(
        text,
        "                if size_matches and local_exists:\n                    payload = self._file_payload(existing, persistent_url)\n",
        "                if size_matches and local_exists:\n                    update_data = getattr(Files, \"update_file_data_by_id\", None)\n                    if callable(update_data):\n                        await self._await_if_needed(\n                            update_data(\n                                file_id,\n                                {\n                                    \"status\": \"completed\",\n                                    \"content\": self._output_content_message(\n                                        original_name, persistent_url\n                                    ),\n                                },\n                            )\n                        )\n                        existing = await self._await_if_needed(get_file(file_id))\n                    payload = self._file_payload(existing, persistent_url)\n",
        "existing output content repair",
    )
    text = replace_once(
        text,
        "                        \"autogenbook_job_id\": str(enriched.get(\"job_id\") or \"\"),\n                    },\n",
        "                        \"autogenbook_job_id\": str(enriched.get(\"job_id\") or \"\"),\n                        \"content\": self._output_content_message(\n                            original_name, persistent_url\n                        ),\n                    },\n",
        "new output content",
    )
    text = replace_once(
        text,
        "        *,\n        include_all: bool = False,\n    ) -> tuple[list[dict[str, Any]], list[str]]:\n",
        "        *,\n        include_all: bool = False,\n        request: Any = None,\n    ) -> tuple[list[dict[str, Any]], list[str]]:\n",
        "attachment request argument",
    )
    text = replace_once(
        text,
        "                    user,\n                    event_emitter,\n                )\n",
        "                    user,\n                    event_emitter,\n                    request,\n                )\n",
        "publisher request forwarding",
    )
    text = replace_once(
        text,
        "        event_call: Any = None,\n        user_valves: \"Pipe.UserValves | None\" = None,\n    ) -> str:\n",
        "        event_call: Any = None,\n        user_valves: \"Pipe.UserValves | None\" = None,\n        request: Any = None,\n    ) -> str:\n",
        "job command request argument",
    )
    text = replace_once(
        text,
        "                include_all=(command == \"artifacts\"),\n            )\n",
        "                include_all=(command == \"artifacts\"),\n                request=request,\n            )\n",
        "job artifact request forwarding",
    )
    text = replace_once(
        text,
        "                    __event_call__,\n                    user_valves,\n                )\n",
        "                    __event_call__,\n                    user_valves,\n                    __request__,\n                )\n",
        "pipe command request forwarding",
    )
    text = replace_once(
        text,
        "                artifacts, skipped = await self._attach_artifacts(\n                    bridge, owner_id, job_id, user, __event_emitter__\n                )\n",
        "                artifacts, skipped = await self._attach_artifacts(\n                    bridge, owner_id, job_id, user, __event_emitter__, request=__request__\n                )\n",
        "completed job request forwarding",
    )

    ast.parse(text)
    forbidden = (
        'return f"/api/v1/files/{file_id}/content?attachment=true"',
        '"url": file_id',
    )
    for marker in forbidden:
        if marker in text:
            raise PatchError(f"Forbidden unauthenticated output contract remains: {marker}")
    required_after = (
        "_AUTOGENBOOK_DOWNLOAD_ROUTE_NAME",
        "_autogenbook_output_capability",
        "app.add_api_route",
        '"url": persistent_url',
        'data["content"]',
        "request=__request__",
        "Content-Range",
        "Accept-Ranges",
    )
    missing_after = [marker for marker in required_after if marker not in text]
    if missing_after:
        raise PatchError("Patched source lacks required v0.3.2 markers: " + ", ".join(missing_after))
    return text


def normalize_api_key(value: str) -> str:
    token = value.strip().strip("\"'")
    if "=" in token and token.split("=", 1)[0].strip().casefold() in {
        "api_key", "openwebui_api_key", "webui_api_key"
    }:
        token = token.split("=", 1)[1].strip().strip("\"'")
    if token.casefold().startswith("bearer "):
        token = token[7:].strip()
    if not token or any(ord(char) < 32 for char in token):
        raise PatchError("Open WebUI API key file is empty or contains control characters")
    return token


def request_json(url: str, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PatchError(f"Open WebUI API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise PatchError(f"Cannot reach Open WebUI API: {exc}") from exc
    return json.loads(body) if body else {}


def update_openwebui_function(base_url: str, api_key_file: Path, output_dir: Path) -> Path:
    token = normalize_api_key(api_key_file.read_text(encoding="utf-8-sig"))
    base = base_url.rstrip("/")
    record = request_json(f"{base}/api/v1/functions/id/{FUNCTION_ID}", token)
    current = str(record.get("content") or "")
    patched = patch_source(current)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / OUTPUT_NAME
    output.write_text(patched, encoding="utf-8", newline="\n")
    payload = {
        "id": FUNCTION_ID,
        "name": str(record.get("name") or "AutoGenBook Companion"),
        "content": patched,
        "meta": record.get("meta") or {"description": "AutoGenBook Companion"},
    }
    request_json(
        f"{base}/api/v1/functions/id/{FUNCTION_ID}/update",
        token,
        method="POST",
        payload=payload,
    )
    verified = request_json(f"{base}/api/v1/functions/id/{FUNCTION_ID}", token)
    if "version: 0.3.2" not in str(verified.get("content") or ""):
        raise PatchError("Open WebUI accepted the request but Function 0.3.2 was not persisted")
    return output


def candidate_files(install_dir: Path | None, explicit: Iterable[Path]) -> list[Path]:
    rows: list[Path] = []
    rows.extend(path.expanduser() for path in explicit)
    roots: list[Path] = []
    if install_dir is not None:
        roots.append(install_dir.expanduser())
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        roots.append(local / "Programs" / "AutoGenBook OpenWebUI")
    roots.append(Path.cwd())
    names = {
        "autogenbook_pipe.py",
        "AutoGenBook-OpenWebUI-Function-v0.3.1.py",
        "function_autogenbook_companion.py",
    }
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name not in names:
                continue
            try:
                head = path.read_text(encoding="utf-8-sig")
            except Exception:
                continue
            if "id: autogenbook_companion" in head and "version: 0.3.1" in head:
                rows.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in rows:
        resolved = path.resolve()
        key = str(resolved).casefold()
        if key not in seen and resolved.is_file():
            seen.add(key)
            unique.append(resolved)
    return unique


def patch_file(path: Path, output_dir: Path) -> Path:
    source = path.read_text(encoding="utf-8-sig")
    patched = patch_source(source)
    backup = path.with_suffix(path.suffix + ".v0.3.1.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(patched, encoding="utf-8", newline="\n")
    temporary.replace(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    standalone = output_dir / OUTPUT_NAME
    standalone.write_text(patched, encoding="utf-8", newline="\n")
    return standalone


def fixture_source() -> str:
    return '''"""\ntitle: AutoGenBook Companion\nid: autogenbook_companion\nversion: 0.3.1\n"""\nfrom __future__ import annotations\nimport asyncio\nimport hashlib\nimport json\nimport mimetypes\nimport os\nimport re\nimport subprocess\nimport threading\nimport sys\nimport time\nimport uuid\nfrom pathlib import Path\nfrom typing import Any, Literal, Optional\nimport httpx\nfrom pydantic import BaseModel, Field\nclass CompanionError(RuntimeError):\n    pass\nclass Pipe:\n    class Valves:\n        PERSIST_OUTPUTS_TO_OPENWEBUI = True\n        PERSIST_OUTPUT_MAX_BYTES = 0\n        PERSIST_OUTPUT_MAX_FILES = 100\n        PERSIST_DIAGNOSTIC_OUTPUTS = False\n        OUTPUT_COPY_CHUNK_BYTES = 1048576\n    class UserValves:\n        pass\n    def __init__(self) -> None:\n        self.valves = self.Valves()\n        self._bridge_cache: dict[str, Any] | None = None\n    @staticmethod\n    async def _await_if_needed(value: Any) -> Any:\n        return await value if hasattr(value, "__await__") else value\n    @staticmethod\n    def _persistent_output_url(file_id: str) -> str:\n        return f"/api/v1/files/{file_id}/content?attachment=true"\n    @staticmethod\n    def _output_file_id(user_id: str, artifact: dict[str, Any]) -> str:\n        return "id"\n    @staticmethod\n    def _output_storage_name(file_id: str, original_name: str, upload_dir: Path) -> str:\n        return original_name\n    @staticmethod\n    def _file_payload(item: Any, persistent_url: str) -> dict[str, Any]:\n        raw = item.model_dump(exclude={"path"}) if hasattr(item, "model_dump") else dict(item)\n        return {"type": "file", "id": raw.get("id"), "url": raw.get("id"), "persistent_url": persistent_url}\n    async def _stream_companion_artifact(self, *args, **kwargs):\n        return 1, "hash"\n    async def _publish_artifact_to_openwebui(\n        self,\n        bridge: dict[str, Any],\n        owner_id: str,\n        artifact: dict[str, Any],\n        user: dict[str, Any],\n        event_emitter: Any,\n    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:\n        enriched = dict(artifact)\n        user_id = str(user.get("id") or "")\n        expected_size = 0\n        file_id = self._output_file_id(user_id, enriched)\n        persistent_url = self._persistent_output_url(file_id)\n        get_file = getattr(Files, "get_file_by_id", None)\n        existing = None\n        if existing is not None:\n            size_matches = True\n            local_exists = True\n            if size_matches and local_exists:\n                payload = self._file_payload(existing, persistent_url)\n                return payload, enriched\n        upload_dir = Path(".")\n        upload_dir.mkdir(parents=True, exist_ok=True)\n        original_name = str(enriched.get("filename") or "artifact.bin")\n        storage_name = self._output_storage_name(file_id, original_name, upload_dir)\n        item = await self._await_if_needed(\n            Files.insert_new_file(\n                user_id,\n                FileForm(\n                    id=file_id, hash="", filename=original_name, path=storage_name,\n                    data={\n                        "status": "completed",\n                        "autogenbook_artifact_id": str(enriched.get("id") or ""),\n                        "autogenbook_job_id": str(enriched.get("job_id") or ""),\n                    },\n                    meta={},\n                ),\n            )\n        )\n        verified = item\n        payload = self._file_payload(verified, persistent_url)\n        return payload, enriched\n    async def _attach_artifacts(\n        self,\n        bridge: dict[str, Any],\n        owner_id: str,\n        job_id: str,\n        user: dict[str, Any],\n        event_emitter: Any,\n        *,\n        include_all: bool = False,\n    ) -> tuple[list[dict[str, Any]], list[str]]:\n        artifact = {"filename": "x"}\n        registered_file, enriched = await self._publish_artifact_to_openwebui(\n            bridge,\n            owner_id,\n            artifact,\n            user,\n            event_emitter,\n        )\n        return [], []\n    async def _job_command(\n        self,\n        command: str,\n        job_id: str,\n        bridge: dict[str, Any],\n        owner_id: str,\n        user: dict[str, Any],\n        event_emitter: Any,\n        event_call: Any = None,\n        user_valves: "Pipe.UserValves | None" = None,\n    ) -> str:\n        artifacts, skipped = await self._attach_artifacts(\n            bridge, owner_id, job_id, user, event_emitter,\n            include_all=(command == "artifacts"),\n        )\n        return "ok"\n    async def pipe(self, __request__: Any = None, __event_emitter__: Any = None, **kwargs):\n        bridge = {}\n        owner_id = "u"\n        user = {"id": "u"}\n        command = "artifacts"\n        command_job_id = "j"\n        __event_call__ = None\n        user_valves = self.UserValves()\n        if command and command_job_id:\n            return await self._job_command(\n                command,\n                command_job_id,\n                bridge,\n                owner_id,\n                user,\n                __event_emitter__,\n                __event_call__,\n                user_valves,\n            )\n        job_id = "j"\n        artifacts, skipped = await self._attach_artifacts(\n            bridge, owner_id, job_id, user, __event_emitter__\n        )\n        return "ok"\n'''


def self_test(report_path: Path | None = None) -> dict:
    patched = patch_source(fixture_source())
    ast.parse(patched)
    result = {
        "version": VERSION,
        "syntax": "passed",
        "capability_route": "_autogenbook_output_capability" in patched,
        "range_support": all(marker in patched for marker in ("Content-Range", "Accept-Ranges", "206", "416")),
        "absolute_card_url": '"url": persistent_url' in patched,
        "nonempty_modal_content": 'data["content"]' in patched,
        "request_threaded": "request=__request__" in patched,
        "old_bearer_endpoint_removed": 'return f"/api/v1/files/{file_id}/content?attachment=true"' not in patched,
    }
    if not all(value == "passed" or value is True or key == "version" for key, value in result.items()):
        raise PatchError(f"Self-test failed: {result}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function-file", type=Path, action="append", default=[])
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--openwebui-url", default="")
    parser.add_argument("--webui-api-key-file", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.self_test:
        print(json.dumps(self_test(args.report), ensure_ascii=False, indent=2))
        return 0

    results: list[dict[str, str]] = []
    output_dir = args.output_dir.resolve()
    if args.openwebui_url and args.webui_api_key_file:
        output = update_openwebui_function(
            args.openwebui_url,
            args.webui_api_key_file.expanduser().resolve(),
            output_dir,
        )
        results.append({"source": "openwebui_api", "output": str(output), "status": "updated"})

    for path in candidate_files(args.install_dir, args.function_file):
        output = patch_file(path, output_dir)
        results.append({"source": str(path), "output": str(output), "status": "updated"})

    if not results:
        raise PatchError(
            "No AutoGenBook Function 0.3.1 was found. Supply --function-file, or supply both "
            "--openwebui-url and --webui-api-key-file for an automatic admin API update."
        )
    report = {
        "version": VERSION,
        "results": results,
        "self_test": self_test(),
    }
    report_path = args.report or output_dir / "AutoGenBook-OpenWebUI-v0.3.2-UPDATE-REPORT.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"AutoGenBook v0.3.2 update failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
