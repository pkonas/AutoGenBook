from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "tools" / "openwebui_overlays"
DEFAULT_MODEL = "e-infra.glm-5"
PIPE_MARKER = "# AUTOGENBOOK_OPENWEBUI_LLM_PROGRESS_EXTENSION"
RUNNER_MARKER = "# AUTOGENBOOK_LLM_PROGRESS_RUNNER_PATCH"
APP_MARKER = "# AUTOGENBOOK_LONG_JOB_ROUTES_PATCH"
MAIN_MARKER = "# AUTOGENBOOK_RUNTIME_CONTROL_PATCH"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def current_bundle() -> tuple[bytes, list[tarfile.TarInfo]]:
    parts = sorted((ROOT / "integration").glob("openwebui_bundle.part*.b64"))
    if not parts:
        raise RuntimeError("Open WebUI bundle parts were not found")
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in parts)
    payload = base64.b64decode(encoded, validate=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
    return payload, members


def unpack_bundle() -> None:
    subprocess.run([sys.executable, str(ROOT / "tools" / "unpack_openwebui_bundle.py")], check=True)


def insert_after_future_imports(source: str, block: str) -> str:
    lines = source.splitlines(keepends=True)
    index = 0
    if lines and lines[0].startswith("#!"):
        index = 1
    if index < len(lines) and re.match(r"^#.*coding[:=]", lines[index]):
        index += 1
    if index < len(lines) and lines[index].startswith("from __future__ import"):
        index += 1
        while index < len(lines) and lines[index].startswith("from __future__ import"):
            index += 1
    lines.insert(index, "\n" + block.rstrip() + "\n")
    return "".join(lines)


def find_pipe() -> Path:
    preferred = ROOT / "integrations" / "openwebui" / "autogenbook_pipe.py"
    if preferred.exists():
        return preferred
    candidates: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", ".venv", "site-packages"} for part in path.parts):
            continue
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            text = stream.read()
        if "class Pipe" in text and "UserValves" in text and "openwebui" in path.as_posix().casefold():
            candidates.append(path)
    if not candidates:
        raise RuntimeError("Open WebUI Pipe source was not found")
    return sorted(candidates, key=lambda item: (len(item.parts), item.as_posix()))[0]


def patch_pipe(path: Path) -> None:
    source = path.read_text(encoding="utf-8-sig")
    if PIPE_MARKER in source:
        return
    extension = (OVERLAYS / "pipe_extension.py").read_text(encoding="utf-8")
    extension = extension.replace("from __future__ import annotations\n", "", 1)
    path.write_text(source.rstrip() + "\n\n" + extension.lstrip(), encoding="utf-8")


def runner_helpers() -> str:
    return r'''
# AUTOGENBOOK_LLM_PROGRESS_RUNNER_PATCH
_AGB_DEFAULT_LLM_MODEL = "e-infra.glm-5"


def _agb_mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    for name in ("model_dump", "dict", "to_dict"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                result = method()
            except Exception:
                continue
            if isinstance(result, dict):
                return {str(key): item for key, item in result.items()}
    try:
        return {str(key): item for key, item in vars(value).items() if not str(key).startswith("_")}
    except Exception:
        return {}


def _agb_model_from_value(value: object, depth: int = 0, seen: set[int] | None = None) -> str:
    if depth > 7:
        return ""
    if seen is None:
        seen = set()
    identifier = id(value)
    if identifier in seen:
        return ""
    seen.add(identifier)
    mapping = _agb_mapping(value)
    for key in ("llm_model", "autogenbook_llm_model", "openwebui_model"):
        text = str(mapping.get(key, "") or "").strip()
        if text:
            return text
    for key in ("spec", "job", "request", "options", "config", "metadata", "parameters", "payload"):
        if key in mapping:
            found = _agb_model_from_value(mapping[key], depth + 1, seen)
            if found:
                return found
    # Generic model is considered only after the AutoGenBook-specific keys.
    text = str(mapping.get("model", "") or "").strip()
    if text and not text.startswith("<"):
        return text
    if isinstance(value, (list, tuple, set)):
        for child in value:
            found = _agb_model_from_value(child, depth + 1, seen)
            if found:
                return found
    return ""


def _agb_model_from_locals(values: dict[str, object]) -> str:
    selected = _agb_model_from_value(values)
    if selected:
        return selected
    for name in (
        "AUTOGENBOOK_LLM_MODEL",
        "OPENWEBUI_MODEL",
        "OPENAI_MODEL",
        "OPENROUTER_MODEL",
        "LLM_MODEL",
    ):
        text = os.environ.get(name, "").strip()
        if text:
            return text
    return _AGB_DEFAULT_LLM_MODEL


def _agb_paths_from_value(value: object, depth: int = 0, seen: set[int] | None = None):
    if depth > 6:
        return
    if seen is None:
        seen = set()
    identifier = id(value)
    if identifier in seen:
        return
    seen.add(identifier)
    if isinstance(value, Path):
        yield value
    elif isinstance(value, str) and ("/" in value or "\\" in value):
        try:
            yield Path(value)
        except Exception:
            pass
    mapping = _agb_mapping(value)
    for key, child in mapping.items():
        if any(token in key.casefold() for token in ("dir", "path", "root", "workspace", "output", "work")):
            yield from _agb_paths_from_value(child, depth + 1, seen)
        elif isinstance(child, (dict, list, tuple, set)) or hasattr(child, "__dict__"):
            yield from _agb_paths_from_value(child, depth + 1, seen)
    if isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _agb_paths_from_value(child, depth + 1, seen)


def _agb_job_id_from_value(value: object) -> str:
    mapping = _agb_mapping(value)
    for key in ("job_id", "run_id", "task_id", "id"):
        text = str(mapping.get(key, "") or "").strip()
        if text:
            return text
    return ""


def _agb_job_dir_from_locals(values: dict[str, object], settings: object | None = None) -> Path:
    job_id = _agb_job_id_from_value(values)
    candidates: list[tuple[int, Path]] = []
    for path in _agb_paths_from_value({"values": values, "settings": settings}):
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            continue
        score = 0
        folded = resolved.as_posix().casefold()
        if job_id and job_id in resolved.parts:
            score += 100
        if any(token in folded for token in ("/jobs/", "/runs/", "/work/", "/output/")):
            score += 25
        if resolved.exists() and resolved.is_dir():
            score += 10
        if resolved.suffix:
            resolved = resolved.parent
        candidates.append((score, resolved))
    if candidates:
        candidates.sort(key=lambda item: (item[0], len(item[1].parts)), reverse=True)
        target = candidates[0][1]
    else:
        target = Path.cwd().resolve() / "output" / (job_id or "current-job")
    target.mkdir(parents=True, exist_ok=True)
    return target
'''.strip()


def patch_runner(path: Path) -> None:
    source = path.read_text(encoding="utf-8-sig")
    if RUNNER_MARKER in source:
        return
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)env = \{str\(k\): str\(v\) for k, v in os\.environ\.items\(\)\}[ \t]*$",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if match is None:
        raise RuntimeError(f"Runner environment construction was not found in {path}")
    indent = match.group("indent")
    block_lines = [
        "_agb_selected_model = _agb_model_from_locals(locals())",
        "for _agb_name in ('AUTOGENBOOK_LLM_MODEL', 'OPENWEBUI_MODEL', 'OPENAI_MODEL', 'OPENROUTER_MODEL', 'LLM_MODEL', 'MODEL_NAME'):",
        "    env[_agb_name] = _agb_selected_model",
        "_agb_job_dir = _agb_job_dir_from_locals(locals(), getattr(self, 'settings', None))",
        "env['AUTOGENBOOK_JOB_DIR'] = str(_agb_job_dir)",
        "env['AUTOGENBOOK_WORK_DIR'] = str(_agb_job_dir)",
        "env['AUTOGENBOOK_OUTPUT_DIR'] = str(_agb_job_dir)",
        "env['AUTOGENBOOK_PROGRESS_FILE'] = str(_agb_job_dir / '.autogenbook-progress.json')",
        "env['AUTOGENBOOK_ARTIFACT_MANIFEST'] = str(_agb_job_dir / '.autogenbook-artifacts.json')",
        "env['AUTOGENBOOK_HEARTBEAT_SECONDS'] = env.get('AUTOGENBOOK_HEARTBEAT_SECONDS', '30')",
        "env['PYTHONUNBUFFERED'] = '1'",
    ]
    indented = "\n".join(indent + line if line else line for line in block_lines)
    replacement = match.group(0) + "\n" + indented
    source = source[: match.start()] + replacement + source[match.end() :]
    source = source.rstrip() + "\n\n" + runner_helpers() + "\n"
    path.write_text(source, encoding="utf-8")


