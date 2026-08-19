from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

DEFAULT_LLM_MODEL = "e-infra.glm-5"
_PROGRESS_PREFIX = "AUTOGENBOOK_PROGRESS "
_STATE_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "progress": 0.0,
    "stage": "queued",
    "message": "Úloha čeká na spuštění.",
    "current": None,
    "total": None,
    "started_at": None,
    "updated_at": None,
    "heartbeat_at": None,
    "model": DEFAULT_LLM_MODEL,
    "status": "queued",
}
_PATCHED_OPENAI_CLASSES: set[type[Any]] = set()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def selected_llm_model(explicit: str | None = None) -> str:
    candidates = (
        explicit,
        os.environ.get("AUTOGENBOOK_LLM_MODEL"),
        os.environ.get("OPENWEBUI_MODEL"),
        os.environ.get("OPENAI_MODEL"),
        os.environ.get("OPENROUTER_MODEL"),
        os.environ.get("LLM_MODEL"),
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return DEFAULT_LLM_MODEL


def configure_model_environment(explicit: str | None = None) -> str:
    model = selected_llm_model(explicit)
    for name in (
        "AUTOGENBOOK_LLM_MODEL",
        "OPENWEBUI_MODEL",
        "OPENAI_MODEL",
        "OPENROUTER_MODEL",
        "LLM_MODEL",
        "MODEL_NAME",
    ):
        os.environ[name] = model
    with _STATE_LOCK:
        _STATE["model"] = model
    return model


def _first_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value).expanduser().resolve()
    return None


def job_root() -> Path:
    explicit = _first_path(
        "AUTOGENBOOK_JOB_DIR",
        "AUTOGENBOOK_WORK_DIR",
        "AUTOGENBOOK_OUTPUT_DIR",
    )
    if explicit is not None:
        explicit.mkdir(parents=True, exist_ok=True)
        return explicit
    fallback = Path.cwd().resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def progress_path() -> Path:
    explicit = _first_path("AUTOGENBOOK_PROGRESS_FILE")
    if explicit is not None:
        explicit.parent.mkdir(parents=True, exist_ok=True)
        return explicit
    return job_root() / ".autogenbook-progress.json"


