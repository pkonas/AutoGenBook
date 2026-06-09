from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

DEFAULT_PRESENTATION_PROMPT_FILES: Dict[str, str] = {
    "global_system_policy": "global_system_policy.md",
    "global_evidence_instructions": "global_evidence_instructions.md",
    "json_only_system": "json_only_system.md",
    "json_repair_system": "json_repair_system.md",
    "mcp_tools_system": "mcp_tools_system.md",
    "presentation_json_from_txt_system": "presentation_json_from_txt_system.md",
    "presentation_json_from_txt_user": "presentation_json_from_txt_user.md",
    "structure_subdivider_system": "structure_subdivider_system.md",
    "structure_subdivider_user": "structure_subdivider_user.md",
    "presentation_slide_writer_system": "presentation_slide_writer_system.md",
    "presentation_slide_writer_user": "presentation_slide_writer_user.md",
    "presentation_narration_system": "presentation_narration_system.md",
    "presentation_narration_user": "presentation_narration_user.md",
    "presentation_image_prompt_system": "presentation_image_prompt_system.md",
    "presentation_image_prompt_user": "presentation_image_prompt_user.md",
    "length_control_system": "length_control_system.md",
    "length_control_user": "length_control_user.md",
}


def _default_prompt_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / "presentation"


def load_presentation_prompts(prompt_dir: Optional[Path] = None) -> Dict[str, str]:
    base = Path(prompt_dir) if prompt_dir is not None else _default_prompt_dir()
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Presentation prompt directory not found: {base}")

    prompts: Dict[str, str] = {}
    for key, filename in DEFAULT_PRESENTATION_PROMPT_FILES.items():
        path = base / filename
        if not path.exists():
            raise FileNotFoundError(f"Presentation prompt file not found: {path}")
        prompts[key] = path.read_text(encoding="utf-8")
    return prompts