def patch_entry_point(path: Path) -> bool:
    source = path.read_text(encoding="utf-8-sig")
    if MAIN_MARKER in source:
        return True
    patterns = (
        "raise SystemExit(main())",
        "sys.exit(main())",
    )
    matched = next((pattern for pattern in patterns if pattern in source), None)
    if matched is None:
        return False
    import_block = (
        "# AUTOGENBOOK_RUNTIME_CONTROL_PATCH\n"
        "from autogenbook.runtime_control import run_main as _autogenbook_run_main\n"
    )
    source = insert_after_future_imports(source, import_block)
    replacement = "raise SystemExit(_autogenbook_run_main(main))"
    source = source.replace(matched, replacement, 1)
    path.write_text(source, encoding="utf-8")
    return True


def patch_openrouter(path: Path) -> None:
    source = path.read_text(encoding="utf-8-sig")
    marker = "# AUTOGENBOOK_SELECTED_MODEL_PATCH"
    if marker in source:
        return
    block = (
        marker
        + "\nfrom autogenbook.runtime_control import configure_model_environment as _autogenbook_configure_model\n"
        + "_autogenbook_selected_model = _autogenbook_configure_model()\n"
    )
    source = insert_after_future_imports(source, block)
    # Replace literal chat-model keywords and common model defaults.  The SDK-level
    # override remains the final guard for older code paths.
    source = re.sub(
        r"(?P<prefix>\bmodel\s*=\s*)(?P<quote>['\"])(?P<value>[^'\"\n]{2,200})(?P=quote)",
        lambda match: match.group("prefix") + "_autogenbook_selected_model"
        if any(token in match.group("value").casefold() for token in ("gpt", "claude", "gemini", "llama", "mistral", "glm", "/"))
        else match.group(0),
        source,
    )
    source = re.sub(
        r"(?P<prefix>\bmodel\s*:\s*str(?:\s*\|\s*None)?\s*=\s*)(?P<quote>['\"])(?P<value>[^'\"\n]{2,200})(?P=quote)",
        lambda match: match.group("prefix") + "_autogenbook_selected_model",
        source,
    )
    path.write_text(source, encoding="utf-8")


