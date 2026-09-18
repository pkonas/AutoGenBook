from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "tools" / "openwebui_overlays"
DEFAULT_MODEL = "e-infra.glm-5"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_background_extension(v4: Any) -> None:
    extension = OVERLAYS / "pipe_extension.py"
    source = extension.read_text(encoding="utf-8")
    insertion_marker = "_AgbOriginalInit = getattr(Pipe, \"__init__\", lambda self: None)"
    if v4._BACKGROUND_HELPER not in source:
        if insertion_marker not in source:
            raise RuntimeError("Pipe background-mode insertion point was not found")
        source = source.replace(insertion_marker, v4._BACKGROUND_HELPER + "\n\n\n" + insertion_marker, 1)
    call_marker = "        if show_progress:\n            await _agb_emit(f\"AutoGenBook odesílá úlohu s modelem {model}.\", progress=0.0)"
    if "        _agb_prefer_background(self)\n" not in source:
        if call_marker not in source:
            raise RuntimeError("Pipe execution insertion point was not found")
        source = source.replace(call_marker, "        _agb_prefer_background(self)\n" + call_marker, 1)
    extension.write_text(source, encoding="utf-8")


def annotation_names(annotation: ast.AST | None) -> set[str]:
    if annotation is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def route_request_models(package: Path) -> set[str]:
    candidates: set[str] = set()
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_job_route = False
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr.casefold() not in {"post", "put", "patch"}:
                    continue
                path_value = decorator.args[0].value if decorator.args and isinstance(decorator.args[0], ast.Constant) else ""
                if any(token in str(path_value).casefold() for token in ("job", "run", "task", "project", "generate")):
                    is_job_route = True
                    break
            if not is_job_route:
                continue
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            for argument in arguments:
                if argument.arg in {"request", "response", "user", "background_tasks"}:
                    continue
                candidates.update(annotation_names(argument.annotation))
    return candidates


