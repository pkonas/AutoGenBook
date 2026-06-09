from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict


@dataclass
class EvidenceWindowConfig:
    window_chars: int = 600
    allow_same_paragraph: bool = True
    allowed_evidence_markers: Dict[str, str] = field(
        default_factory=lambda: {
            "cite": r"\\cite[a-zA-Z]*\s*(?:\[[^\]]*\]\s*)?\{[^}]+\}",
            "footnote_source": r"\\footnote\{[^}]*Source:[^}]*\}",
            "inline_source": r"Source:\s*RID:[^\s\}]+",
        }
    )


def numeric_claim_has_evidence(tex: str, span: Dict[str, object], config: EvidenceWindowConfig) -> bool:
    start = int(span.get("start", 0))
    end = int(span.get("end", 0))
    if start < 0 or end <= start:
        return False

    if _is_inside_block(tex, start, ("table", "table*", "tabular", "tabular*")):
        return True
    if _is_inside_block(tex, start, ("figure", "figure*")) and _is_inside_caption(tex, start):
        return True

    if _is_inside_references(tex, start):
        return True

    window_start = max(0, start - config.window_chars)
    window_end = min(len(tex), end + config.window_chars)
    window_text = tex[window_start:window_end]

    if config.allow_same_paragraph:
        para = _paragraph_for_position(tex, start)
        if para:
            window_text = para

    for pattern in config.allowed_evidence_markers.values():
        if re.search(pattern, window_text, re.DOTALL):
            return True
    return False


def _paragraph_for_position(tex: str, pos: int) -> str:
    if pos < 0 or pos > len(tex):
        return ""
    start = tex.rfind("\n\n", 0, pos)
    end = tex.find("\n\n", pos)
    if start == -1:
        start = 0
    else:
        start += 2
    if end == -1:
        end = len(tex)
    return tex[start:end]


def _is_inside_block(tex: str, pos: int, envs: tuple[str, ...]) -> bool:
    for env in envs:
        begin = tex.rfind(f"\\begin{{{env}}}", 0, pos)
        if begin == -1:
            continue
        end = tex.find(f"\\end{{{env}}}", begin)
        if end != -1 and pos < end:
            return True
    return False


def _is_inside_caption(tex: str, pos: int) -> bool:
    cap = tex.rfind("\\caption", 0, pos)
    if cap == -1:
        return False
    next_line = tex.find("\n", cap)
    if next_line == -1:
        next_line = len(tex)
    return cap <= pos <= next_line


def _is_inside_references(tex: str, pos: int) -> bool:
    for marker in ("\\bibliography", "\\begin{thebibliography}"):
        idx = tex.rfind(marker, 0, pos)
        if idx != -1:
            return True
    return False