def base_model_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pydantic":
            for alias in node.names:
                if alias.name == "BaseModel":
                    names.add(alias.asname or alias.name)
    names.add("BaseModel")
    return names


def patch_request_models(package: Path) -> list[str]:
    patched: list[str] = []
    class_pattern = re.compile(r"(?:job|run|task|project).*(?:create|request|submit|start|spec|options)|(?:create|submit|start).*(?:job|run|task|project)", re.IGNORECASE)
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        model_bases = base_model_names(tree)
        insertions: list[tuple[int, str]] = []
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not class_pattern.search(node.name):
                continue
            bases = {
                base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
                for base in node.bases
            }
            if not bases.intersection(model_bases):
                continue
            fields = {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            }
            if "llm_model" in fields:
                continue
            if node.body:
                first = node.body[0]
                line = first.end_lineno if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str) else first.lineno - 1
            else:
                line = node.lineno
            indent = " " * (node.col_offset + 4)
            insertions.append((line, f'{indent}llm_model: str = "{DEFAULT_MODEL}"\n'))
        if not insertions:
            continue
        lines = source.splitlines(keepends=True)
        for line, text in sorted(insertions, reverse=True):
            lines.insert(line, text)
        path.write_text("".join(lines), encoding="utf-8")
        patched.append(path.relative_to(ROOT).as_posix())
    return patched


