from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "tools" / "openwebui_overlays"

_BACKGROUND_HELPER = r'''
def _agb_prefer_background(pipe: _AgbAny) -> None:
    """Disable legacy foreground waits while leaving the Companion job running."""
    for root in (getattr(pipe, "valves", None), getattr(pipe, "user_valves", None)):
        if root is None:
            continue
        mapping = _agb_mapping(root)
        for name, value in mapping.items():
            folded = name.casefold()
            replacement = None
            if folded in {
                "wait_for_completion",
                "block_until_complete",
                "synchronous",
                "synchronous_mode",
                "sync_mode",
            }:
                replacement = False
            elif "foreground" in folded and "wait" in folded:
                replacement = False
            elif "timeout" in folded:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    numeric = 0.0
                if numeric > 1800:
                    replacement = 1800
            if replacement is None:
                continue
            if isinstance(root, dict):
                root[name] = replacement
            else:
                try:
                    setattr(root, name, replacement)
                except Exception:
                    pass
'''.strip()


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    v3 = load(ROOT / "tools" / "apply_openwebui_llm_progress_v3.py", "autogenbook_llm_progress_v3")
    v3.harden_overlays()

    extension = OVERLAYS / "pipe_extension.py"
    source = extension.read_text(encoding="utf-8")
    insertion_marker = "_AgbOriginalInit = getattr(Pipe, \"__init__\", lambda self: None)"
    if _BACKGROUND_HELPER not in source:
        if insertion_marker not in source:
            raise RuntimeError("Pipe background-mode insertion point was not found")
        source = source.replace(insertion_marker, _BACKGROUND_HELPER + "\n\n\n" + insertion_marker, 1)
    call_marker = "        if show_progress:\n            await _agb_emit(f\"AutoGenBook odesílá úlohu s modelem {model}.\", progress=0.0)"
    replacement = "        _agb_prefer_background(self)\n" + call_marker
    if "        _agb_prefer_background(self)\n" not in source:
        if call_marker not in source:
            raise RuntimeError("Pipe execution insertion point was not found")
        source = source.replace(call_marker, replacement, 1)
    extension.write_text(source, encoding="utf-8")

    v1 = load(ROOT / "tools" / "apply_openwebui_llm_progress.py", "autogenbook_llm_progress_v1")
    v1.insert_after_future_imports = v3.syntax_safe_insert
    result = int(v1.main())
    generated_pipe = v1.find_pipe().read_text(encoding="utf-8")
    if "_agb_prefer_background(self)" not in generated_pipe:
        raise RuntimeError("Generated Pipe does not switch legacy waits to background mode")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
