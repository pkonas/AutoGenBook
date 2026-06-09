from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

DEFAULT_PAPER_PROMPT_FILES: Dict[str, str] = {
    "global_system_policy": "global_system_policy.md",
    "global_evidence_instructions": "global_evidence_instructions.md",
    "json_only_system": "json_only_system.md",
    "json_repair_system": "json_repair_system.md",
    "mcp_tools_system": "mcp_tools_system.md",
    "paper_json_from_txt_system": "paper_json_from_txt_system.md",
    "paper_json_from_txt_user": "paper_json_from_txt_user.md",
    "structure_subdivider_system": "structure_subdivider_system.md",
    "structure_subdivider_user": "structure_subdivider_user.md",
    "paper_section_writer_system": "paper_section_writer_system.md",
    "paper_section_writer_user": "paper_section_writer_user.md",
    "web_literature_system": "web_literature_system.md",
    "web_literature_user": "web_literature_user.md",
    "length_control_system": "length_control_system.md",
    "length_control_user": "length_control_user.md",
}


def _default_prompt_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / "paper"


def load_paper_prompts(
    prompt_dir: Optional[Path] = None,
    *,
    content_format: str = "latex",
) -> Dict[str, str]:
    base = Path(prompt_dir) if prompt_dir is not None else _default_prompt_dir()
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Paper prompt directory not found: {base}")

    content_format = (content_format or "latex").strip().lower()
    prompt_files = dict(DEFAULT_PAPER_PROMPT_FILES)
    if content_format == "markdown":
        prompt_files["paper_section_writer_system"] = "paper_section_writer_system_md.md"
        prompt_files["paper_section_writer_user"] = "paper_section_writer_user_md.md"
        prompt_files["length_control_system"] = "length_control_system_md.md"
        prompt_files["length_control_user"] = "length_control_user_md.md"

    prompts: Dict[str, str] = {}
    for key, filename in prompt_files.items():
        path = base / filename
        if not path.exists():
            raise FileNotFoundError(f"Paper prompt file not found: {path}")
        prompts[key] = path.read_text(encoding="utf-8")
    return prompts