def artifact_manifest_path() -> Path:
    explicit = _first_path("AUTOGENBOOK_ARTIFACT_MANIFEST")
    if explicit is not None:
        explicit.parent.mkdir(parents=True, exist_ok=True)
        return explicit
    return job_root() / ".autogenbook-artifacts.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_artifacts(root: Path | None = None, *, max_files: int = 20_000) -> list[dict[str, Any]]:
    root = (root or job_root()).resolve()
    if not root.exists():
        return []
    ignored_names = {
        progress_path().name,
        artifact_manifest_path().name,
        ".DS_Store",
    }
    ignored_parts = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if len(rows) >= max_files:
            break
        if not path.is_file() or path.name in ignored_names:
            continue
        relative = path.relative_to(root)
        if any(part in ignored_parts for part in relative.parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        row: dict[str, Any] = {
            "path": relative.as_posix(),
            "name": path.name,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        # Hashing multi-gigabyte outputs on every heartbeat would delay a running job.
        # Large files remain downloadable; their digest can be computed after completion.
        if stat.st_size <= 64 * 1024 * 1024:
            with contextlib.suppress(OSError):
                row["sha256"] = _sha256(path)
        rows.append(row)
    return rows


def write_artifact_manifest(*, final: bool = False) -> list[dict[str, Any]]:
    rows = collect_artifacts()
    if final:
        for row in rows:
            if "sha256" in row:
                continue
            path = job_root() / str(row["path"])
            with contextlib.suppress(OSError):
                row["sha256"] = _sha256(path)
    _atomic_write_json(
        artifact_manifest_path(),
        {
            "generated_at": utc_now(),
            "root": str(job_root()),
            "files": rows,
        },
    )
    return rows


def emit_progress(
    progress: float,
    stage: str,
    message: str,
    *,
    current: int | None = None,
    total: int | None = None,
    status: str = "running",
    allow_decrease: bool = False,
    scan_artifacts: bool = False,
) -> dict[str, Any]:
    now = utc_now()
    with _STATE_LOCK:
        previous = float(_STATE.get("progress") or 0.0)
        normalized = max(0.0, min(100.0, float(progress)))
        if not allow_decrease and status == "running":
            normalized = max(previous, normalized)
        if _STATE.get("started_at") is None and status in {"running", "completed", "failed"}:
            _STATE["started_at"] = now
        _STATE.update(
            {
                "progress": round(normalized, 2),
                "stage": str(stage or "running"),
                "message": str(message or stage or "Probíhá zpracování."),
                "current": current,
                "total": total,
                "updated_at": now,
                "heartbeat_at": now,
                "status": status,
                "model": selected_llm_model(),
                "pid": os.getpid(),
            }
        )
        payload = dict(_STATE)
    if scan_artifacts:
        with contextlib.suppress(Exception):
            payload["artifacts"] = write_artifact_manifest(final=status == "completed")
    _atomic_write_json(progress_path(), payload)
    print(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), file=sys.__stdout__, flush=True)
    return payload


def heartbeat() -> dict[str, Any]:
    with _STATE_LOCK:
        payload = dict(_STATE)
    return emit_progress(
        float(payload.get("progress") or 0.0),
        str(payload.get("stage") or "running"),
        str(payload.get("message") or "Probíhá zpracování."),
        current=payload.get("current"),
        total=payload.get("total"),
        status=str(payload.get("status") or "running"),
    )


def _progress_from_line(line: str) -> tuple[float, str, str, int | None, int | None] | None:
    compact = " ".join(line.strip().split())
    if not compact:
        return None
    lowered = compact.casefold()
    ratio = re.search(r"(?<!\d)(\d{1,7})\s*(?:/|of|z)\s*(\d{1,7})(?!\d)", lowered)
    if ratio:
        current = int(ratio.group(1))
        total = int(ratio.group(2))
        if total > 0 and 0 <= current <= total:
            percent = 15.0 + (75.0 * current / total)
            return percent, "generation", compact[:500], current, total
    stages: tuple[tuple[tuple[str, ...], float, str], ...] = (
        (("start", "initial", "načít", "loading", "config"), 2.0, "initializing"),
        (("prompt", "input", "vstup"), 5.0, "input"),
        (("outline", "structure", "osnov", "strukt"), 10.0, "structure"),
        (("research", "search", "rešer", "zdroj"), 20.0, "research"),
        (("chapter", "kapitol", "section", "sekc", "slide", "snímek"), 35.0, "generation"),
        (("citation", "reference", "bibliograf", "citac"), 86.0, "references"),
        (("render", "export", "latex", "docx", "pptx", "pdf", "audio", "video"), 92.0, "rendering"),
        (("package", "archive", "zip", "tar", "balen"), 98.0, "packaging"),
    )
    for needles, percent, stage in stages:
        if any(needle in lowered for needle in needles):
            return percent, stage, compact[:500], None, None
    return None


class ProgressTee:
    def __init__(self, wrapped: TextIO):
        self._wrapped = wrapped
        self._buffer = ""

    def write(self, value: str) -> int:
        written = self._wrapped.write(value)
        self._wrapped.flush()
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            parsed = _progress_from_line(line)
            if parsed is not None:
                progress, stage, message, current, total = parsed
                with contextlib.suppress(Exception):
                    emit_progress(progress, stage, message, current=current, total=total)
        return written

    def flush(self) -> None:
        self._wrapped.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._wrapped, "isatty", lambda: False)())

    def fileno(self) -> int:
        return self._wrapped.fileno()

    @property
    def encoding(self) -> str | None:
        return getattr(self._wrapped, "encoding", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _patch_completion_class(cls: type[Any]) -> None:
    if cls in _PATCHED_OPENAI_CLASSES:
        return
    original = getattr(cls, "create", None)
    if not callable(original):
        return

    if getattr(original, "_autogenbook_model_override", False):
        _PATCHED_OPENAI_CLASSES.add(cls)
        return

    if getattr(original, "__name__", "").startswith("async"):
        async def create(self: Any, *args: Any, **kwargs: Any) -> Any:
            kwargs["model"] = selected_llm_model(kwargs.get("model"))
            return await original(self, *args, **kwargs)
    else:
        def create(self: Any, *args: Any, **kwargs: Any) -> Any:
            kwargs["model"] = selected_llm_model(kwargs.get("model"))
            return original(self, *args, **kwargs)

    setattr(create, "_autogenbook_model_override", True)
    setattr(cls, "create", create)
    _PATCHED_OPENAI_CLASSES.add(cls)


def install_openai_model_override() -> None:
    """Force every OpenAI-compatible chat request to use the persisted job model.

    AutoGenBook has several generation paths.  Patching the SDK resource layer keeps
    the model selection consistent even when an older path still supplies its own
    historical default.
    """
    configure_model_environment()
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions  # type: ignore
    except Exception:
        return
    _patch_completion_class(Completions)
    _patch_completion_class(AsyncCompletions)


class HeartbeatThread:
    def __init__(self, interval_seconds: float = 30.0):
        self.interval_seconds = max(5.0, interval_seconds)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="autogenbook-progress", daemon=True)
        self._ticks = 0

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=min(5.0, self.interval_seconds))

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            self._ticks += 1
            with contextlib.suppress(Exception):
                heartbeat()
            # Refresh the file list every ten minutes, not on every heartbeat.
            if self._ticks % max(1, int(600 / self.interval_seconds)) == 0:
                with contextlib.suppress(Exception):
                    write_artifact_manifest()


def run_main(main_callable: Callable[[], Any]) -> Any:
    model = configure_model_environment()
    install_openai_model_override()
    emit_progress(1.0, "initializing", f"AutoGenBook se spouští s modelem {model}.")
    watcher = HeartbeatThread(float(os.environ.get("AUTOGENBOOK_HEARTBEAT_SECONDS", "30")))
    watcher.start()
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = ProgressTee(original_stdout)  # type: ignore[assignment]
    sys.stderr = ProgressTee(original_stderr)  # type: ignore[assignment]
    try:
        result = main_callable()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if code == 0:
            emit_progress(100.0, "completed", "Generování bylo dokončeno.", status="completed", scan_artifacts=True)
        else:
            emit_progress(
                float(_STATE.get("progress") or 0.0),
                "failed",
                f"AutoGenBook skončil s návratovým kódem {code}.",
                status="failed",
                scan_artifacts=True,
            )
        raise
    except BaseException as exc:
        emit_progress(
            float(_STATE.get("progress") or 0.0),
            "failed",
            f"Generování selhalo: {type(exc).__name__}: {exc}",
            status="failed",
            scan_artifacts=True,
        )
        raise
    else:
        code = result if isinstance(result, int) else 0
        if code == 0:
            emit_progress(100.0, "completed", "Generování bylo dokončeno.", status="completed", scan_artifacts=True)
        else:
            emit_progress(
                float(_STATE.get("progress") or 0.0),
                "failed",
                f"AutoGenBook skončil s návratovým kódem {code}.",
                status="failed",
                scan_artifacts=True,
            )
        return result
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        watcher.stop()