def app_patch_block(factory_names: list[tuple[str, bool]], has_app: bool) -> str:
    rows = [
        APP_MARKER,
        "from .long_job_support import install_long_job_support as _autogenbook_install_long_job_support",
        "",
    ]
    if has_app:
        rows.extend(
            (
                "try:",
                "    _autogenbook_install_long_job_support(app, globals())",
                "except Exception:",
                "    pass",
                "",
            )
        )
    for name, is_async in factory_names:
        original = f"_autogenbook_original_{name}"
        rows.append(f"{original} = {name}")
        if is_async:
            rows.extend(
                (
                    f"async def {name}(*args, **kwargs):",
                    f"    _autogenbook_app = await {original}(*args, **kwargs)",
                    "    return _autogenbook_install_long_job_support(_autogenbook_app, {**globals(), 'factory_args': args, 'factory_kwargs': kwargs})",
                )
            )
        else:
            rows.extend(
                (
                    f"def {name}(*args, **kwargs):",
                    f"    _autogenbook_app = {original}(*args, **kwargs)",
                    "    return _autogenbook_install_long_job_support(_autogenbook_app, {**globals(), 'factory_args': args, 'factory_kwargs': kwargs})",
                )
            )
        rows.append("")
    return "\n".join(rows).rstrip() + "\n"


def patch_companion_apps(package: Path) -> list[str]:
    patched: list[str] = []
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        if APP_MARKER in source or ("FastAPI" not in source and "create_app" not in source):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        has_app = False
        factories: list[tuple[str, bool]] = []
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "app" for target in targets):
                    value = node.value
                    if isinstance(value, ast.Call):
                        function_name = value.func.id if isinstance(value.func, ast.Name) else value.func.attr if isinstance(value.func, ast.Attribute) else ""
                        if function_name in {"FastAPI", "create_app", "build_app", "make_app", "get_app"}:
                            has_app = True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"create_app", "build_app", "make_app", "get_app"}:
                factories.append((node.name, isinstance(node, ast.AsyncFunctionDef)))
        if not has_app and not factories:
            continue
        source = source.rstrip() + "\n\n" + app_patch_block(factories, has_app)
        path.write_text(source, encoding="utf-8")
        patched.append(path.relative_to(ROOT).as_posix())
    if not patched:
        raise RuntimeError("No Companion FastAPI application entry point was patched")
    return patched


