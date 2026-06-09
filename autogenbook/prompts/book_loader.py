from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

DEFAULT_BOOK_PROMPT_FILES: Dict[str, str] = {
    "global_system_policy": "global_system_policy.md",
    "global_evidence_instructions": "global_evidence_instructions.md",
    "json_only_system": "json_only_system.md",
    "json_repair_system": "json_repair_system.md",
    "mcp_tools_system": "mcp_tools_system.md",
    "book_json_from_txt_system": "book_json_from_txt_system.md",
    "book_json_from_txt_user": "book_json_from_txt_user.md",
    "book_redundancy_system": "book_redundancy_system.md",
    "book_redundancy_user": "book_redundancy_user.md",
    "structure_subdivider_system": "structure_subdivider_system.md",
    "structure_subdivider_user": "structure_subdivider_user.md",
    "book_section_writer_system": "book_section_writer_system.md",
    "book_section_writer_user": "book_section_writer_user.md",
    "book_section_reviewer_system": "book_section_reviewer_system.md",
    "book_section_reviewer_user": "book_section_reviewer_user.md",
    "book_section_revision_system": "book_section_revision_system.md",
    "book_section_revision_user": "book_section_revision_user.md",
    "context_memory_system": "context_memory_system.md",
    "context_memory_user": "context_memory_user.md",
    "length_control_system": "length_control_system.md",
    "length_control_user": "length_control_user.md",
}

MARKDOWN_BOOK_PROMPT_FILES: Dict[str, str] = {
    **DEFAULT_BOOK_PROMPT_FILES,
    "book_section_writer_system": "book_section_writer_system_md.md",
    "book_section_writer_user": "book_section_writer_user_md.md",
    "book_section_reviewer_system": "book_section_reviewer_system_md.md",
    "book_section_reviewer_user": "book_section_reviewer_user_md.md",
    "book_section_revision_system": "book_section_revision_system_md.md",
    "book_section_revision_user": "book_section_revision_user_md.md",
    "context_memory_user": "context_memory_user_md.md",
    "length_control_system": "length_control_system_md.md",
    "length_control_user": "length_control_user_md.md",
}


def _default_prompt_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / "book"


def load_book_prompts(
    prompt_dir: Optional[Path] = None,
    *,
    content_format: str = "latex",
) -> Dict[str, str]:
    base = Path(prompt_dir) if prompt_dir is not None else _default_prompt_dir()
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Book prompt directory not found: {base}")

    mapping = DEFAULT_BOOK_PROMPT_FILES
    if str(content_format).strip().lower() == "markdown":
        mapping = MARKDOWN_BOOK_PROMPT_FILES

    prompts: Dict[str, str] = {}
    for key, filename in mapping.items():
        path = base / filename
        if not path.exists():
            raise FileNotFoundError(f"Book prompt file not found: {path}")
        prompts[key] = path.read_text(encoding="utf-8")
    return prompts
