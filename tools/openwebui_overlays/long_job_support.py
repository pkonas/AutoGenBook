from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import inspect
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Mapping
from urllib.parse import quote

DEFAULT_LLM_MODEL = "e-infra.glm-5"
_TERMINAL_STATES = {"completed", "complete", "succeeded", "success", "failed", "error", "cancelled", "canceled", "interrupted"}
_INTERNAL_FILES = {
    ".autogenbook-progress.json",
    ".autogenbook-artifacts.json",
    "progress.json",
    "artifacts.json",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        with contextlib.suppress(Exception):
            value = value.decode("utf-8")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in {"{", "["}:
            with contextlib.suppress(Exception):
                return json.loads(stripped)
    return value


def _as_mapping(value: Any) -> dict[str, Any]:
    value = _json_value(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    for method_name in ("model_dump", "dict", "to_dict", "as_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            with contextlib.suppress(Exception):
                result = method()
                if isinstance(result, Mapping):
                    return {str(key): _json_value(item) for key, item in result.items()}
    with contextlib.suppress(Exception):
        return {str(key): _json_value(item) for key, item in vars(value).items() if not str(key).startswith("_")}
    return {}


def _walk_values(value: Any, *, depth: int = 0, seen: set[int] | None = None) -> Iterable[Any]:
    if depth > 4:
        return
    if seen is None:
        seen = set()
    identifier = id(value)
    if identifier in seen:
        return
    seen.add(identifier)
    yield value
    mapping = _as_mapping(value)
    for child in mapping.values():
        if isinstance(child, (Mapping, list, tuple, set)) or hasattr(child, "__dict__"):
            yield from _walk_values(child, depth=depth + 1, seen=seen)
    if isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _walk_values(child, depth=depth + 1, seen=seen)


def _candidate_setting_values(namespace: Mapping[str, Any], names: Iterable[str]) -> list[str]:
    wanted = {name.casefold() for name in names}
    values: list[str] = []
    for root in _walk_values(namespace):
        mapping = _as_mapping(root)
        for key, value in mapping.items():
            folded = key.casefold()
            if folded in wanted or any(name in folded for name in wanted):
                text = str(value or "").strip()
                if text:
                    values.append(text)
    return values


def _configured_bearer(namespace: Mapping[str, Any]) -> str:
    for env_name in (
        "AUTOGENBOOK_COMPANION_TOKEN",
        "AUTOGENBOOK_API_TOKEN",
        "COMPANION_API_TOKEN",
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    candidates = _candidate_setting_values(
        namespace,
        ("api_token", "auth_token", "companion_token", "bearer_token", "access_token"),
    )
    return candidates[0] if candidates else ""


def _path_from_value(value: Any) -> Path | None:
    if isinstance(value, Path):
        return value.expanduser().resolve()
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 4096 or "\n" in text:
        return None
    if not any(marker in text for marker in ("/", "\\", ".db", ".sqlite")):
        return None
    with contextlib.suppress(Exception):
        path = Path(text).expanduser().resolve()
        return path
    return None


def _data_roots(namespace: Mapping[str, Any]) -> list[Path]:
    rows: list[Path] = []
    for env_name in (
        "AUTOGENBOOK_DATA_DIR",
        "AUTOGENBOOK_COMPANION_DATA_DIR",
        "AUTOGENBOOK_JOBS_DIR",
        "AUTOGENBOOK_WORK_DIR",
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            rows.append(Path(value).expanduser().resolve())
    for root in _walk_values(namespace):
        mapping = _as_mapping(root)
        for key, value in mapping.items():
            folded = key.casefold()
            if any(token in folded for token in ("data_dir", "jobs_dir", "work_dir", "output_dir", "database", "db_path")):
                path = _path_from_value(value)
                if path is not None:
                    rows.append(path.parent if path.suffix in {".db", ".sqlite", ".sqlite3"} else path)
    rows.extend(
        (
            Path.cwd().resolve(),
            (Path.home() / ".local" / "share" / "AutoGenBook").resolve(),
            (Path.home() / ".autogenbook").resolve(),
        )
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for row in rows:
        key = os.path.normcase(str(row))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _download_secret(namespace: Mapping[str, Any]) -> bytes:
    configured = _configured_bearer(namespace)
    if configured:
        return hashlib.sha256(("download:" + configured).encode("utf-8")).digest()
    roots = _data_roots(namespace)
    root = next((item for item in roots if item.exists() and item.is_dir()), roots[0])
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".autogenbook-download-secret"
    if path.exists():
        with contextlib.suppress(OSError):
            payload = path.read_bytes()
            if len(payload) >= 32:
                return payload[:64]
    payload = secrets.token_bytes(48)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    with contextlib.suppress(OSError):
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return payload


def _token_payload(job_id: str, relative_path: str, expires: int) -> bytes:
    return f"{job_id}\n{relative_path}\n{expires}".encode("utf-8")


def _sign_download(secret: bytes, job_id: str, relative_path: str, *, ttl_seconds: int = 86_400) -> str:
    expires = int(time.time()) + max(60, int(ttl_seconds))
    signature = hmac.new(secret, _token_payload(job_id, relative_path, expires), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{expires}.{encoded}"


def _verify_download(secret: bytes, token: str, job_id: str, relative_path: str) -> bool:
    try:
        expires_text, encoded = token.split(".", 1)
        expires = int(expires_text)
    except (TypeError, ValueError):
        return False
    if expires < int(time.time()):
        return False
    padding = "=" * (-len(encoded) % 4)
    with contextlib.suppress(Exception):
        supplied = base64.urlsafe_b64decode(encoded + padding)
        expected = hmac.new(secret, _token_payload(job_id, relative_path, expires), hashlib.sha256).digest()
        return hmac.compare_digest(supplied, expected)
    return False


def _loopback_host(host: str | None) -> bool:
    return str(host or "").casefold() in {"127.0.0.1", "::1", "localhost", "testclient"}


def _authorized(request: Any, namespace: Mapping[str, Any], *, job_id: str = "", relative_path: str = "") -> bool:
    signed = str(request.query_params.get("token", "") or "")
    if signed and job_id and _verify_download(_download_secret(namespace), signed, job_id, relative_path):
        return True
    configured = _configured_bearer(namespace)
    supplied = str(request.headers.get("authorization", "") or "")
    if supplied.casefold().startswith("bearer "):
        supplied = supplied[7:].strip()
    if configured:
        return hmac.compare_digest(supplied, configured)
    client = getattr(request, "client", None)
    return _loopback_host(getattr(client, "host", None))


async def _record_from_store(namespace: Mapping[str, Any], job_id: str) -> dict[str, Any]:
    method_names = ("get_job", "fetch_job", "load_job", "find_job", "get", "fetch", "load")
    for value in _walk_values(namespace):
        class_name = type(value).__name__.casefold()
        if not any(token in class_name for token in ("store", "repository", "database", "job")):
            continue
        for name in method_names:
            method = getattr(value, name, None)
            if not callable(method):
                continue
            try:
                result = method(job_id)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                continue
            mapping = _as_mapping(result)
            if mapping:
                return mapping
    return {}


def _walk_limited(root: Path, *, max_depth: int = 5) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return
    root_depth = len(root.parts)
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.parts) - root_depth
        directories[:] = [item for item in directories if item not in {".git", "__pycache__", "node_modules"}]
        if depth >= max_depth:
            directories[:] = []
        for file_name in files:
            yield current_path / file_name


def _record_from_sqlite(namespace: Mapping[str, Any], job_id: str) -> dict[str, Any]:
    database_paths: list[Path] = []
    for root in _data_roots(namespace):
        if root.is_file() and root.suffix in {".db", ".sqlite", ".sqlite3"}:
            database_paths.append(root)
            continue
        for path in _walk_limited(root, max_depth=4):
            if path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
                database_paths.append(path)
    seen: set[str] = set()
    for database in database_paths:
        key = os.path.normcase(str(database))
        if key in seen:
            continue
        seen.add(key)
        try:
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            tables = [str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                if not re.fullmatch(r"[A-Za-z0-9_]+", table):
                    continue
                columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
                id_column = next((item for item in ("job_id", "id", "run_id") if item in columns), None)
                if id_column is None:
                    continue
                try:
                    row = connection.execute(
                        f'SELECT * FROM "{table}" WHERE "{id_column}" = ? LIMIT 1',
                        (job_id,),
                    ).fetchone()
                except sqlite3.Error:
                    continue
                if row is not None:
                    return {str(column): _json_value(row[column]) for column in row.keys()}
        finally:
            connection.close()
    return {}


async def _job_record(namespace: Mapping[str, Any], job_id: str) -> dict[str, Any]:
    record = await _record_from_store(namespace, job_id)
    return record or await asyncio.to_thread(_record_from_sqlite, namespace, job_id)


def _paths_in_value(value: Any, *, depth: int = 0) -> Iterable[Path]:
    if depth > 6:
        return
    path = _path_from_value(value)
    if path is not None:
        yield path
    value = _json_value(value)
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _paths_in_value(child, depth=depth + 1)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _paths_in_value(child, depth=depth + 1)


def _locate_job_root(namespace: Mapping[str, Any], job_id: str, record: Mapping[str, Any]) -> Path | None:
    candidates: list[Path] = []
    candidates.extend(_paths_in_value(record))
    for root in _data_roots(namespace):
        candidates.extend((root / job_id, root / "jobs" / job_id, root / "runs" / job_id, root / "work" / job_id))
    for candidate in candidates:
        if candidate.is_file():
            candidate = candidate.parent
        if candidate.exists() and candidate.is_dir():
            if candidate.name == job_id or job_id in candidate.parts:
                return candidate.resolve()
            for child in (candidate / job_id, candidate / "jobs" / job_id, candidate / "runs" / job_id):
                if child.exists() and child.is_dir():
                    return child.resolve()
    for root in _data_roots(namespace):
        if not root.exists() or not root.is_dir():
            continue
        root_depth = len(root.parts)
        for current, directories, _ in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            if current_path.name == job_id:
                return current_path.resolve()
            directories[:] = [item for item in directories if item not in {".git", "__pycache__", "node_modules"}]
            if depth >= 5:
                directories[:] = []
    return None


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    return None


def _record_status(record: Mapping[str, Any]) -> str:
    for name in ("status", "state", "phase", "conclusion"):
        value = str(record.get(name, "") or "").strip().casefold()
        if value:
            return value
    return "unknown"


def _progress_file(root: Path) -> Path | None:
    candidates = (
        root / ".autogenbook-progress.json",
        root / "progress.json",
        root / "output" / ".autogenbook-progress.json",
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    for candidate in _walk_limited(root, max_depth=3):
        if candidate.name in {".autogenbook-progress.json", "progress.json"}:
            return candidate
    return None


def _last_log_message(root: Path) -> tuple[str, float | None]:
    logs = [path for path in _walk_limited(root, max_depth=3) if path.suffix.casefold() in {".log", ".txt"}]
    logs.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    for path in logs[:5]:
        with contextlib.suppress(OSError):
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 64 * 1024))
                text = stream.read().decode("utf-8", errors="replace")
            lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
            if lines:
                return lines[-1][:1000], path.stat().st_mtime
    return "", None


def _fallback_progress(record: Mapping[str, Any], root: Path | None) -> dict[str, Any]:
    state = _record_status(record)
    if state in {"completed", "complete", "succeeded", "success"}:
        percent = 100.0
    elif state in {"failed", "error", "cancelled", "canceled", "interrupted"}:
        percent = float(record.get("progress", 0.0) or 0.0)
    elif state in {"running", "started", "processing"}:
        percent = max(1.0, float(record.get("progress", 1.0) or 1.0))
    else:
        percent = float(record.get("progress", 0.0) or 0.0)
    message = str(record.get("message") or record.get("status_message") or "")
    heartbeat_at: str | None = None
    if root is not None:
        log_message, modified = _last_log_message(root)
        message = log_message or message
        if modified is not None:
            heartbeat_at = datetime.fromtimestamp(modified, timezone.utc).isoformat().replace("+00:00", "Z")
        ratio = re.search(r"(?<!\d)(\d+)\s*(?:/|of|z)\s*(\d+)(?!\d)", message.casefold())
        if ratio and int(ratio.group(2)) > 0:
            percent = max(percent, 15.0 + 75.0 * int(ratio.group(1)) / int(ratio.group(2)))
    return {
        "progress": round(min(100.0, percent), 2),
        "stage": str(record.get("stage") or state or "unknown"),
        "message": message or "Stav úlohy je uložen, ale zatím nebyla zaznamenána podrobnější zpráva.",
        "status": state,
        "heartbeat_at": heartbeat_at,
        "model": str(record.get("llm_model") or record.get("model") or DEFAULT_LLM_MODEL),
    }


def _progress_payload(job_id: str, record: Mapping[str, Any], root: Path | None) -> dict[str, Any]:
    payload = _fallback_progress(record, root)
    if root is not None:
        path = _progress_file(root)
        if path is not None:
            with contextlib.suppress(Exception):
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, Mapping):
                    payload.update({str(key): item for key, item in value.items()})
                payload["progress_file"] = str(path.relative_to(root).as_posix())
                payload["heartbeat_at"] = payload.get("heartbeat_at") or datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                ).isoformat().replace("+00:00", "Z")
    payload["job_id"] = job_id
    payload["root_available"] = root is not None
    heartbeat_epoch = _parse_timestamp(payload.get("heartbeat_at"))
    state = str(payload.get("status") or "").casefold()
    if state in {"running", "started", "processing"} and heartbeat_epoch is not None:
        stale_seconds = max(0, int(time.time() - heartbeat_epoch))
        payload["heartbeat_age_seconds"] = stale_seconds
        if stale_seconds > 600:
            payload["stale"] = True
            payload["message"] = (
                str(payload.get("message") or "")
                + " Poslední heartbeat je starší než 10 minut; ověřte, zda Companion stále běží."
            ).strip()
    return payload


def _safe_relative(root: Path, relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/").lstrip("/")
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PermissionError("Požadovaná cesta opouští adresář úlohy.") from exc
    return candidate


def _artifact_manifest(root: Path) -> list[dict[str, Any]]:
    candidates = (
        root / ".autogenbook-artifacts.json",
        root / "artifacts.json",
        root / "output" / ".autogenbook-artifacts.json",
    )
    for path in candidates:
        if not path.exists():
            continue
        with contextlib.suppress(Exception):
            value = json.loads(path.read_text(encoding="utf-8"))
            rows = value.get("files", value) if isinstance(value, Mapping) else value
            if isinstance(rows, list):
                return [dict(item) for item in rows if isinstance(item, Mapping)]
    return []


def _artifact_rows(root: Path, *, max_files: int = 20_000) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for row in _artifact_manifest(root):
        relative = str(row.get("path") or "").replace("\\", "/").lstrip("/")
        if not relative:
            continue
        with contextlib.suppress(PermissionError):
            path = _safe_relative(root, relative)
            if path.exists() and path.is_file():
                stat = path.stat()
                merged = dict(row)
                merged.update({"path": relative, "name": path.name, "size": stat.st_size})
                by_path[relative] = merged
    for path in _walk_limited(root, max_depth=8):
        if len(by_path) >= max_files:
            break
        if path.name in _INTERNAL_FILES or any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        with contextlib.suppress(OSError):
            relative = path.relative_to(root).as_posix()
            stat = path.stat()
            by_path.setdefault(
                relative,
                {
                    "path": relative,
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            )
    priority_extensions = {".zip", ".tar", ".gz", ".pdf", ".docx", ".pptx", ".mp4", ".wav"}
    rows = list(by_path.values())
    rows.sort(
        key=lambda item: (
            0 if Path(str(item["path"])).suffix.casefold() in priority_extensions else 1,
            -int(item.get("size", 0)),
            str(item["path"]),
        )
    )
    return rows


def _parse_range(range_header: str, size: int) -> tuple[int, int] | None:
    if not range_header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if match is None or "," in range_header:
        raise ValueError("Podporován je právě jeden byte range.")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("Prázdný byte range.")
    if not start_text:
        length = int(end_text)
        if length <= 0:
            raise ValueError("Neplatná délka suffix range.")
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError("Byte range je mimo soubor.")
    return start, min(end, size - 1)


def _file_chunks(path: Path, start: int, end: int, *, chunk_size: int = 1024 * 1024) -> Iterable[bytes]:
    remaining = end - start + 1
    with path.open("rb") as stream:
        stream.seek(start)
        while remaining > 0:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def install_long_job_support(app: Any, namespace: Mapping[str, Any] | None = None) -> Any:
    """Attach restart-safe progress and large-file endpoints to the Companion app."""
    namespace = dict(namespace or {})
    state = getattr(app, "state", None)
    if state is not None and getattr(state, "autogenbook_long_job_support", False):
        return app
    if state is not None:
        state.autogenbook_long_job_support = True
    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    async def context(job_id: str) -> tuple[dict[str, Any], Path | None]:
        record = await _job_record(namespace, job_id)
        root = await asyncio.to_thread(_locate_job_root, namespace, job_id, record)
        return record, root

    @app.get(
        "/api/v1/autogenbook/jobs/{job_id}/progress",
        name="autogenbook_job_progress",
    )
    async def job_progress(job_id: str, request: Request) -> JSONResponse:
        if not _authorized(request, namespace):
            raise HTTPException(status_code=401, detail="Neplatné oprávnění.")
        record, root = await context(job_id)
        if not record and root is None:
            raise HTTPException(status_code=404, detail="Úloha nebyla nalezena.")
        payload = await asyncio.to_thread(_progress_payload, job_id, record, root)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.get(
        "/api/v1/autogenbook/jobs/{job_id}/files",
        name="autogenbook_job_files",
    )
    async def job_files(job_id: str, request: Request) -> JSONResponse:
        if not _authorized(request, namespace):
            raise HTTPException(status_code=401, detail="Neplatné oprávnění.")
        record, root = await context(job_id)
        if root is None:
            if not record:
                raise HTTPException(status_code=404, detail="Úloha nebyla nalezena.")
            return JSONResponse({"job_id": job_id, "files": [], "message": "Výstupní adresář zatím není dostupný."})
        rows = await asyncio.to_thread(_artifact_rows, root)
        secret = _download_secret(namespace)
        base = str(request.base_url).rstrip("/")
        for row in rows:
            relative = str(row["path"])
            token = _sign_download(secret, job_id, relative)
            row["download_url"] = (
                f"{base}/api/v1/autogenbook/jobs/{quote(job_id, safe='')}/files/"
                f"{quote(relative, safe='/')}?token={quote(token, safe='')}"
            )
            row["supports_resume"] = True
        return JSONResponse(
            {"job_id": job_id, "count": len(rows), "files": rows, "generated_at": _utc_now()},
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        "/api/v1/autogenbook/jobs/{job_id}/files/{relative_path:path}",
        name="autogenbook_download_file",
    )
    async def download_file(job_id: str, relative_path: str, request: Request) -> StreamingResponse:
        if not _authorized(request, namespace, job_id=job_id, relative_path=relative_path):
            raise HTTPException(status_code=401, detail="Neplatné nebo expirované oprávnění.")
        record, root = await context(job_id)
        if root is None:
            raise HTTPException(status_code=404, detail="Výstupní adresář nebyl nalezen.")
        try:
            path = _safe_relative(root, relative_path)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Soubor nebyl nalezen.")
        size = path.stat().st_size
        try:
            byte_range = _parse_range(str(request.headers.get("range", "") or ""), size)
        except ValueError as exc:
            raise HTTPException(
                status_code=416,
                detail=str(exc),
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            ) from exc
        start, end = byte_range or (0, max(0, size - 1))
        status_code = 206 if byte_range is not None else 200
        content_length = max(0, end - start + 1)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        disposition_name = quote(path.name, safe="")
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f"attachment; filename*=UTF-8''{disposition_name}",
            "Cache-Control": "private, max-age=0, must-revalidate",
            "X-Content-Type-Options": "nosniff",
        }
        if byte_range is not None:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(
            _file_chunks(path, start, end),
            status_code=status_code,
            media_type=media_type,
            headers=headers,
        )

    return app
