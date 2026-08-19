
# AUTOGENBOOK_OPENWEBUI_LLM_PROGRESS_EXTENSION
from __future__ import annotations

import asyncio as _agb_asyncio
import contextvars as _agb_contextvars
import inspect as _agb_inspect
import json as _agb_json
import os as _agb_os
import re as _agb_re
from collections.abc import Mapping as _AgbMapping
from typing import Any as _AgbAny
from urllib.parse import urlsplit as _agb_urlsplit

from pydantic import BaseModel as _AgbBaseModel, Field as _AgbField

_AGB_DEFAULT_MODEL = "e-infra.glm-5"
_AGB_MODEL = _agb_contextvars.ContextVar("autogenbook_llm_model", default=_AGB_DEFAULT_MODEL)
_AGB_EMITTER = _agb_contextvars.ContextVar("autogenbook_event_emitter", default=None)
_AGB_JOB_ID = _agb_contextvars.ContextVar("autogenbook_job_id", default="")
_AGB_COMPANION_ORIGIN = _agb_contextvars.ContextVar("autogenbook_companion_origin", default="")
_AGB_COMPANION_HEADERS = _agb_contextvars.ContextVar("autogenbook_companion_headers", default={})


def _agb_mapping(value: _AgbAny) -> dict[str, _AgbAny]:
    if isinstance(value, _AgbMapping):
        return {str(key): item for key, item in value.items()}
    for name in ("model_dump", "dict", "to_dict"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                result = method()
            except Exception:
                continue
            if isinstance(result, _AgbMapping):
                return {str(key): item for key, item in result.items()}
    try:
        return {str(key): item for key, item in vars(value).items() if not str(key).startswith("_")}
    except Exception:
        return {}


def _agb_normalize_models(value: _AgbAny) -> list[str]:
    rows: list[str] = []
    if isinstance(value, _AgbMapping):
        for key in ("data", "models", "items", "results"):
            if key in value:
                rows.extend(_agb_normalize_models(value[key]))
        if any(key in value for key in ("id", "model", "model_id", "name")):
            for key in ("id", "model_id", "model", "name"):
                text = str(value.get(key, "") or "").strip()
                if text:
                    rows.append(text)
                    break
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            rows.extend(_agb_normalize_models(item))
    elif isinstance(value, str):
        rows.extend(item.strip() for item in _agb_re.split(r"[,\n]", value) if item.strip())
    else:
        mapping = _agb_mapping(value)
        if mapping:
            rows.extend(_agb_normalize_models(mapping))
    unique: list[str] = []
    seen: set[str] = set()
    for row in [_AGB_DEFAULT_MODEL, *rows]:
        value = str(row or "").strip()
        if not value or value in seen:
            continue
        # Internal utility/manifold identifiers are not useful as generation LLMs.
        if value.casefold().startswith(("autogenbook", "pipe.")):
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _agb_available_models(extra: _AgbAny = None) -> list[str]:
    values: list[_AgbAny] = [extra]
    for env_name in ("OPENWEBUI_MODELS", "AUTOGENBOOK_OPENWEBUI_MODELS"):
        if _agb_os.environ.get(env_name):
            values.append(_agb_os.environ[env_name])
    module_names = (
        "open_webui.utils.models",
        "open_webui.models.models",
        "open_webui.routers.models",
        "open_webui.routers.openai",
    )
    function_names = (
        "get_all_models",
        "get_models",
        "get_model_list",
        "get_all_base_models",
    )
    for module_name in module_names:
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception:
            continue
        for function_name in function_names:
            function = getattr(module, function_name, None)
            if not callable(function):
                continue
            try:
                result = function()
            except Exception:
                continue
            if not _agb_inspect.isawaitable(result):
                values.append(result)
        models_class = getattr(module, "Models", None)
        if models_class is not None:
            for function_name in function_names:
                function = getattr(models_class, function_name, None)
                if not callable(function):
                    continue
                try:
                    result = function()
                except Exception:
                    continue
                if not _agb_inspect.isawaitable(result):
                    values.append(result)
    rows: list[str] = []
    for value in values:
        rows.extend(_agb_normalize_models(value))
    return _agb_normalize_models(rows)


def _agb_model_schema(models: list[str]) -> dict[str, _AgbAny]:
    options = [{"label": model, "value": model} for model in models]
    # Open WebUI versions have used both enum and select/options metadata.
    # Supplying both keeps the Valve a combo box across supported desktop releases.
    return {
        "type": "select",
        "enum": models,
        "options": options,
        "ui:widget": "select",
        "x-openwebui-type": "model",
        "x-openwebui-options-source": "models",
    }


def _agb_refresh_model_schema(extra: _AgbAny = None) -> list[str]:
    models = _agb_available_models(extra)
    user_valves = getattr(Pipe, "UserValves", None)
    fields = getattr(user_valves, "model_fields", {})
    field = fields.get("llm_model") if isinstance(fields, dict) else None
    if field is not None:
        field.json_schema_extra = _agb_model_schema(models)
    return models


_AgbOriginalUserValves = getattr(Pipe, "UserValves", _AgbBaseModel)
_AGB_INITIAL_MODELS = _agb_available_models()


class _AgbUserValves(_AgbOriginalUserValves):
    llm_model: str = _AgbField(
        default=_AGB_DEFAULT_MODEL,
        title="LLM model",
        description="Model dostupný v Open WebUI, který bude použit pro celou úlohu AutoGenBook.",
        json_schema_extra=_agb_model_schema(_AGB_INITIAL_MODELS),
    )
    show_progress: bool = _AgbField(
        default=True,
        title="Zobrazovat průběh",
        description="Pravidelně aktualizovat stav dlouhotrvající úlohy v konverzaci.",
    )
    progress_interval_seconds: int = _AgbField(
        default=15,
        ge=5,
        le=300,
        title="Interval průběhu (s)",
    )
    foreground_wait_minutes: int = _AgbField(
        default=5,
        ge=0,
        le=30,
        title="Sledovat v popředí (min)",
        description=(
            "Po uplynutí limitu se požadavek ukončí, ale úloha pokračuje v Companionu. "
            "Stav lze kdykoliv obnovit příkazem /autogenbook status ID."
        ),
    )


Pipe.UserValves = _AgbUserValves


def _agb_user_valves(pipe: _AgbAny, user: _AgbAny) -> dict[str, _AgbAny]:
    candidates: list[_AgbAny] = []
    user_mapping = _agb_mapping(user)
    if user_mapping:
        candidates.extend((user_mapping.get("valves"), user_mapping.get("user_valves")))
    candidates.extend(
        (
            getattr(pipe, "user_valves", None),
            getattr(pipe, "UserValves", None),
        )
    )
    merged: dict[str, _AgbAny] = {}
    for candidate in candidates:
        merged.update(_agb_mapping(candidate))
    return merged


def _agb_selected_model(pipe: _AgbAny, user: _AgbAny, body: _AgbAny) -> str:
    valves = _agb_user_valves(pipe, user)
    body_mapping = _agb_mapping(body)
    explicit = str(
        valves.get("llm_model")
        or body_mapping.get("llm_model")
        or body_mapping.get("autogenbook_llm_model")
        or _AGB_DEFAULT_MODEL
    ).strip()
    return explicit or _AGB_DEFAULT_MODEL


def _agb_inject_model(payload: _AgbAny, model: str) -> _AgbAny:
    if not isinstance(payload, dict):
        return payload
    payload["llm_model"] = model
    for key in ("spec", "job", "request", "options", "config", "metadata", "parameters", "payload"):
        child = payload.get(key)
        if isinstance(child, dict):
            child["llm_model"] = model
            child.setdefault("model", model)
    return payload


def _agb_find_job_id(value: _AgbAny, *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, _AgbMapping):
        for key in ("job_id", "run_id", "task_id"):
            text = str(value.get(key, "") or "").strip()
            if text:
                return text
        if "id" in value and any(key in value for key in ("status", "state", "job", "progress")):
            text = str(value.get("id", "") or "").strip()
            if text:
                return text
        for child in value.values():
            found = _agb_find_job_id(child, depth=depth + 1)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _agb_find_job_id(child, depth=depth + 1)
            if found:
                return found
    return ""


def _agb_origin(url: _AgbAny) -> str:
    try:
        parsed = _agb_urlsplit(str(url))
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _agb_companion_origins(pipe: _AgbAny) -> list[str]:
    rows: list[str] = []
    captured = _AGB_COMPANION_ORIGIN.get()
    if captured:
        rows.append(captured)
    for root in (pipe, getattr(pipe, "valves", None), getattr(pipe, "user_valves", None)):
        for key, value in _agb_mapping(root).items():
            folded = key.casefold()
            if any(token in folded for token in ("companion", "base_url", "api_url", "endpoint")):
                origin = _agb_origin(value)
                if origin:
                    rows.append(origin)
    unique: list[str] = []
    for row in rows:
        if row and row not in unique:
            unique.append(row)
    return unique


def _agb_request_headers(pipe: _AgbAny, request: _AgbAny = None) -> dict[str, str]:
    headers = {str(key): str(value) for key, value in _AGB_COMPANION_HEADERS.get().items()}
    for source in (getattr(request, "headers", None),):
        mapping = _agb_mapping(source)
        authorization = str(mapping.get("authorization", "") or "")
        if authorization and "Authorization" not in headers:
            headers["Authorization"] = authorization
    for root in (pipe, getattr(pipe, "valves", None), getattr(pipe, "user_valves", None)):
        for key, value in _agb_mapping(root).items():
            folded = key.casefold()
            if any(token in folded for token in ("companion_token", "api_token", "auth_token")):
                token = str(value or "").strip()
                if token:
                    headers["Authorization"] = token if token.casefold().startswith("bearer ") else f"Bearer {token}"
    return headers


async def _agb_emit(description: str, *, done: bool = False, error: bool = False, progress: float | None = None) -> None:
    emitter = _AGB_EMITTER.get()
    if not callable(emitter):
        return
    data: dict[str, _AgbAny] = {
        "description": description,
        "done": done,
        "status": "error" if error else ("complete" if done else "in_progress"),
    }
    if progress is not None:
        data["progress"] = max(0.0, min(100.0, float(progress)))
    try:
        result = emitter({"type": "status", "data": data})
        if _agb_inspect.isawaitable(result):
            await result
    except Exception:
        return


async def _agb_get_json(origin: str, path: str, headers: dict[str, str]) -> dict[str, _AgbAny]:
    import httpx as _agb_httpx

    async with _agb_httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(origin.rstrip("/") + path, headers=headers)
        response.raise_for_status()
        value = response.json()
        return dict(value) if isinstance(value, _AgbMapping) else {}


def _agb_terminal(status: str) -> bool:
    return status.casefold() in {
        "completed",
        "complete",
        "succeeded",
        "success",
        "failed",
        "error",
        "cancelled",
        "canceled",
        "interrupted",
    }


def _agb_status_markdown(job_id: str, model: str, progress: dict[str, _AgbAny], files: dict[str, _AgbAny] | None = None) -> str:
    percent = float(progress.get("progress", 0.0) or 0.0)
    state = str(progress.get("status") or progress.get("stage") or "unknown")
    message = str(progress.get("message") or "")
    lines = [
        "### AutoGenBook",
        f"- **Úloha:** `{job_id}`",
        f"- **LLM model:** `{model}`",
        f"- **Stav:** `{state}` — {percent:.1f} %",
    ]
    if message:
        lines.append(f"- **Aktuální krok:** {message}")
    heartbeat = str(progress.get("heartbeat_at") or "")
    if heartbeat:
        lines.append(f"- **Poslední heartbeat:** `{heartbeat}`")
    if files:
        rows = files.get("files", [])
        if isinstance(rows, list) and rows:
            lines.extend(("", "#### Vygenerované soubory"))
            for row in rows[:100]:
                if not isinstance(row, _AgbMapping):
                    continue
                name = str(row.get("path") or row.get("name") or "soubor")
                url = str(row.get("download_url") or "")
                size = int(row.get("size", 0) or 0)
                size_text = f"{size / (1024 ** 3):.2f} GiB" if size >= 1024 ** 3 else f"{size / (1024 ** 2):.1f} MiB"
                lines.append(f"- [{name}]({url}) — {size_text}" if url else f"- `{name}` — {size_text}")
            lines.append("\nStahování podporuje obnovení přenosu pomocí HTTP Range.")
    if not _agb_terminal(state):
        lines.extend(("", f"Stav později obnovíte příkazem `/autogenbook status {job_id}`."))
    return "\n".join(lines)


async def _agb_poll(pipe: _AgbAny, job_id: str, model: str, request: _AgbAny, wait_minutes: int, interval: int) -> tuple[dict[str, _AgbAny], dict[str, _AgbAny] | None]:
    headers = _agb_request_headers(pipe, request)
    origins = _agb_companion_origins(pipe)
    if not origins:
        return {}, None
    deadline = _agb_asyncio.get_running_loop().time() + max(0, min(30, wait_minutes)) * 60
    last: dict[str, _AgbAny] = {}
    while True:
        for origin in origins:
            try:
                last = await _agb_get_json(
                    origin,
                    f"/api/v1/autogenbook/jobs/{job_id}/progress",
                    headers,
                )
            except Exception:
                continue
            percent = float(last.get("progress", 0.0) or 0.0)
            message = str(last.get("message") or last.get("stage") or "Probíhá zpracování.")
            await _agb_emit(f"AutoGenBook {percent:.1f} % — {message}", progress=percent)
            state = str(last.get("status") or "")
            if _agb_terminal(state):
                files: dict[str, _AgbAny] | None = None
                try:
                    files = await _agb_get_json(
                        origin,
                        f"/api/v1/autogenbook/jobs/{job_id}/files",
                        headers,
                    )
                except Exception:
                    pass
                await _agb_emit(
                    "AutoGenBook dokončil úlohu." if state.casefold() in {"completed", "complete", "success", "succeeded"} else f"AutoGenBook skončil ve stavu {state}.",
                    done=True,
                    error=state.casefold() in {"failed", "error", "interrupted"},
                    progress=percent,
                )
                return last, files
            break
        if wait_minutes <= 0 or _agb_asyncio.get_running_loop().time() >= deadline:
            return last, None
        await _agb_asyncio.sleep(max(5, min(300, interval)))


def _agb_last_message(body: _AgbAny) -> str:
    mapping = _agb_mapping(body)
    messages = mapping.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if isinstance(item, _AgbMapping):
                content = item.get("content")
                if isinstance(content, str):
                    return content.strip()
    for key in ("prompt", "query", "message", "input"):
        value = mapping.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _agb_status_command(body: _AgbAny) -> str:
    text = _agb_last_message(body)
    match = _agb_re.fullmatch(r"\s*[!/]?autogenbook\s+(?:status|stav|files|soubory)\s+([A-Za-z0-9_.:-]+)\s*", text, _agb_re.IGNORECASE)
    return match.group(1) if match else ""


# Inject the selected model into the Companion create-job request and capture its ID.
try:
    import httpx as _agb_httpx

    if not getattr(_agb_httpx.AsyncClient.request, "_autogenbook_model_patch", False):
        _agb_original_async_request = _agb_httpx.AsyncClient.request

        async def _agb_async_request(self: _AgbAny, method: str, url: _AgbAny, *args: _AgbAny, **kwargs: _AgbAny) -> _AgbAny:
            method_upper = str(method).upper()
            url_text = str(url)
            path_lower = _agb_urlsplit(url_text).path.casefold()
            if method_upper in {"POST", "PUT", "PATCH"} and any(token in path_lower for token in ("job", "run", "submit", "project")) and "chat/completions" not in path_lower:
                if isinstance(kwargs.get("json"), dict):
                    kwargs["json"] = _agb_inject_model(kwargs["json"], _AGB_MODEL.get())
            response = await _agb_original_async_request(self, method, url, *args, **kwargs)
            origin = _agb_origin(url_text)
            if origin and any(token in path_lower for token in ("job", "run", "submit", "project")):
                _AGB_COMPANION_ORIGIN.set(origin)
                request_headers = kwargs.get("headers")
                if isinstance(request_headers, _AgbMapping):
                    _AGB_COMPANION_HEADERS.set({str(key): str(value) for key, value in request_headers.items()})
                try:
                    job_id = _agb_find_job_id(response.json())
                except Exception:
                    job_id = ""
                if job_id:
                    _AGB_JOB_ID.set(job_id)
            return response

        _agb_async_request._autogenbook_model_patch = True
        _agb_httpx.AsyncClient.request = _agb_async_request
except Exception:
    pass

try:
    import requests as _agb_requests

    if not getattr(_agb_requests.sessions.Session.request, "_autogenbook_model_patch", False):
        _agb_original_sync_request = _agb_requests.sessions.Session.request

        def _agb_sync_request(self: _AgbAny, method: str, url: _AgbAny, *args: _AgbAny, **kwargs: _AgbAny) -> _AgbAny:
            method_upper = str(method).upper()
            path_lower = _agb_urlsplit(str(url)).path.casefold()
            if method_upper in {"POST", "PUT", "PATCH"} and any(token in path_lower for token in ("job", "run", "submit", "project")) and "chat/completions" not in path_lower:
                if isinstance(kwargs.get("json"), dict):
                    kwargs["json"] = _agb_inject_model(kwargs["json"], _AGB_MODEL.get())
            response = _agb_original_sync_request(self, method, url, *args, **kwargs)
            origin = _agb_origin(url)
            if origin and any(token in path_lower for token in ("job", "run", "submit", "project")):
                _AGB_COMPANION_ORIGIN.set(origin)
                headers = kwargs.get("headers")
                if isinstance(headers, _AgbMapping):
                    _AGB_COMPANION_HEADERS.set({str(key): str(value) for key, value in headers.items()})
                try:
                    job_id = _agb_find_job_id(response.json())
                except Exception:
                    job_id = ""
                if job_id:
                    _AGB_JOB_ID.set(job_id)
            return response

        _agb_sync_request._autogenbook_model_patch = True
        _agb_requests.sessions.Session.request = _agb_sync_request
except Exception:
    pass


_AgbOriginalInit = getattr(Pipe, "__init__", lambda self: None)


def _agb_init(self: _AgbAny, *args: _AgbAny, **kwargs: _AgbAny) -> None:
    _AgbOriginalInit(self, *args, **kwargs)
    _agb_refresh_model_schema()
    current = getattr(self, "user_valves", None)
    if current is not None and not isinstance(current, _AgbUserValves):
        try:
            self.user_valves = _AgbUserValves(**_agb_mapping(current))
        except Exception:
            pass


Pipe.__init__ = _agb_init
_AgbOriginalPipe = Pipe.pipe


async def _agb_pipe(self: _AgbAny, *args: _AgbAny, **kwargs: _AgbAny) -> _AgbAny:
    try:
        bound = _agb_inspect.signature(_AgbOriginalPipe).bind_partial(self, *args, **kwargs)
        arguments = dict(bound.arguments)
    except Exception:
        arguments = dict(kwargs)
    body = arguments.get("body") or arguments.get("__body__") or kwargs.get("body")
    user = arguments.get("__user__") or arguments.get("user") or kwargs.get("__user__")
    emitter = arguments.get("__event_emitter__") or kwargs.get("__event_emitter__")
    request = arguments.get("__request__") or kwargs.get("__request__")
    _agb_refresh_model_schema(_agb_mapping(body).get("models") if body is not None else None)
    model = _agb_selected_model(self, user, body)
    valves = _agb_user_valves(self, user)
    interval = int(valves.get("progress_interval_seconds", 15) or 15)
    wait_minutes = int(valves.get("foreground_wait_minutes", 5) or 0)
    show_progress = bool(valves.get("show_progress", True))
    token_model = _AGB_MODEL.set(model)
    token_emitter = _AGB_EMITTER.set(emitter)
    token_job = _AGB_JOB_ID.set("")
    if isinstance(body, dict):
        _agb_inject_model(body, model)
    command_job = _agb_status_command(body)
    try:
        if command_job:
            progress, files = await _agb_poll(self, command_job, model, request, 0, interval)
            if progress:
                selected = str(progress.get("model") or model)
                return _agb_status_markdown(command_job, selected, progress, files)
        if show_progress:
            await _agb_emit(f"AutoGenBook odesílá úlohu s modelem {model}.", progress=0.0)
        result = _AgbOriginalPipe(self, *args, **kwargs)
        if _agb_inspect.isawaitable(result):
            result = await result
        # Async/sync generators are preserved; their own stream remains authoritative.
        if _agb_inspect.isasyncgen(result) or _agb_inspect.isgenerator(result):
            return result
        job_id = _AGB_JOB_ID.get()
        if not job_id and isinstance(result, str):
            match = _agb_re.search(r"(?:job|run|task)[ _-]?(?:id)?\s*[:#=]\s*`?([A-Za-z0-9_.:-]{6,})", result, _agb_re.IGNORECASE)
            if match:
                job_id = match.group(1)
        if not job_id:
            return result
        progress, files = await _agb_poll(
            self,
            job_id,
            model,
            request,
            wait_minutes if show_progress else 0,
            interval,
        )
        card = _agb_status_markdown(
            job_id,
            str(progress.get("model") or model) if progress else model,
            progress or {"progress": 0, "status": "queued", "message": "Úloha byla předána Companionu."},
            files,
        )
        if isinstance(result, str) and result.strip():
            return result.rstrip() + "\n\n" + card
        return card
    finally:
        _AGB_JOB_ID.reset(token_job)
        _AGB_EMITTER.reset(token_emitter)
        _AGB_MODEL.reset(token_model)


Pipe.pipe = _agb_pipe