def write_tests(pipe_path: Path, runner_path: Path, main_path: Path) -> Path:
    tests = ROOT / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    path = tests / "test_openwebui_llm_progress.py"
    content = f'''from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "companion" / "src"))


def test_default_and_explicit_model(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AUTOGENBOOK_LLM_MODEL", "OPENWEBUI_MODEL", "OPENAI_MODEL", "OPENROUTER_MODEL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    module = importlib.import_module("autogenbook.runtime_control")
    assert module.selected_llm_model() == "{DEFAULT_MODEL}"
    assert module.configure_model_environment("custom.model") == "custom.model"
    assert os.environ["AUTOGENBOOK_LLM_MODEL"] == "custom.model"
    assert os.environ["OPENROUTER_MODEL"] == "custom.model"


def test_progress_is_persistent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = importlib.import_module("autogenbook.runtime_control")
    monkeypatch.setenv("AUTOGENBOOK_JOB_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOGENBOOK_PROGRESS_FILE", str(tmp_path / "progress.json"))
    module.emit_progress(37.5, "generation", "Kapitola 3/8", current=3, total=8)
    value = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert value["progress"] == 37.5
    assert value["current"] == 3
    assert value["total"] == 8


def test_large_file_range_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOGENBOOK_COMPANION_TOKEN", "test-secret")
    job_id = "job-large-001"
    job_root = tmp_path / job_id
    job_root.mkdir(parents=True)
    (job_root / ".autogenbook-progress.json").write_text(
        json.dumps({{"job_id": job_id, "progress": 61, "status": "running", "message": "Rendering", "model": "{DEFAULT_MODEL}"}}),
        encoding="utf-8",
    )
    large = job_root / "large-output.bin"
    with large.open("wb") as stream:
        stream.seek(32 * 1024 * 1024 - 1)
        stream.write(b"X")
    with large.open("r+b") as stream:
        stream.seek(1024)
        stream.write(bytes(range(100)))

    module = importlib.import_module("autogenbook_companion.long_job_support")

    class Settings:
        data_dir = tmp_path

    app = FastAPI()
    module.install_long_job_support(app, {{"settings": Settings()}})
    client = TestClient(app)
    headers = {{"Authorization": "Bearer test-secret"}}
    progress = client.get(f"/api/v1/autogenbook/jobs/{{job_id}}/progress", headers=headers)
    assert progress.status_code == 200
    assert progress.json()["model"] == "{DEFAULT_MODEL}"
    files = client.get(f"/api/v1/autogenbook/jobs/{{job_id}}/files", headers=headers)
    assert files.status_code == 200
    row = next(item for item in files.json()["files"] if item["path"] == "large-output.bin")
    assert row["size"] == 32 * 1024 * 1024
    assert row["supports_resume"] is True
    response = client.get(
        f"/api/v1/autogenbook/jobs/{{job_id}}/files/large-output.bin",
        headers={{**headers, "Range": "bytes=1024-1123"}},
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 1024-1123/{{32 * 1024 * 1024}}"
    assert response.content == bytes(range(100))


def test_pipe_and_runner_are_wired() -> None:
    pipe = (ROOT / "{pipe_path.relative_to(ROOT).as_posix()}").read_text(encoding="utf-8")
    assert "{PIPE_MARKER}" in pipe
    assert "llm_model" in pipe
    assert '"type": "select"' in pipe
    assert "{DEFAULT_MODEL}" in pipe
    runner = (ROOT / "{runner_path.relative_to(ROOT).as_posix()}").read_text(encoding="utf-8")
    assert "AUTOGENBOOK_PROGRESS_FILE" in runner
    assert "AUTOGENBOOK_ARTIFACT_MANIFEST" in runner
    assert "AUTOGENBOOK_LLM_MODEL" in runner
    main = (ROOT / "{main_path.relative_to(ROOT).as_posix()}").read_text(encoding="utf-8")
    assert "_autogenbook_run_main(main)" in main
'''
    path.write_text(content, encoding="utf-8")
    return path


def compile_sources(paths: list[Path]) -> None:
    for path in paths:
        source = path.read_text(encoding="utf-8-sig")
        compile(source, str(path), "exec")
    subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "autogenbook", "companion/src", "integrations/openwebui"],
        cwd=ROOT,
        check=True,
    )


def normalized_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def deterministic_bundle(original_members: list[tarfile.TarInfo], additions: list[Path]) -> bytes:
    names: set[str] = {
        normalized_member_name(member.name)
        for member in original_members
        if member.isfile() and normalized_member_name(member.name)
    }
    for path in additions:
        names.add(path.relative_to(ROOT).as_posix())
    missing = [name for name in sorted(names) if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError(f"Bundle members disappeared after patch: {missing[:20]}")
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name in sorted(names):
                path = ROOT / name
                payload = path.read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(payload))
    return raw.getvalue()