def class_fields(node: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            fields.add(child.target.id)
        elif isinstance(child, ast.Assign):
            fields.update(target.id for target in child.targets if isinstance(target, ast.Name))
    return fields


def strict_request_model_patch(v1: Any, package: Path) -> list[str]:
    patched = list(v1._original_patch_request_models(package))
    route_models = route_request_models(package)
    candidate_rows: list[tuple[Path, ast.ClassDef]] = []
    broad_tokens = {"prompt", "topic", "request", "spec", "project", "config", "output", "source", "command"}
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        base_names = v1.base_model_names(tree)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {
                base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
                for base in node.bases
            }
            if not bases.intersection(base_names):
                continue
            fields = class_fields(node)
            is_route_model = node.name in route_models
            is_job_shape = bool(fields.intersection(broad_tokens)) and any(
                token in node.name.casefold() for token in ("job", "run", "task", "project", "request", "create", "submit", "generate")
            )
            if (is_route_model or is_job_shape) and "llm_model" not in fields:
                candidate_rows.append((path, node))

    by_path: dict[Path, list[ast.ClassDef]] = {}
    for path, node in candidate_rows:
        by_path.setdefault(path, []).append(node)
    for path, nodes in by_path.items():
        source = path.read_text(encoding="utf-8-sig")
        lines = source.splitlines(keepends=True)
        for node in sorted(nodes, key=lambda item: item.lineno, reverse=True):
            first = node.body[0] if node.body else None
            if first is not None and isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                index = int(first.end_lineno or first.lineno)
            elif first is not None:
                index = first.lineno - 1
            else:
                index = node.lineno
            lines.insert(index, " " * (node.col_offset + 4) + f'llm_model: str = "{DEFAULT_MODEL}"\n')
        path.write_text("".join(lines), encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        if relative not in patched:
            patched.append(relative)

    # A dict-based request body needs no schema field.  Otherwise at least one job
    # request model must explicitly accept llm_model to avoid a FastAPI 422 response.
    package_source = "\n".join(path.read_text(encoding="utf-8-sig") for path in package.glob("*.py"))
    dict_body = bool(re.search(r"(?:payload|body|spec|job)\s*:\s*(?:dict|Dict|Mapping)\b", package_source))
    if not patched and not dict_body:
        raise RuntimeError(
            "No Companion job request model accepted llm_model and no dict-based job body was found"
        )
    return patched


_CONTRACT_TEST = r'''


def test_openwebui_combo_contract_and_job_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import sys
    import types
    from types import SimpleNamespace
    from pydantic import BaseModel
    import httpx
    import requests

    open_webui = types.ModuleType("open_webui")
    utils = types.ModuleType("open_webui.utils")
    models = types.ModuleType("open_webui.utils.models")
    models.get_all_models = lambda: [
        {"id": "e-infra.glm-5"},
        {"id": "local.llama-validated"},
    ]
    monkeypatch.setitem(sys.modules, "open_webui", open_webui)
    monkeypatch.setitem(sys.modules, "open_webui.utils", utils)
    monkeypatch.setitem(sys.modules, "open_webui.utils.models", models)

    original_async_request = httpx.AsyncClient.request
    original_sync_request = requests.sessions.Session.request

    class SyntheticPipe:
        class UserValves(BaseModel):
            pass

        def __init__(self) -> None:
            self.valves = SimpleNamespace(wait_for_completion=True)
            self.user_valves = self.UserValves()

        async def pipe(self, body=None, __user__=None, __event_emitter__=None, __request__=None):
            return "synthetic"

    namespace = {"Pipe": SyntheticPipe, "__name__": "synthetic_autogenbook_pipe"}
    extension = (ROOT / "tools" / "openwebui_overlays" / "pipe_extension.py").read_text(encoding="utf-8")
    try:
        exec(compile(extension, "synthetic_autogenbook_pipe.py", "exec"), namespace)
        patched = namespace["Pipe"]
        schema = patched.UserValves.model_json_schema()["properties"]["llm_model"]
        assert schema["default"] == "e-infra.glm-5"
        assert "local.llama-validated" in schema["enum"]
        assert schema["type"] == "select"
        assert any(option["value"] == "local.llama-validated" for option in schema["options"])

        payload = {"spec": {}, "metadata": {}}
        namespace["_agb_inject_model"](payload, "local.llama-validated")
        assert payload["llm_model"] == "local.llama-validated"
        assert payload["spec"]["llm_model"] == "local.llama-validated"
        assert payload["metadata"]["llm_model"] == "local.llama-validated"

        instance = patched()
        namespace["_agb_prefer_background"](instance)
        assert instance.valves.wait_for_completion is False
    finally:
        httpx.AsyncClient.request = original_async_request
        requests.sessions.Session.request = original_sync_request
'''


def main() -> int:
    v3 = load(ROOT / "tools" / "apply_openwebui_llm_progress_v3.py", "autogenbook_llm_progress_v3")
    v3.harden_overlays()
    v4 = load(ROOT / "tools" / "apply_openwebui_llm_progress_v4.py", "autogenbook_llm_progress_v4")
    apply_background_extension(v4)

    v1 = load(ROOT / "tools" / "apply_openwebui_llm_progress.py", "autogenbook_llm_progress_v1")
    v1.insert_after_future_imports = v3.syntax_safe_insert
    v1._original_patch_request_models = v1.patch_request_models
    v1.patch_request_models = lambda package: strict_request_model_patch(v1, package)

    original_write_tests = v1.write_tests

    def write_tests_with_contract(pipe_path: Path, runner_path: Path, main_path: Path) -> Path:
        path = original_write_tests(pipe_path, runner_path, main_path)
        source = path.read_text(encoding="utf-8")
        if "test_openwebui_combo_contract_and_job_payload" not in source:
            path.write_text(source.rstrip() + _CONTRACT_TEST + "\n", encoding="utf-8")
        return path

    v1.write_tests = write_tests_with_contract
    result = int(v1.main())
    test_source = (ROOT / "tests" / "test_openwebui_llm_progress.py").read_text(encoding="utf-8")
    if "test_openwebui_combo_contract_and_job_payload" not in test_source:
        raise RuntimeError("The generated bundle is missing the Open WebUI combo-box contract test")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
