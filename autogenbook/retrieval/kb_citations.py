from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Optional

_PAGE_RE = re.compile(r"(?:page|p\.?)[\s:]*([0-9]+)", re.IGNORECASE)
_SLIDE_RE = re.compile(r"(?:slide|s\.?)[\s:]*([0-9]+)", re.IGNORECASE)
_CHUNK_RE = re.compile(r"(?:chunk)[\s:]*([0-9]+)", re.IGNORECASE)


def extract_page(loc: str) -> Optional[str]:
    if not loc:
        return None
    match = _PAGE_RE.search(loc)
    if match:
        return match.group(1)
    return None


def extract_slide(loc: str) -> Optional[str]:
    if not loc:
        return None
    match = _SLIDE_RE.search(loc)
    if match:
        return match.group(1)
    return None


def extract_chunk(loc: str) -> Optional[str]:
    if not loc:
        return None
    match = _CHUNK_RE.search(loc)
    if match:
        return match.group(1)
    return None


def _sanitize_key(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return cleaned or "ref"


def _source_id_from_path(source_path: str, max_len: int = 48) -> str:
    stem = Path(source_path).stem or "source"
    base = _sanitize_key(stem)
    if max_len > 0 and len(base) > max_len:
        base = base[:max_len].rstrip("_")
    digest = hashlib.sha256(source_path.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{base}_{digest}" if base else f"source_{digest}"


def format_loc_label(loc: str) -> str:
    return re.sub(r"\s+", " ", (loc or "").strip())


def kb_cite_key(source_path: str, loc: str) -> str:
    source_id = _source_id_from_path(source_path)
    loc_label = format_loc_label(loc)
    if loc_label:
        return _sanitize_key(f"{source_id}_{loc_label}")
    return _sanitize_key(source_id)


def kb_source_label(source_path: str, loc: str) -> str:
    name = Path(source_path).name
    loc_label = format_loc_label(loc)
    if loc_label:
        return f"{name} ({loc_label})"
    return name


def build_kb_index(kb: Any) -> Dict[str, Dict[str, Dict[str, str]]]:
    def _excerpt_from_text(raw: str, max_words: int = 12, max_chars: int = 120) -> str:
        cleaned = re.sub(r"\s+", " ", (raw or "").strip())
        if not cleaned:
            return ""
        words = cleaned.split()
        excerpt = " ".join(words[:max_words])
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars].rstrip()
        return excerpt

    cite_keys: Dict[str, Dict[str, str]] = {}
    rids: Dict[str, Dict[str, str]] = {}
    page_keys: Dict[str, list[Dict[str, str]]] = {}
    chunks: list[Dict[str, str]] = []
    for chunk in getattr(kb, "chunks", []):
        source_path = str(getattr(chunk, "source_path", "") or "")
        loc = str(getattr(chunk, "loc", "") or "")
        rid = str(getattr(chunk, "rid", "") or "")
        cite_key = str(getattr(chunk, "cite_key", "") or "")
        excerpt = _excerpt_from_text(str(getattr(chunk, "text", "") or ""))
        entry = {
            "source_path": source_path,
            "loc": loc,
            "rid": rid,
            "cite_key": cite_key,
            "excerpt": excerpt,
        }
        chunks.append(entry)
        if cite_key:
            cite_keys[cite_key] = {"source_path": source_path, "loc": loc, "excerpt": excerpt}
        page_key = kb_cite_key(source_path, loc)
        page_keys.setdefault(page_key, []).append(entry)
        if rid:
            rids[rid] = {"source_path": source_path, "loc": loc, "excerpt": excerpt}
    return {"cite_keys": cite_keys, "rids": rids, "page_keys": page_keys, "chunks": chunks}
