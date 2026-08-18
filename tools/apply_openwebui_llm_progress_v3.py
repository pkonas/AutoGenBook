from __future__ import annotations

import ast
import contextlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "tools" / "openwebui_overlays"


def harden_overlays() -> None:
    runtime = OVERLAYS / "runtime_control.py"
    source = runtime.read_text(encoding="utf-8")
    old = 'kwargs["model"] = selected_llm_model(kwargs.get("model"))'
    if old in source:
        source = source.replace(old, 'kwargs["model"] = selected_llm_model()')
        runtime.write_text(source, encoding="utf-8")
    if old in runtime.read_text(encoding="utf-8"):
        raise RuntimeError("A chat-completion path can still override the persisted model")

    long_jobs = OVERLAYS / "long_job_support.py"
    source = long_jobs.read_text(encoding="utf-8")
    old_mapping = '''    with contextlib.suppress(Exception):
        return {str(key): _json_value(item) for key, item in vars(value).items() if not str(key).startswith("_")}
    return {}
'''
    new_mapping = '''    result: dict[str, Any] = {}
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
'''
    if old_mapping in source:
        source = source.replace(old_mapping, new_mapping, 1)
        long_jobs.write_text(source, encoding="utf-8")
    if new_mapping not in long_jobs.read_text(encoding="utf-8"):
        raise RuntimeError("Class-level Companion settings are not included in path discovery")


def syntax_safe_insert(source: str, block: str) -> str:
    """Insert imports after a module docstring and every future import."""
    tree = ast.parse(source)
    insertion_line = 0
    for index, node in enumerate(tree.body):
        is_docstring = (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if is_docstring or is_future:
            insertion_line = max(insertion_line, int(node.end_lineno or node.lineno))
            continue
        break
    lines = source.splitlines(keepends=True)
    # Preserve a shebang/encoding declaration even in files without a module AST node.
    while insertion_line < len(lines) and insertion_line < 2 and (
        lines[insertion_line].startswith("#!") or "coding" in lines[insertion_line]
    ):
        insertion_line += 1
    lines.insert(insertion_line, "\n" + block.rstrip() + "\n")
    result = "".join(lines)
    compile(result, "<syntax-safe-patch>", "exec")
    return result


def main() -> int:
    harden_overlays()
    spec = importlib.util.spec_from_file_location(
        "autogenbook_apply_openwebui_llm_progress_v1",
        ROOT / "tools" / "apply_openwebui_llm_progress.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the deterministic integration patcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.insert_after_future_imports = syntax_safe_insert
    result = int(module.main())
    runtime_target = ROOT / "autogenbook" / "runtime_control.py"
    generated = runtime_target.read_text(encoding="utf-8")
    if 'selected_llm_model(kwargs.get("model"))' in generated:
        raise RuntimeError("Generated runtime still permits a call-site model to replace the selected model")
    compile(generated, str(runtime_target), "exec")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
