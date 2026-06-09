from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from autogenbook.retrieval.kb_citations import format_loc_label


def format_kb_footnote(source: str, loc: str) -> str:
    filename = Path(source).name if source else "unknown"
    loc_label = format_loc_label(loc)
    if loc_label:
        return f"{filename} ({loc_label})"
    return filename


def apply_kb_footnotes(
    tex: str,
    cite_key_map: Dict[str, Tuple[str, str]],
    rid_map: Dict[str, Tuple[str, str]],
) -> str:
    def _format_token(token: str) -> Optional[str]:
        if token in cite_key_map:
            source_path, loc = cite_key_map[token]
            return format_kb_footnote(source_path, loc)
        if token in rid_map:
            source_path, loc = rid_map[token]
            return format_kb_footnote(source_path, loc)
        return None

    def _replace_cite(match: re.Match[str]) -> str:
        keys = match.group("keys")
        if not keys:
            return match.group(0)
        kb_entries = []
        non_kb_keys = []
        for raw_key in keys.split(","):
            key = raw_key.strip()
            if not key:
                continue
            formatted = _format_token(key)
            if formatted:
                kb_entries.append(formatted)
            else:
                non_kb_keys.append(key)
        if not kb_entries:
            return match.group(0)
        footnote = f"\\footnote{{{' '.join(kb_entries)}}}"
        if non_kb_keys:
            cmd = match.group("cmd")
            opts = match.group("opts") or ""
            cite = f"{cmd}{opts}{{{', '.join(non_kb_keys)}}}"
            return f"{cite}{footnote}"
        return footnote

    def _replace_source_footnote(match: re.Match[str]) -> str:
        body = match.group(1)
        token_match = re.search(r"Source:\s*([^\s}]+)", body)
        if not token_match:
            return match.group(0)
        token = token_match.group(1).strip()
        if not token:
            return match.group(0)
        formatted = _format_token(token)
        if not formatted:
            return match.group(0)
        return f"\\footnote{{{formatted}}}"

    cite_pattern = re.compile(
        r"(?P<cmd>\\cite[a-zA-Z*]*)"
        r"(?P<opts>(?:\[[^\]]*\]\s*)*)"
        r"\{(?P<keys>[^}]+)\}",
    )
    tex = cite_pattern.sub(_replace_cite, tex)
    tex = re.sub(r"\\footnote\{(.*?)\}", _replace_source_footnote, tex, flags=re.S)
    return tex