def write_bundle(payload: bytes) -> list[Path]:
    integration = ROOT / "integration"
    for path in integration.glob("openwebui_bundle.part*.b64"):
        path.unlink()
    encoded = base64.b64encode(payload).decode("ascii")
    chunk_size = 9000
    paths: list[Path] = []
    for index, start in enumerate(range(0, len(encoded), chunk_size), start=1):
        path = integration / f"openwebui_bundle.part{index:03d}.b64"
        path.write_text(encoded[start : start + chunk_size] + "\n", encoding="ascii")
        paths.append(path)
    unpacker = ROOT / "tools" / "unpack_openwebui_bundle.py"
    source = unpacker.read_text(encoding="utf-8-sig")
    digest = sha256_bytes(payload)
    source, count = re.subn(
        r'EXPECTED_SHA256\s*=\s*"[0-9a-fA-F]{64}"',
        f'EXPECTED_SHA256 = "{digest}"',
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError("The integration bundle hash constant was not found")
    unpacker.write_text(source, encoding="utf-8")
    return paths


def main() -> int:
    old_payload, old_members = current_bundle()
    unpack_bundle()

    runtime_target = ROOT / "autogenbook" / "runtime_control.py"
    runtime_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OVERLAYS / "runtime_control.py", runtime_target)

    companion_package = ROOT / "companion" / "src" / "autogenbook_companion"
    if not companion_package.is_dir():
        raise RuntimeError("Companion package was not found after unpacking")
    long_job_target = companion_package / "long_job_support.py"
    shutil.copy2(OVERLAYS / "long_job_support.py", long_job_target)

    pipe_path = find_pipe()
    patch_pipe(pipe_path)

    runner_path = companion_package / "runner.py"
    if not runner_path.exists():
        raise RuntimeError("Companion runner.py was not found")
    patch_runner(runner_path)

    entry_candidates = [ROOT / "main.py", ROOT / "autogenbook" / "__main__.py"]
    main_path = next((path for path in entry_candidates if path.exists() and patch_entry_point(path)), None)
    if main_path is None:
        for path in ROOT.rglob("__main__.py"):
            if patch_entry_point(path):
                main_path = path
                break
    if main_path is None:
        raise RuntimeError("AutoGenBook CLI entry point was not patched")

    openrouter = ROOT / "openrouter_llm.py"
    if openrouter.exists():
        patch_openrouter(openrouter)

    request_models = patch_request_models(companion_package)
    app_modules = patch_companion_apps(companion_package)
    test_path = write_tests(pipe_path, runner_path, main_path)

    compile_sources(
        [runtime_target, long_job_target, pipe_path, runner_path, main_path, test_path]
        + [ROOT / path for path in app_modules]
    )

    additions = [runtime_target, long_job_target, test_path]
    payload = deterministic_bundle(old_members, additions)
    parts = write_bundle(payload)

    # Verify that the newly written bundle can be decoded and compiled independently.
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in parts)
    decoded = base64.b64decode(encoded, validate=True)
    if decoded != payload:
        raise RuntimeError("Reconstructed bundle differs from the generated payload")

    report = {
        "default_model": DEFAULT_MODEL,
        "old_bundle_sha256": sha256_bytes(old_payload),
        "new_bundle_sha256": sha256_bytes(payload),
        "bundle_size": len(payload),
        "bundle_parts": len(parts),
        "pipe": pipe_path.relative_to(ROOT).as_posix(),
        "runner": runner_path.relative_to(ROOT).as_posix(),
        "entry_point": main_path.relative_to(ROOT).as_posix(),
        "request_models": request_models,
        "app_modules": app_modules,
        "tests": test_path.relative_to(ROOT).as_posix(),
        "features": [
            "Open WebUI model selector",
            "persisted model per job",
            "disk-backed progress and heartbeat",
            "restart-safe status endpoint",
            "large-file streaming",
            "HTTP Range resume support",
            "time-limited signed download URLs",
        ],
    }
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "openwebui-llm-progress-patch-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
