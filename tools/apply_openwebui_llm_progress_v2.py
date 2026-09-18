from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "tools" / "openwebui_overlays"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return
    if old not in source:
        raise RuntimeError(f"{label}: expected source fragment was not found in {path}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    # The model persisted with the job is authoritative.  An older call-site default
    # must never replace the value selected in the Open WebUI combo box.
    replace_once(
        OVERLAYS / "runtime_control.py",
        'kwargs["model"] = selected_llm_model(kwargs.get("model"))',
        'kwargs["model"] = selected_llm_model()',
        "force persisted model in OpenAI-compatible calls",
    )

    # Include class-level settings (common in Pydantic/dataclass configurations) in
    # the generic namespace walker used to locate SQLite and output directories.
    replace_once(
        OVERLAYS / "long_job_support.py",
        '''    with contextlib.suppress(Exception):
        return {str(key): _json_value(item) for key, item in vars(value).items() if not str(key).startswith("_")}
    return {}
''',
        '''    result: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        result.update(
            {str(key): _json_value(item) for key, item in vars(value).items() if not str(key).startswith("_")}
        )
    for name in (
        "data_dir",
        "jobs_dir",
        "work_dir",
        "output_dir",
        "database_path",
        "db_path",
        "api_token",
        "auth_token",
        "companion_token",
    ):
        with contextlib.suppress(Exception):
            item = getattr(value, name)
            if not callable(item):
                result.setdefault(name, _json_value(item))
    return result
''',
        "include class-level Companion settings",
    )

    spec = importlib.util.spec_from_file_location(
        "autogenbook_apply_openwebui_llm_progress_v1",
        ROOT / "tools" / "apply_openwebui_llm_progress.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the deterministic integration patcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
