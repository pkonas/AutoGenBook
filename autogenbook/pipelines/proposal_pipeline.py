from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from pydantic import ValidationError

from autogenbook.prompts.proposal_loader import load_proposal_prompts
from autogenbook.prompts.registry import get_prompt, set_prompt_registry
from autogenbook.audit.latex_auditor import AuditorConfig, audit_latex
from autogenbook.audit.types import AuditSeverity
from autogenbook.citations.markdown import (
    extract_markdown_citation_keys,
    normalize_markdown_citation_keys,
)
from autogenbook.retrieval.kb_citations import build_kb_index
from autogenbook.retrieval.manager import RetrievalManager
from autogenbook.retrieval.mcp_papers import MCPPaperRetriever, items_from_tool_result
from autogenbook.retrieval.tavily import TavilyRetriever
from autogenbook.retrieval.types import RetrievalItem
from autogenbook.schemas.proposal import (
    ProposalLLM1Output,
    ProposalOutlineOutput,
    ProposalReviewOutput,
)
from autogenbook.llm_usage import log_usage, write_run_meta
from autogenbook.schemas.validate import (
    format_validation_error,
    json_schema_snippet,
    validate_or_raise,
)
from openrouter_llm import LLMConfig, OpenRouterLLM
from rag_kb import KnowledgeBase
from utils import extract_first_json_object, safe_filename

from ..state import RunContext


USER_INPUTS_START = "-----PROPOSAL_USER_INPUTS-----"
USER_INPUTS_END = "-----END_PROPOSAL_USER_INPUTS-----"
FINAL_REVIEW_DELIM = "-----FINAL_REVIEW_REPORT-----"


def _write_run_meta(
    run_ctx: RunContext,
    args: Any,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error: Optional[str],
    models: Optional[Dict[str, Any]] = None,
    token_totals: Optional[Dict[str, Any]] = None,
    cost_totals_usd: Optional[Dict[str, Any]] = None,
) -> None:
    write_run_meta(
        run_ctx=run_ctx,
        args=args,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        error=error,
        models=models or {},
        token_totals=token_totals or {},
        cost_totals_usd=cost_totals_usd or {},
    )


def _require_dir(path: Optional[Path], label: str) -> Optional[Path]:
    if path is None:
        print(f"Error: {label} was not provided.", file=sys.stderr)
        return None
    if not path.exists() or not path.is_dir():
        print(f"Error: {label} is missing or not a directory: {path}", file=sys.stderr)
        return None
    return path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_explicit_language(text: str) -> Optional[str]:
    match = re.search(r"(?im)^\s*(language|jazyk)\s*:\s*(.+)$", text)
    if match:
        value = match.group(2).strip()
        if value:
            return value
    return None


def _detect_language(text: str) -> str:
    lowered = text.lower()
    if re.search(r"[\u00e1\u010d\u010f\u00e9\u011b\u00ed\u0148\u00f3\u0159\u0161\u0165\u00fa\u016f\u00fd\u017e]", lowered):
        return "Czech"
    if re.search(r"[\u00e4\u00f6\u00fc\u00df]", lowered):
        return "German"
    return "English"


def _apply_language(prompts: Dict[str, str], language: str) -> Dict[str, str]:
    rendered: Dict[str, str] = {}
    for key, value in prompts.items():
        rendered[key] = value.replace("{{LANGUAGE}}", language)
    return rendered


def _resolve_value_override(args: Any, arg_name: str, env_var: str) -> Optional[str]:
    value = getattr(args, arg_name, None)
    if value:
        value = str(value).strip()
        if value:
            return value
    env_val = os.environ.get(env_var, "").strip()
    return env_val or None


def _resolve_model_override(args: Any, arg_name: str, env_var: str) -> Optional[str]:
    return _resolve_value_override(args, arg_name, env_var)


def _format_kb_context(label: str, text: str) -> str:
    if not text:
        return f"[{label}] (none)"
    return f"[{label}]\n{text}"


def _strip_kb_cite_keys(text: str) -> str:
    if not text:
        return text
    return re.sub(r'\s+cite_key="[^"]*"', "", text)


def _strip_user_inputs_block(text: str) -> str:
    if USER_INPUTS_START not in text:
        return text
    head, tail = text.split(USER_INPUTS_START, 1)
    if USER_INPUTS_END in tail:
        _, rest = tail.split(USER_INPUTS_END, 1)
        rest = rest.lstrip("\n")
        if rest:
            return head.rstrip() + "\n\n" + rest
        return head.rstrip() + "\n"
    return head.rstrip() + "\n"


def _sanitize_outline_title(title: str) -> str:
    cleaned = re.sub(r"^\s*#+\s*", "", str(title or "")).strip()
    if not cleaned:
        return ""
    tail_patterns = [
        r"\s*[\(\[][^)\]]*\b(?:page|pages|word|words|char|chars|character|characters|limit|target|max|min|approx|stran|slov)\b[^)\]]*[\)\]]\s*$",
        r"\s*[-–—]\s*(?:max|min|limit|target|approx)\b[^:]*[:=]?\s*[\d.,]+\s*(?:page|pages|word|words|char|chars|stran|slov)?\s*$",
        r"\s*\b(?:max|min|target|approx)\b\s*[:=]?\s*[\d.,]+\s*(?:page|pages|word|words|char|chars|stran|slov)\s*$",
    ]
    for pattern in tail_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _sanitize_markdown_headings(markdown: str) -> str:
    if not markdown:
        return markdown
    out: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6}\s+)(.+?)\s*$", line)
        if not match:
            out.append(line)
            continue
        title = _sanitize_outline_title(match.group(2))
        out.append(f"{match.group(1)}{title or match.group(2).strip()}")
    return "\n".join(out)


def _iter_outline_nodes(sections: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        yield sec
        children = sec.get("subsections") or []
        if children:
            yield from _iter_outline_nodes(children)


def _outline_quality_score(outline: Optional[Dict[str, Any]]) -> tuple[int, Dict[str, Any]]:
    if not isinstance(outline, dict):
        return -10**9, {"reason": "invalid_outline"}
    sections = list(_iter_outline_nodes(outline.get("outline") or []))
    top_sections = outline.get("outline") or []
    total_sections = len(sections)
    top_count = len(top_sections) if isinstance(top_sections, list) else 0
    non_generic_titles = 0
    with_purpose = 0
    with_targets = 0
    for sec in sections:
        title = _sanitize_outline_title(str(sec.get("title") or ""))
        if title and not re.match(r"^section\s+\d+$", title, flags=re.IGNORECASE):
            non_generic_titles += 1
        if str(sec.get("purpose") or "").strip():
            with_purpose += 1
        target = sec.get("target_length") if isinstance(sec.get("target_length"), dict) else {}
        if target.get("words") or target.get("pages"):
            with_targets += 1
    meta = outline.get("project_metadata") if isinstance(outline.get("project_metadata"), dict) else {}
    title = str(meta.get("title") or "").strip().lower()
    author = str(meta.get("author") or "").strip().lower()
    generic_title = title in {"", "untitled", "untitled proposal", "proposal overview"}
    generic_author = author in {"", "unknown", "unknown author"}
    score = total_sections * 100 + top_count * 30 + non_generic_titles * 15 + with_purpose * 4 + with_targets * 3
    if generic_title:
        score -= 500
    if generic_author:
        score -= 250
    if total_sections <= 1:
        score -= 400
    if non_generic_titles == 0:
        score -= 300
    return score, {
        "total_sections": total_sections,
        "top_sections": top_count,
        "non_generic_titles": non_generic_titles,
        "with_purpose": with_purpose,
        "with_target_length": with_targets,
        "generic_title": generic_title,
        "generic_author": generic_author,
    }


_REF_HEADING_RE = re.compile(
    r"^\s*(references|bibliography|literature|zdroje|pouzit[ae]\s+literatura|pou[zž]it[aá]\s+literatura|seznam\s+literatury)\s*:?\s*$",
    flags=re.IGNORECASE,
)


def _extract_user_reference_entries(text: str) -> list[str]:
    lines = text.splitlines()
    start_idx: Optional[int] = None
    for idx, line in enumerate(lines):
        if _REF_HEADING_RE.match(line.strip()):
            start_idx = idx + 1
            break
    if start_idx is None:
        return []

    entries: list[str] = []
    current: list[str] = []
    numbered_seen = False
    for raw in lines[start_idx:]:
        line = raw.strip()
        if not line:
            if current:
                entries.append(" ".join(current).strip())
                current = []
            continue
        if line == USER_INPUTS_START:
            break
        if _REF_HEADING_RE.match(line) and entries:
            break
        numbered = re.match(r"^(?:\[\d+\]|\(\d+\)|\d+[.)])\s*(.+)$", line)
        if numbered:
            numbered_seen = True
            if current:
                entries.append(" ".join(current).strip())
            current = [numbered.group(1).strip()]
            continue
        if entries and re.match(r"^\d{1,2}\.\s+[A-Z]", line):
            break
        if current:
            current.append(line)
            continue
        if numbered_seen:
            current = [line]
            continue
        entries.append(line)
    if current:
        entries.append(" ".join(current).strip())

    unique: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        cleaned = re.sub(r"\s+", " ", entry).strip(" \t-")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def _normalize_user_reference_entry(entry_text: str, idx: int) -> Dict[str, Any]:
    text = re.sub(r"\s+", " ", str(entry_text or "")).strip(" \t-")
    if not text:
        text = f"User reference {idx}"
    doi_match = re.search(r"\b(10\.\d{4,9}/[^\s,;]+)", text, flags=re.IGNORECASE)
    doi = doi_match.group(1).rstrip(".;,") if doi_match else None
    url_match = re.search(r"(https?://\S+)", text, flags=re.IGNORECASE)
    url = url_match.group(1).rstrip(").,;") if url_match else None
    year_match = re.search(r"\b(19|20)\d{2}\b", text)
    year = int(year_match.group(0)) if year_match else None
    arxiv_id = _extract_arxiv_id_from_url(url or "")
    authors: list[str] = []
    title = text
    if ":" in text:
        maybe_authors, maybe_title = text.split(":", 1)
        if re.search(r"[A-Za-z]", maybe_authors) and len(maybe_authors) <= 220:
            guesses = [p.strip() for p in maybe_authors.split(",") if p.strip()]
            if guesses:
                if len(guesses) == 1:
                    authors = guesses
                else:
                    # Pair common "Surname Initials" comma-separated patterns.
                    paired: list[str] = []
                    i = 0
                    while i < len(guesses):
                        if i + 1 < len(guesses) and re.search(r"^[A-Z][A-Za-z-]*\.?$", guesses[i + 1]):
                            paired.append(f"{guesses[i]}, {guesses[i + 1]}")
                            i += 2
                        else:
                            paired.append(guesses[i])
                            i += 1
                    authors = paired
                if maybe_title.strip():
                    title = maybe_title.strip()
    if not authors:
        first_sentence = text.split(".", 1)[0].strip()
        if first_sentence and len(first_sentence) <= 120 and re.search(r"[A-Za-z]", first_sentence):
            authors = [first_sentence]
    title = title.strip() or text
    if url and not year and not doi and not arxiv_id:
        year = datetime.now(timezone.utc).year
    accessed = datetime.now(timezone.utc).date().isoformat() if url else None
    return {
        "source_key": f"SRC:USER-{idx:04d}",
        "source_type": "user",
        "title": title,
        "authors": authors,
        "year": year,
        "url": url,
        "doi": doi,
        "arxiv_id": arxiv_id or None,
        "accessed_date": accessed,
        "publisher": None,
        "venue": None,
        "kb_source_path": None,
        "notes": text,
    }


def _tokenize_match_terms(text: str) -> set[str]:
    if not text:
        return set()
    return {t for t in re.findall(r"[a-z0-9]{3,}", text.lower()) if t}


def _seed_item_relevance(item: RetrievalItem, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    item_tokens = _tokenize_match_terms(
        " ".join(
            [
                str(item.title or ""),
                str(item.text or ""),
                " ".join(item.authors or []),
                str(item.venue or ""),
                str(item.doi or ""),
                str(item.url or ""),
            ]
        )
    )
    if not item_tokens:
        return 0.0
    overlap = len(query_tokens & item_tokens)
    score = overlap / max(1, len(query_tokens))
    if item.doi:
        score += 0.2
    if item.year:
        score += 0.1
    return score


def _select_seed_items_for_query(
    query: str,
    seed_items: list[RetrievalItem],
    *,
    k: int,
) -> list[RetrievalItem]:
    if k <= 0 or not seed_items:
        return []
    query_tokens = _tokenize_match_terms(query)
    if not query_tokens:
        return seed_items[:k]
    scored: list[tuple[float, RetrievalItem]] = []
    for item in seed_items:
        scored.append((_seed_item_relevance(item, query_tokens), item))
    scored.sort(key=lambda row: row[0], reverse=True)
    selected = [item for score, item in scored if score > 0][:k]
    if selected:
        return selected
    return seed_items[:k]


def _merge_retrieval_items(
    primary: list[RetrievalItem],
    supplemental: list[RetrievalItem],
) -> list[RetrievalItem]:
    merged: list[RetrievalItem] = []
    seen: set[str] = set()
    for item in (primary or []) + (supplemental or []):
        key = str(item.cite_key or item.rid or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(item)
    return merged


def _section_key(sec: Dict[str, Any], path_tuple: Tuple[str, ...]) -> str:
    raw_id = str(sec.get("id") or "").strip()
    if raw_id:
        return raw_id
    if path_tuple:
        return " > ".join(path_tuple)
    return str(sec.get("title") or "section")


def _section_dir(out_dir: Path, sec: Dict[str, Any], path_tuple: Tuple[str, ...]) -> Path:
    key = _section_key(sec, path_tuple)
    digest_src = f"{key}||{sec.get('id','')}||{' > '.join(path_tuple)}"
    digest = hashlib.sha1(digest_src.encode("utf-8", errors="ignore")).hexdigest()[:8]
    base = safe_filename(key)
    if not base:
        base = "section"
    return out_dir / "sections" / f"{base}_{digest}"


def _legacy_section_dir(out_dir: Path, sec: Dict[str, Any], path_tuple: Tuple[str, ...]) -> Path:
    key = _section_key(sec, path_tuple)
    base = safe_filename(key)
    if not base:
        base = "section"
    return out_dir / "sections" / base


def _migrate_legacy_section_dir(out_dir: Path, sec: Dict[str, Any], path_tuple: Tuple[str, ...]) -> None:
    new_dir = _section_dir(out_dir, sec, path_tuple)
    legacy_dir = _legacy_section_dir(out_dir, sec, path_tuple)
    if new_dir.exists() or not legacy_dir.exists():
        return
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        for entry in legacy_dir.iterdir():
            if not entry.is_file():
                continue
            shutil.copy2(entry, new_dir / entry.name)
    except Exception:
        return


def _write_section_artifacts(
    section_dir: Path,
    *,
    markdown: str,
    citations: Iterable[str],
    used_sources: list[Dict[str, Any]],
    tool_calls: list[Dict[str, Any]],
) -> None:
    section_dir.mkdir(parents=True, exist_ok=True)
    (section_dir / "section.md").write_text(markdown.strip() + "\n", encoding="utf-8")
    (section_dir / "citations.json").write_text(
        json.dumps({"citations": sorted(set(citations))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (section_dir / "used_sources.json").write_text(
        json.dumps({"used_sources": used_sources}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (section_dir / "tool_calls.json").write_text(
        json.dumps({"tool_calls": tool_calls}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def _parse_user_inputs(text: str) -> Tuple[set[str], set[str]]:
    answered: set[str] = set()
    refused: set[str] = set()
    if USER_INPUTS_START not in text:
        return answered, refused
    section = text.split(USER_INPUTS_START, 1)[1]
    if USER_INPUTS_END in section:
        section = section.split(USER_INPUTS_END, 1)[0]
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        match_refusal = re.match(r"^(Q-\d+)_REFUSAL:\s*(.*)$", line, flags=re.I)
        if match_refusal:
            qid = match_refusal.group(1).upper()
            refused.add(qid)
            answered.add(qid)
            continue
        match_answer = re.match(r"^(Q-\d+):\s*(.*)$", line, flags=re.I)
        if match_answer:
            qid = match_answer.group(1).upper()
            answered.add(qid)
    return answered, refused


def _append_user_inputs(path: Path, lines: Iterable[str]) -> None:
    text = _read_text(path)
    block = "\n".join(lines).rstrip()
    if USER_INPUTS_START in text:
        head, tail = text.split(USER_INPUTS_START, 1)
        if USER_INPUTS_END in tail:
            existing, rest = tail.split(USER_INPUTS_END, 1)
            existing = existing.rstrip()
            merged = "\n".join([s for s in [existing, block] if s])
            new_text = head + USER_INPUTS_START + "\n" + merged + "\n" + USER_INPUTS_END + rest
            path.write_text(new_text, encoding="utf-8")
            return
    new_text = text.rstrip() + "\n\n" + USER_INPUTS_START + "\n" + block + "\n" + USER_INPUTS_END + "\n"
    path.write_text(new_text, encoding="utf-8")


def _log_llm_usage(
    out_dir: Optional[Path],
    llm: OpenRouterLLM,
    label: str,
    usage: Optional[Dict[str, int]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if out_dir is None:
        return
    usage = usage or llm.get_last_usage()
    if not usage:
        return
    try:
        print(llm.format_usage_line(usage, label=label))
    except Exception:
        pass
    log_usage(out_dir, label, llm, usage, extra or {})


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    total = max(0, int(seconds))
    mins, sec = divmod(total, 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs}h {mins}m {sec}s"
    if mins:
        return f"{mins}m {sec}s"
    return f"{sec}s"


def _log_progress(logger: Any, message: str) -> None:
    if logger is not None:
        try:
            logger.info(message)
            return
        except Exception:
            pass
    print(message)


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _count_chars(text: str) -> int:
    compact = re.sub(r"\s+", " ", text).strip()
    return len(compact)


def _bullet_ratio(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    bullet_lines = [
        line for line in lines if re.match(r"^[-*+]\s+.+", line)
    ]
    return len(bullet_lines) / max(1, len(lines))


def _collapse_bullets_to_paragraphs(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        paragraph = " ".join(part.strip() for part in buffer if part.strip()).strip()
        if paragraph and paragraph[-1] not in ".!?":
            paragraph += "."
        out.append(paragraph)
        buffer.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            out.append("")
            continue
        if re.match(r"^[-*+]\s+.+", stripped) or re.match(r"^\d+[.)]\s+.+", stripped):
            item = re.sub(r"^([-*+]\s+|\d+[.)]\s+)", "", stripped)
            buffer.append(item)
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


def _salvage_citation_sentence(language: str, cite_key: str) -> str:
    lang = (language or "").lower()
    if lang.startswith("cs") or "czech" in lang:
        return f"Relevantní zdroje zahrnují [{cite_key}]."
    if lang.startswith("de") or "german" in lang:
        return f"Relevante Quellen umfassen [{cite_key}]."
    return f"Relevant sources include [{cite_key}]."


def _build_minimal_section_fallback(
    *,
    title: str,
    purpose: str,
    what_to_write: list[str],
    language: str,
    cite_key: Optional[str] = None,
) -> str:
    purpose_text = str(purpose or "").strip() or str(title or "").strip()
    details = [str(item).strip() for item in (what_to_write or []) if str(item).strip()]
    details_text = " ".join(details[:6]).strip()
    lang = (language or "").lower()
    if lang.startswith("cs") or "czech" in lang:
        base = (
            f"Tato část shrnuje klíčový záměr: {purpose_text}. "
            "Jde o minimální pracovní verzi, aby bylo možné pokračovat v generování návrhu."
        )
        if details_text:
            base += " Zaměřuje se zejména na tyto body: " + details_text + "."
        tail = (
            "V navazující iteraci má být sekce rozšířena o konkrétní metodiku, měřitelné výstupy "
            "a formální požadavky výzvy."
        )
    elif lang.startswith("de") or "german" in lang:
        base = (
            f"Dieser Abschnitt fasst die zentrale Zielsetzung zusammen: {purpose_text}. "
            "Dies ist eine minimale Arbeitsversion, damit die Proposal-Generierung fortgesetzt werden kann."
        )
        if details_text:
            base += " Schwerpunkt liegt auf folgenden Punkten: " + details_text + "."
        tail = (
            "In der nächsten Iteration soll dieser Abschnitt um konkrete Methodik, messbare Ergebnisse "
            "und formale Anforderungen der Ausschreibung ergänzt werden."
        )
    else:
        base = (
            f"This section summarizes the core intent: {purpose_text}. "
            "This is a minimal working draft so proposal generation can continue."
        )
        if details_text:
            base += " It focuses on the following points: " + details_text + "."
        tail = (
            "The next iteration should expand this section with concrete methodology, measurable outcomes, "
            "and formal call-specific requirements."
        )
    text = base + "\n\n" + tail
    if cite_key:
        text = text.rstrip() + "\n\n" + _salvage_citation_sentence(language, cite_key)
    return text.strip()


def _split_markdown_sections(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if re.match(r"^##\s+", line):
            if current:
                sections.append("\n".join(current).strip())
                current = []
        current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [s for s in sections if s]


def _target_word_bounds(
    target_length: Any,
    *,
    words_per_page: int = 300,
    tolerance: float = 0.25,
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    if not isinstance(target_length, dict):
        return None, None, None
    words = target_length.get("words")
    pages = target_length.get("pages")
    target_words = None
    if isinstance(words, (int, float)) and words > 0:
        target_words = int(words)
    elif isinstance(pages, (int, float)) and pages > 0:
        target_words = int(round(float(pages) * words_per_page))
    if not target_words:
        return None, None, None
    min_words = max(1, int(round(target_words * (1.0 - tolerance))))
    max_words = max(1, int(round(target_words * (1.0 + tolerance))))
    return target_words, min_words, max_words


def _target_char_bounds(
    target_length: Any,
    *,
    chars_per_page: int = 2500,
    tolerance: float = 0.25,
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    if not isinstance(target_length, dict):
        return None, None, None
    char_limit = target_length.get("char_limit")
    pages = target_length.get("pages")
    target_chars = None
    if isinstance(char_limit, (int, float)) and char_limit > 0:
        target_chars = int(char_limit)
    elif isinstance(pages, (int, float)) and pages > 0:
        target_chars = int(round(float(pages) * chars_per_page))
    if not target_chars:
        return None, None, None
    min_chars = max(1, int(round(target_chars * (1.0 - tolerance))))
    max_chars = max(1, int(round(target_chars * (1.0 + tolerance))))
    return target_chars, min_chars, max_chars


def _target_doc_word_bounds(
    format_spec: Any,
    *,
    words_per_page: int = 300,
    chars_per_page: int = 2500,
    tolerance: float = 0.25,
) -> Tuple[
    Optional[int],
    Optional[int],
    Optional[int],
    Optional[int],
    Optional[int],
    Optional[float],
    Optional[int],
]:
    if not isinstance(format_spec, dict):
        return None, None, None, None, None, None, None
    limits = format_spec.get("limits", {}) if isinstance(format_spec.get("limits"), dict) else {}
    page_limit = limits.get("page_limit")
    word_limit = limits.get("word_limit")
    char_limit = limits.get("char_limit")
    target_words = None
    target_chars = None
    target_pages = None
    if isinstance(word_limit, (int, float)) and word_limit > 0:
        target_words = int(word_limit)
    if isinstance(char_limit, (int, float)) and char_limit > 0:
        target_chars = int(char_limit)
    if isinstance(page_limit, (int, float)) and page_limit > 0:
        target_pages = float(page_limit)
        if target_chars is None:
            target_chars = int(round(target_pages * chars_per_page))
    min_words = max_words = None
    min_chars = max_chars = None
    if target_words:
        min_words = max(1, int(round(target_words * (1.0 - tolerance))))
        max_words = max(1, int(round(target_words * (1.0 + tolerance))))
    if target_chars:
        min_chars = max(1, int(round(target_chars * (1.0 - tolerance))))
        max_chars = max(1, int(round(target_chars * (1.0 + tolerance))))
    return target_words, min_words, max_words, target_chars, min_chars, max_chars, target_pages
def _is_noninteractive() -> bool:
    return os.environ.get("AUTOGENBOOK_NONINTERACTIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parse_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(1, value)


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [text]


def _filter_req_ids(values: Iterable[str]) -> list[str]:
    valid: list[str] = []
    for v in values:
        if re.match(r"^REQ-[A-Za-z0-9][A-Za-z0-9_-]{1,63}$", v):
            valid.append(v)
    return valid


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "ano", "a"}:
            return True
        if text in {"0", "false", "no", "n", "off", "ne"}:
            return False
    return default


def _coerce_score_0_10(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        if value is None:
            return default
        score = float(value)
    except Exception:
        return default
    return max(0, min(10, int(round(score))))


def _normalize_review_output(output: Any, *, language_default: str) -> Any:
    if not isinstance(output, dict):
        return output

    language = str(output.get("language") or language_default or "").strip() or "English"

    compliance_raw = output.get("compliance_assessment")
    compliance = compliance_raw if isinstance(compliance_raw, dict) else {}
    missing_raw = compliance.get("missing_requirements")
    missing_requirements: list[Dict[str, Any]] = []
    if isinstance(missing_raw, list):
        for idx, item in enumerate(missing_raw, start=1):
            if not isinstance(item, dict):
                continue
            req_id = str(item.get("req_id") or "").strip()
            if not re.match(r"^REQ-[A-Za-z0-9][A-Za-z0-9_-]{1,63}$", req_id):
                req_id = f"REQ-{idx:03d}"
            evidence: list[Dict[str, Any]] = []
            evidence_raw = item.get("evidence")
            if isinstance(evidence_raw, list):
                for ev in evidence_raw:
                    if not isinstance(ev, dict):
                        continue
                    evidence.append(
                        {
                            "source_id": str(ev.get("source_id") or "KB1-UNKNOWN"),
                            "file": str(ev.get("file") or "unknown"),
                            "chunk_id": str(ev.get("chunk_id") or "unknown"),
                            "quote": str(ev.get("quote") or "UNKNOWN"),
                        }
                    )
            missing_requirements.append(
                {
                    "req_id": req_id,
                    "problem": str(item.get("problem") or "").strip() or "Unspecified issue.",
                    "evidence": evidence,
                    "fix_suggestion": str(item.get("fix_suggestion") or "").strip()
                    or "Provide a concrete fix.",
                }
            )
    other_raw = compliance.get("other_issues")
    other_issues: list[Dict[str, Any]] = []
    valid_other_types = {"format", "annex", "ethics", "budget", "logic", "other"}
    if isinstance(other_raw, list):
        for item in other_raw:
            if not isinstance(item, dict):
                continue
            issue_type = str(item.get("type") or "").strip().lower()
            if issue_type not in valid_other_types:
                issue_type = "other"
            other_issues.append(
                {
                    "type": issue_type,
                    "problem": str(item.get("problem") or "").strip() or "Unspecified issue.",
                    "fix": str(item.get("fix") or "").strip() or "Provide a concrete fix.",
                }
            )
    compliance_assessment = {
        "is_compliant": _coerce_bool(compliance.get("is_compliant"), False),
        "missing_requirements": missing_requirements,
        "other_issues": other_issues,
    }

    scientific_raw = output.get("scientific_assessment")
    scientific = scientific_raw if isinstance(scientific_raw, dict) else {}
    gaps_raw = scientific.get("gaps")
    gaps: list[Dict[str, Any]] = []
    if isinstance(gaps_raw, list):
        for item in gaps_raw:
            if not isinstance(item, dict):
                continue
            gaps.append(
                {
                    "where": str(item.get("where") or "").strip() or "UNKNOWN",
                    "gap": str(item.get("gap") or "").strip() or "Unspecified gap.",
                    "why_it_matters": str(item.get("why_it_matters") or "").strip()
                    or "Impact not specified.",
                    "suggested_fix": str(item.get("suggested_fix") or "").strip()
                    or "Provide a concrete fix.",
                }
            )
    scientific_assessment = {
        "is_thematically_aligned": _coerce_bool(scientific.get("is_thematically_aligned"), True),
        "gaps": gaps,
        "innovation_score_0_10": _coerce_score_0_10(scientific.get("innovation_score_0_10"), 0),
        "feasibility_score_0_10": _coerce_score_0_10(scientific.get("feasibility_score_0_10"), 0),
    }

    questions_raw = output.get("user_questions")
    user_questions: list[Dict[str, Any]] = []
    if isinstance(questions_raw, list):
        for idx, item in enumerate(questions_raw, start=1):
            if not isinstance(item, dict):
                continue
            qid = str(item.get("id") or "").strip().upper()
            if not re.match(r"^Q-[0-9]{3}$", qid):
                qid = f"Q-{idx:03d}"
            question = str(item.get("question") or "").strip()
            why_needed = str(item.get("why_needed") or "").strip()
            if not question or not why_needed:
                continue
            user_questions.append(
                {
                    "id": qid,
                    "question": question,
                    "why_needed": why_needed,
                    "where_to_insert_in_proposal_input": str(
                        item.get("where_to_insert_in_proposal_input") or "Additional details"
                    ).strip(),
                    "if_user_refuses_then_write": str(
                        item.get("if_user_refuses_then_write") or f"AUTO_SKIPPED: {question}"
                    ).strip(),
                }
            )

    return {
        "language": language,
        "compliance_assessment": compliance_assessment,
        "scientific_assessment": scientific_assessment,
        "user_questions": user_questions,
        "instructions_to_llm1": _coerce_str_list(output.get("instructions_to_llm1")),
        "instructions_to_llm2": _coerce_str_list(output.get("instructions_to_llm2")),
        "should_iterate": _coerce_bool(output.get("should_iterate"), True),
        "stop_reason": str(output.get("stop_reason") or "").strip() or "No stop reason provided.",
    }


def _infer_req_type(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("format", "page", "pages", "word", "words", "limit")):
        return "format"
    if any(token in lowered for token in ("ethic", "etik")):
        return "ethics"
    if any(token in lowered for token in ("budget", "cost", "finance", "rozpo")):
        return "budget"
    if any(token in lowered for token in ("eligib", "opravn")):
        return "eligibility"
    if any(token in lowered for token in ("annex", "appendix", "priloha", "příloha")):
        return "annex"
    if any(token in lowered for token in ("evaluation", "criteria", "hodnoc")):
        return "evaluation"
    if any(token in lowered for token in ("legal", "law", "pravn")):
        return "legal"
    if any(token in lowered for token in ("section", "kapitol", "část")):
        return "section"
    return "other"


def _infer_req_priority(text: str, required_flag: Optional[bool] = None) -> str:
    if required_flag is True:
        return "must"
    lowered = text.lower()
    if any(token in lowered for token in ("must", "mandatory", "required", "povinn", "mus")):
        return "must"
    if any(token in lowered for token in ("should", "recommended", "doporuč")):
        return "should"
    return "may"


def _normalize_kb1_evidence(item: Any) -> list[Dict[str, Any]]:
    evidence = item.get("evidence") if isinstance(item, dict) else None
    normalized: list[Dict[str, Any]] = []
    if isinstance(evidence, list):
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            source_id = str(ev.get("source_id") or "KB1-UNKNOWN").strip() or "KB1-UNKNOWN"
            file = str(ev.get("file") or "unknown").strip() or "unknown"
            chunk_id = str(ev.get("chunk_id") or "unknown").strip() or "unknown"
            quote = str(ev.get("quote") or "UNKNOWN_REQUIREMENT_EVIDENCE").strip()
            normalized.append(
                {
                    "source_id": source_id,
                    "file": file,
                    "chunk_id": chunk_id,
                    "quote": quote or "UNKNOWN_REQUIREMENT_EVIDENCE",
                }
            )
    if normalized:
        return normalized
    return [
        {
            "source_id": "KB1-UNKNOWN",
            "file": "unknown",
            "chunk_id": "unknown",
            "quote": "UNKNOWN_REQUIREMENT_EVIDENCE",
        }
    ]


def _alloc_llm1_question_id(topic: str, used_ids: set[str]) -> str:
    digest = hashlib.sha1(topic.encode("utf-8", errors="ignore")).hexdigest()
    base = 900 + (int(digest[:4], 16) % 100)
    for offset in range(100):
        candidate = 900 + ((base - 900 + offset) % 100)
        qid = f"Q-{candidate:03d}"
        if qid not in used_ids:
            return qid
    return "Q-900"


def _normalize_llm1_output(
    output: Any, *, language_default: str, user_page_limit: Optional[int] = None
) -> Any:
    if not isinstance(output, dict):
        return output
    language = str(output.get("language") or language_default or "").strip() or "English"

    reqs_raw = output.get("kb1_requirement_digest")
    if not isinstance(reqs_raw, list):
        for key in ("requirements", "sections", "reqs"):
            if isinstance(output.get(key), list):
                reqs_raw = output.get(key)
                break
    reqs: list[Dict[str, Any]] = []
    if isinstance(reqs_raw, list):
        for idx, item in enumerate(reqs_raw, start=1):
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or item.get("req_id") or item.get("code") or "").strip()
            if raw_id and re.match(r"^REQ-[A-Za-z0-9][A-Za-z0-9_-]{1,63}$", raw_id):
                req_id = raw_id
            else:
                req_id = f"REQ-{idx:03d}"
            title = str(item.get("title") or item.get("name") or item.get("section_title") or "").strip()
            requirement_text = str(
                item.get("requirement_text")
                or item.get("text")
                or item.get("description")
                or item.get("notes")
                or title
            ).strip()
            type_raw = str(item.get("type") or "").strip().lower()
            req_type = type_raw if type_raw in {
                "section",
                "format",
                "evaluation",
                "legal",
                "ethics",
                "budget",
                "eligibility",
                "annex",
                "other",
            } else _infer_req_type(f"{title} {requirement_text}")
            priority_raw = str(item.get("priority") or item.get("level") or "").strip().lower()
            required_flag = item.get("required") if isinstance(item.get("required"), bool) else None
            req_priority = (
                priority_raw
                if priority_raw in {"must", "should", "may"}
                else _infer_req_priority(f"{title} {requirement_text}", required_flag)
            )
            evidence = _normalize_kb1_evidence(item)
            reqs.append(
                {
                    "id": req_id,
                    "title": title or f"Requirement {idx}",
                    "type": req_type,
                    "requirement_text": requirement_text or title or f"Requirement {idx}",
                    "priority": req_priority,
                    "evidence": evidence,
                }
            )
    if not reqs:
        reqs = [
            {
                "id": "REQ-001",
                "title": "UNKNOWN_REQUIREMENTS",
                "type": "other",
                "requirement_text": "UNKNOWN_REQUIREMENT",
                "priority": "must",
                "evidence": [
                    {
                        "source_id": "KB1-UNKNOWN",
                        "file": "unknown",
                        "chunk_id": "unknown",
                        "quote": "UNKNOWN_REQUIREMENT_EVIDENCE",
                    }
                ],
            }
        ]

    format_spec = output.get("format_spec")
    if not isinstance(format_spec, dict):
        format_spec = {}
    required_formats = format_spec.get("required_output_formats")
    if not isinstance(required_formats, list) or not required_formats:
        required_formats = ["markdown"]
    citation_standard = str(format_spec.get("citation_standard") or "ISO690").strip() or "ISO690"
    limits = format_spec.get("limits")
    if not isinstance(limits, dict):
        limits = {}
    if user_page_limit and not limits.get("page_limit"):
        limits["page_limit"] = user_page_limit
    format_spec = {
        "required_output_formats": [str(v).strip() for v in required_formats if str(v).strip()],
        "citation_standard": citation_standard,
        "language": language,
        "limits": {
            "page_limit": limits.get("page_limit"),
            "word_limit": limits.get("word_limit"),
            "char_limit": limits.get("char_limit"),
        },
    }

    meta_prompt = str(output.get("meta_prompt_for_llm2") or "").strip()
    if not meta_prompt:
        meta_prompt = (
            "You are LLM2 (Researcher-Outline). Produce a strict JSON outline that "
            "satisfies every KB1 requirement. Use only the required schema."
        )

    unknowns_raw = output.get("unknown_or_ambiguous") or output.get("unknown_or_ambiguous_requests") or []
    unknowns: list[Dict[str, Any]] = []
    if isinstance(unknowns_raw, list):
        for item in unknowns_raw:
            if isinstance(item, dict):
                topic = str(item.get("topic") or "").strip()
                why = str(item.get("why_unknown") or item.get("why") or "").strip()
                what = str(item.get("what_to_look_for_in_kb1") or item.get("what") or "").strip()
                if topic and why and what:
                    unknowns.append(
                        {
                            "topic": topic,
                            "why_unknown": why,
                            "what_to_look_for_in_kb1": what,
                        }
                    )

    return {
        "language": language,
        "kb1_requirement_digest": reqs,
        "format_spec": format_spec,
        "meta_prompt_for_llm2": meta_prompt,
        "unknown_or_ambiguous": unknowns,
    }


def _parse_target_length(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value).strip() if value is not None else ""
    if not text:
        return {"words": None, "pages": None}
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers:
        return {"words": None, "pages": None}
    lower = text.lower()
    max_num = max(numbers)
    if any(token in lower for token in ("page", "pages", "stran")):
        return {"pages": max_num, "words": None}
    return {"words": max_num, "pages": None}


def _extract_page_limit_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    patterns = [
        r"(?:page|pages|page\s+limit|max\s+pages|limit)\s*[:=]?\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?)",
        r"(?:počet|pocet)\s+stran\s*[:=]?\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?)",
        r"(?:rozsah|limit)\s+stran\s*[:=]?\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?)",
        r"max(?:imum)?\s*[:=]?\s*([0-9]+)\s*(?:pages|stran|strán)",
    ]
    matches: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            segment = match.group(1) if match.groups() else ""
            nums = [int(n) for n in re.findall(r"\d+", segment)]
            if nums:
                matches.append(max(nums))
    if not matches:
        return None
    return max(matches)


def _parse_limit_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    nums = [int(n) for n in re.findall(r"\d+", text)]
    return max(nums) if nums else None


def _normalize_metadata_output(output: Any) -> Dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    def _pick(*keys: str) -> str:
        for key in keys:
            value = output.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    language = _pick("language", "language_tag", "jazyk", "lang")
    title = _pick("title", "nazev", "name")
    author = _pick("author", "autor")
    grant_call = _pick("grant_call", "call", "vyzva")
    constraints = _pick("constraints", "omezeni")
    keywords = output.get("keywords") or output.get("klicova_slova") or []
    keywords_list = _coerce_str_list(keywords)
    page_limit = _parse_limit_value(output.get("page_limit"))
    word_limit = _parse_limit_value(output.get("word_limit"))
    char_limit = _parse_limit_value(output.get("char_limit"))
    raw_sections = output.get("section_constraints") or output.get("section_constraints_list") or []
    section_constraints: list[Dict[str, Any]] = []
    if isinstance(raw_sections, list):
        for item in raw_sections:
            if not isinstance(item, dict):
                continue
            hint = str(
                item.get("section_hint") or item.get("section") or item.get("title") or ""
            ).strip()
            requirement = str(
                item.get("requirement") or item.get("constraint") or ""
            ).strip()
            page_lim = _parse_limit_value(item.get("page_limit") or item.get("pages"))
            word_lim = _parse_limit_value(item.get("word_limit") or item.get("words"))
            char_lim = _parse_limit_value(item.get("char_limit") or item.get("chars"))
            if not hint and not requirement:
                continue
            section_constraints.append(
                {
                    "section_hint": hint,
                    "requirement": requirement or None,
                    "page_limit": page_lim,
                    "word_limit": word_lim,
                    "char_limit": char_lim,
                }
            )

    return {
        "language": language or None,
        "title": title or None,
        "author": author or None,
        "keywords": keywords_list,
        "grant_call": grant_call or None,
        "constraints": constraints or None,
        "page_limit": page_limit,
        "word_limit": word_limit,
        "char_limit": char_limit,
        "section_constraints": section_constraints,
    }


def _extract_metadata_with_llm(
    llm: OpenRouterLLM,
    proposal_text: str,
    *,
    system_prompt: str,
    model: Optional[str] = None,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    user_prompt = (
        "PROPOSAL_INPUT:\n"
        f"{proposal_text}\n\n"
        "Return JSON only."
    )
    output = llm.chat_json_object(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        allow_tools=False,
        model=model,
    )
    if out_dir is not None:
        _log_llm_usage(out_dir, llm, "metadata")
    return _normalize_metadata_output(output)


def _match_section_constraint(
    title: str,
    path_tuple: Tuple[str, ...],
    constraints: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    title_norm = _normalize_for_match(title)
    path_norm = _normalize_for_match(" > ".join(path_tuple))
    for item in constraints or []:
        hint = _normalize_for_match(str(item.get("section_hint") or ""))
        if not hint:
            continue
        if hint in title_norm or hint in path_norm:
            return item
    return None


def _apply_section_constraint(
    target_length: Any,
    constraint: Dict[str, Any],
    *,
    chars_per_page: int = 2500,
) -> Dict[str, Any]:
    base = dict(target_length) if isinstance(target_length, dict) else {}
    page_limit = constraint.get("page_limit")
    word_limit = constraint.get("word_limit")
    char_limit = constraint.get("char_limit")
    if page_limit:
        base["pages"] = page_limit
        base["char_limit"] = int(round(float(page_limit) * chars_per_page))
    if word_limit:
        base["words"] = word_limit
    if char_limit:
        base["char_limit"] = char_limit
    return base


def _normalize_outline_section(
    sec: Dict[str, Any],
    *,
    idx: int,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    raw_id = str(sec.get("id") or "").strip()
    if raw_id and re.match(r"^SEC-[0-9]{3}(?:\.[0-9]+)*$", raw_id):
        sec_id = raw_id
    else:
        if parent_id:
            sec_id = f"{parent_id}.{idx}"
        else:
            sec_id = f"SEC-{idx:03d}"

    title = _sanitize_outline_title(
        str(sec.get("title") or sec.get("section_title") or sec.get("name") or "").strip()
    )
    if not title:
        title = f"Section {idx}"

    purpose = str(
        sec.get("purpose")
        or sec.get("goal")
        or sec.get("summary")
        or sec.get("description")
        or ""
    ).strip()

    what_to_write = _coerce_str_list(
        sec.get("what_to_write")
        or sec.get("content_plan")
        or sec.get("content")
        or sec.get("guidance")
    )
    if not what_to_write:
        if purpose:
            what_to_write = [purpose]
        else:
            what_to_write = [f"Draft content for {title}."]

    compliance_mapping = _filter_req_ids(
        _coerce_str_list(
            sec.get("compliance_mapping")
            or sec.get("req_ids")
            or sec.get("requirements")
            or sec.get("compliance")
        )
    )

    expected_evidence = sec.get("expected_evidence") or []
    normalized_evidence: list[Dict[str, Any]] = []
    if isinstance(expected_evidence, list):
        for item in expected_evidence:
            if isinstance(item, dict):
                source = str(item.get("source") or "KB1").strip()
                if source not in {"KB1", "KB2", "TOOLS"}:
                    source = "KB1"
                note = str(item.get("note") or "").strip()
                if note:
                    normalized_evidence.append({"source": source, "note": note})
            else:
                note = str(item).strip()
                if note:
                    normalized_evidence.append({"source": "KB1", "note": note})
    elif isinstance(expected_evidence, str):
        note = expected_evidence.strip()
        if note:
            normalized_evidence.append({"source": "KB1", "note": note})

    target_length = _parse_target_length(
        sec.get("target_length") or sec.get("length") or sec.get("approx_pages")
    )

    subsections_raw = sec.get("subsections") or sec.get("sections") or sec.get("children") or []
    normalized_subsections: list[Dict[str, Any]] = []
    if isinstance(subsections_raw, list):
        for sub_idx, sub in enumerate(subsections_raw, start=1):
            if isinstance(sub, dict):
                normalized_subsections.append(
                    _normalize_outline_section(sub, idx=sub_idx, parent_id=sec_id)
                )

    return {
        "id": sec_id,
        "title": title,
        "purpose": purpose or title,
        "what_to_write": what_to_write,
        "compliance_mapping": compliance_mapping,
        "expected_evidence": normalized_evidence,
        "subsections": normalized_subsections,
        "target_length": target_length,
    }


def _normalize_outline_output(output: Any, *, language_default: str) -> Any:
    if not isinstance(output, dict):
        return output

    language = str(output.get("language") or language_default or "").strip()
    if not language:
        language = "English"

    project_metadata_raw = output.get("project_metadata")
    if isinstance(project_metadata_raw, dict):
        pm = dict(project_metadata_raw)
        title = str(pm.get("title") or "").strip()
        author = str(pm.get("author") or "").strip()
        keywords_raw = pm.get("keywords") or output.get("keywords") or []
        grant_call_raw = pm.get("grant_call") or pm.get("grant call") or output.get("grant_call") or output.get("grant call")
    else:
        title = str(output.get("title") or "").strip()
        author = str(output.get("author") or "").strip()
        keywords_raw = output.get("keywords") or []
        grant_call_raw = output.get("grant_call") or output.get("grant call")

    if isinstance(keywords_raw, str):
        keywords = [k.strip() for k in re.split(r"[;,]", keywords_raw) if k.strip()]
    elif isinstance(keywords_raw, list):
        keywords = [str(k).strip() for k in keywords_raw if str(k).strip()]
    else:
        keywords = []
    grant_call = str(grant_call_raw or "").strip() or None

    project_title = _sanitize_outline_title(title)
    if not project_title:
        project_title = "Untitled proposal"
    project_metadata = {
        "title": project_title,
        "author": author or "Unknown author",
        "keywords": keywords,
        "grant_call": grant_call,
    }

    outline_raw = output.get("outline")
    if not isinstance(outline_raw, list):
        outline_raw = output.get("sections") if isinstance(output.get("sections"), list) else []

    normalized_outline: list[Dict[str, Any]] = []
    for idx, sec in enumerate(outline_raw, start=1):
        if isinstance(sec, dict):
            normalized_outline.append(
                _normalize_outline_section(sec, idx=idx, parent_id=None)
            )
    if not normalized_outline:
        fallback_title = str(project_metadata.get("title") or "Proposal Overview").strip()
        normalized_outline.append(
            _normalize_outline_section(
                {"title": fallback_title, "purpose": "Provide a structured proposal outline."},
                idx=1,
                parent_id=None,
            )
        )

    annexes_raw = output.get("annexes") or []
    annexes: list[Dict[str, Any]] = []
    if isinstance(annexes_raw, list):
        for idx, ann in enumerate(annexes_raw, start=1):
            if isinstance(ann, dict):
                ann_id = str(ann.get("id") or "").strip()
                if not ann_id:
                    ann_id = f"ANN-{idx:03d}"
                annexes.append(
                    {
                        "id": ann_id,
                        "title": str(ann.get("title") or "").strip() or f"Annex {idx}",
                        "required_by": _filter_req_ids(_coerce_str_list(ann.get("required_by"))),
                        "content_plan": str(ann.get("content_plan") or "").strip() or "",
                    }
                )
            elif isinstance(ann, str):
                annexes.append(
                    {
                        "id": f"ANN-{idx:03d}",
                        "title": ann.strip() or f"Annex {idx}",
                        "required_by": [],
                        "content_plan": "",
                    }
                )

    questions_raw = output.get("open_questions_for_opponent") or []
    questions: list[Dict[str, Any]] = []
    if isinstance(questions_raw, list):
        for item in questions_raw:
            if isinstance(item, dict):
                question = str(item.get("question") or "").strip()
                why_needed = str(item.get("why_needed") or "").strip()
                if not question or not why_needed:
                    continue
                where_raw = _coerce_str_list(item.get("where_it_affects_outline"))
                where_filtered = [
                    sec for sec in where_raw if re.match(r"^SEC-[0-9]{3}(?:\.[0-9]+)*$", sec)
                ]
                questions.append(
                    {
                        "question": question,
                        "why_needed": why_needed,
                        "where_it_affects_outline": where_filtered,
                    }
                )

    return {
        "language": language,
        "project_metadata": project_metadata,
        "outline": normalized_outline,
        "annexes": annexes,
        "open_questions_for_opponent": questions,
    }


def _chat_json_with_validation(
    llm: OpenRouterLLM,
    system_prompt: str,
    user_prompt: str,
    model_cls: Any,
    model: Optional[str] = None,
    max_attempts: int = 2,
    normalize_fn: Optional[Any] = None,
    out_dir: Optional[Path] = None,
    label: Optional[str] = None,
    usage_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_err: Optional[Exception] = None
    output: Any = None

    def _request_json_or_text(req_messages: list[Dict[str, str]], req_model: Optional[str]) -> Any:
        nonlocal last_err
        try:
            return llm.chat_json_object(req_messages, allow_tools=False, model=req_model)
        except Exception as exc:
            last_err = exc
            try:
                return llm.chat(req_messages, allow_tools=False, model=req_model)
            except Exception as raw_exc:
                last_err = raw_exc
                return None

    for attempt in range(max_attempts + 1):
        req_messages = messages
        usage_label = label
        if attempt > 0 and output is not None:
            if isinstance(output, dict):
                original_json = json.dumps(output, ensure_ascii=False, indent=2)
            else:
                original_json = str(output)
            schema = json_schema_snippet(model_cls, max_chars=2000)
            summary = ""
            if isinstance(last_err, ValidationError):
                summary = format_validation_error(last_err)
            elif last_err is not None:
                summary = str(last_err)
            repair_messages = [
                {
                    "role": "system",
                    "content": get_prompt("json_repair_system"),
                },
                {
                    "role": "user",
                    "content": (
                        "Original JSON:\n"
                        f"{original_json}\n\n"
                        "Validation errors:\n"
                        f"{summary}\n\n"
                        "JSON schema (truncated):\n"
                        f"{schema}"
                    ),
                },
            ]
            req_messages = repair_messages
            usage_label = f"{label}_repair" if label else None

        output = _request_json_or_text(req_messages, model)
        if out_dir and usage_label:
            extra = dict(usage_extra or {})
            extra["attempt"] = attempt
            _log_llm_usage(out_dir, llm, usage_label, extra=extra)

        if output is None:
            if attempt >= max_attempts:
                raise RuntimeError("LLM request failed while generating JSON output.") from last_err
            time.sleep(min(8.0, 0.6 * (2**attempt)))
            continue

        if not isinstance(output, dict):
            if attempt >= max_attempts:
                msg = (
                    "Failed to parse repaired JSON object."
                    if attempt > 0
                    else "Failed to parse JSON object from model output."
                )
                raise ValueError(msg) from last_err
            continue
        try:
            if normalize_fn is not None:
                output = normalize_fn(output)
            validated = validate_or_raise(model_cls, output)
            return validated.model_dump()
        except ValidationError as exc:
            last_err = exc
            if attempt >= max_attempts:
                raise
            continue
    raise ValueError("Failed to validate JSON output.")


def _iter_outline_sections(
    sections: Iterable[Dict[str, Any]],
    depth: int = 1,
    parents: Tuple[str, ...] = (),
) -> Iterable[Tuple[Dict[str, Any], int, Tuple[str, ...]]]:
    for sec in sections or []:
        title = str(sec.get("title", "")).strip()
        current_path = parents + (title,) if title else parents
        yield sec, depth, current_path
        subs = sec.get("subsections") or []
        if subs:
            yield from _iter_outline_sections(subs, depth + 1, current_path)


def _build_section_query(sec: Dict[str, Any], proposal_text: str) -> str:
    parts = [
        proposal_text,
        sec.get("title", ""),
        sec.get("purpose", ""),
        " ".join(sec.get("what_to_write", []) or []),
        " ".join(sec.get("compliance_mapping", []) or []),
    ]
    return "\n".join([str(p).strip() for p in parts if str(p).strip()])


def _split_sources_block(text: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    marker = "-----SOURCES_JSON-----"
    if marker not in text:
        return text.strip(), None, "missing_sources_delimiter"
    before, after = text.split(marker, 1)
    md = before.strip()
    try:
        sources = extract_first_json_object(after)
    except Exception as exc:
        return md, None, f"sources_json_parse_error: {exc}"
    return md, sources, None


def _extract_citation_keys(markdown: str) -> set[str]:
    return extract_markdown_citation_keys(markdown)


def _normalize_citation_keys(markdown: str, key_map: Dict[str, str]) -> str:
    return normalize_markdown_citation_keys(markdown, key_map)


def _item_metadata_complete(item: RetrievalItem) -> bool:
    title = str(item.title or "").strip()
    authors = item.authors or []
    year = item.year
    doi = str(item.doi or "").strip()
    arxiv_id = _extract_arxiv_id_from_url(item.url or "")
    if not title:
        return False
    if not authors:
        return False
    if year is None and not doi and not arxiv_id:
        return False
    return True


def _resolve_item_metadata(
    item: RetrievalItem,
    mcp_papers: Optional[MCPPaperRetriever],
) -> Optional[RetrievalItem]:
    if mcp_papers is None or not mcp_papers.is_available():
        return None
    doi = str(item.doi or "").strip()
    arxiv_id = _extract_arxiv_id_from_url(item.url or "")
    title = str(item.title or "").strip()
    query = doi or arxiv_id or title
    if not query:
        return None
    items = mcp_papers.retrieve_all(query, k=5)
    if not items:
        return None
    used = {"doi": doi, "arxiv_id": arxiv_id, "title": title}
    return _pick_best_item(items, used)


def _register_mcp_items(
    items: Iterable[RetrievalItem],
    mcp_index: Dict[str, RetrievalItem],
    mcp_papers: Optional[MCPPaperRetriever] = None,
) -> None:
    for item in items or []:
        if not getattr(item, "cite_key", None):
            continue
        if _item_metadata_complete(item):
            mcp_index[str(item.cite_key)] = item
            continue
        resolved = _resolve_item_metadata(item, mcp_papers)
        if resolved is not None and _item_metadata_complete(resolved):
            mcp_index[str(resolved.cite_key)] = resolved


def _normalized_source_from_item(
    item: RetrievalItem, *, source_key: Optional[str] = None
) -> Dict[str, Any]:
    url = item.url or ""
    accessed = datetime.now(timezone.utc).date().isoformat() if url else None
    arxiv_id = _extract_arxiv_id_from_url(url) if url else ""
    return {
        "source_key": source_key or f"SRC:{item.cite_key}",
        "source_type": "tool",
        "title": item.title or "Untitled",
        "authors": item.authors or [],
        "year": item.year,
        "url": item.url,
        "doi": item.doi,
        "arxiv_id": arxiv_id or None,
        "accessed_date": accessed,
        "publisher": None,
        "venue": item.venue,
        "kb_source_path": None,
        "notes": None,
    }


def _merge_cited_mcp_sources(
    citations: set[str],
    mcp_index: Dict[str, RetrievalItem],
    sources: list[Dict[str, Any]],
    dedupe_map: Dict[str, Dict[str, Any]],
    key_map: Dict[str, str],
    next_id: int,
) -> Tuple[int, set[str]]:
    missing: set[str] = set()
    for key in citations:
        if key.startswith("SRC-"):
            if key not in key_map:
                missing.add(key)
            continue
        if not key.startswith("SRC:"):
            continue
        if key in key_map:
            continue
        raw_key = key.split(":", 1)[1]
        item = mcp_index.get(raw_key)
        if item is None:
            missing.add(key)
            continue
        normalized = _normalized_source_from_item(item)
        next_id = _merge_sources(sources, dedupe_map, key_map, next_id, normalized)
    return next_id, missing


def _source_metadata_complete(entry: Dict[str, Any]) -> bool:
    title = str(entry.get("title") or "").strip()
    authors = entry.get("authors") or []
    year = entry.get("year")
    doi = str(entry.get("doi") or "").strip()
    arxiv_id = str(entry.get("arxiv_id") or "").strip()
    if not title:
        return False
    if not authors:
        return False
    if year is None and not doi and not arxiv_id:
        return False
    return True


def _normalize_title(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", text or "")
    return cleaned.strip().lower()


def _pick_best_item(items: list[RetrievalItem], used: Dict[str, Any]) -> Optional[RetrievalItem]:
    doi = str(used.get("doi") or "").strip().lower()
    arxiv_id = str(used.get("arxiv_id") or "").strip().lower()
    title = _normalize_title(str(used.get("title") or ""))
    for item in items:
        if doi and item.doi and item.doi.strip().lower() == doi:
            return item
    if arxiv_id:
        for item in items:
            if item.url and arxiv_id in item.url:
                return item
    if title:
        for item in items:
            if _normalize_title(item.title or "") == title:
                return item
    return items[0] if items else None


def _resolve_used_source(
    used: Dict[str, Any],
    mcp_papers: Optional[MCPPaperRetriever],
    mcp_index: Dict[str, RetrievalItem],
) -> Optional[RetrievalItem]:
    if mcp_papers is None or not mcp_papers.is_available():
        return None
    additional = used.get("additional") or {}
    doi = str(used.get("doi") or additional.get("doi") or "").strip()
    arxiv_id = str(used.get("arxiv_id") or additional.get("arxiv_id") or "").strip()
    title = str(used.get("title") or "").strip()
    url_or_path = str(used.get("url_or_path") or used.get("url") or "").strip()
    if not arxiv_id and url_or_path:
        arxiv_id = _extract_arxiv_id_from_url(url_or_path)
    query = doi or arxiv_id or title or url_or_path
    if not query:
        return None
    items = mcp_papers.retrieve_all(query, k=5)
    item = _pick_best_item(items, used)
    if item is None:
        return None
    if item.cite_key:
        mcp_index[str(item.cite_key)] = item
    return item


def _seed_kb2_from_user_references(
    *,
    user_references: list[str],
    mcp_papers: Optional[MCPPaperRetriever],
    mcp_index: Dict[str, RetrievalItem],
    sources: list[Dict[str, Any]],
    dedupe_map: Dict[str, Dict[str, Any]],
    key_map: Dict[str, str],
    next_id: int,
    out_dir: Path,
    logger: Any,
) -> tuple[list[RetrievalItem], int]:
    if not user_references:
        return [], next_id
    if mcp_papers is None or not mcp_papers.is_available():
        _log_progress(
            logger,
            "[PROPOSAL] User references found, but MCP paper tools are unavailable for KB2 seeding.",
        )
        return [], next_id

    max_refs_raw = os.environ.get("AUTOGENBOOK_PROPOSAL_REF_MCP_MAX", "30").strip() or "30"
    try:
        max_refs = max(1, int(max_refs_raw))
    except Exception:
        max_refs = 30
    candidate_refs = user_references[:max_refs]

    resolved_items: list[RetrievalItem] = []
    seen_keys: set[str] = set()
    report_rows: list[Dict[str, Any]] = []

    for idx, ref_text in enumerate(candidate_refs, start=1):
        normalized_ref = _normalize_user_reference_entry(ref_text, idx)
        query = (
            str(normalized_ref.get("doi") or "").strip()
            or str(normalized_ref.get("arxiv_id") or "").strip()
            or str(normalized_ref.get("title") or "").strip()
            or str(normalized_ref.get("notes") or "").strip()
        )
        if not query:
            report_rows.append(
                {"index": idx, "reference": ref_text, "status": "skipped", "reason": "empty_query"}
            )
            continue
        items = mcp_papers.retrieve_all(query, k=8)
        best = _pick_best_item(items, normalized_ref)
        if best is None:
            report_rows.append(
                {"index": idx, "reference": ref_text, "status": "not_found", "query": query}
            )
            continue

        if best.cite_key:
            mcp_index[str(best.cite_key)] = best
        unique_key = str(best.cite_key or best.rid or "")
        if unique_key and unique_key not in seen_keys:
            resolved_items.append(best)
            seen_keys.add(unique_key)

        source_key = f"SRC:{best.cite_key}" if best.cite_key else None
        normalized_source = _normalized_source_from_item(best, source_key=source_key)
        if ref_text:
            note = str(normalized_source.get("notes") or "").strip()
            ref_note = f"Matched from proposal_input reference: {ref_text}"
            normalized_source["notes"] = (note + "\n" + ref_note).strip() if note else ref_note
        next_id = _merge_sources(sources, dedupe_map, key_map, next_id, normalized_source)
        report_rows.append(
            {
                "index": idx,
                "reference": ref_text,
                "status": "resolved",
                "query": query,
                "matched_title": best.title,
                "matched_cite_key": best.cite_key,
                "matched_url": best.url,
                "matched_doi": best.doi,
                "matched_year": best.year,
            }
        )

    try:
        (out_dir / "kb2_user_reference_seed.json").write_text(
            json.dumps(
                {
                    "input_reference_count": len(user_references),
                    "attempted_reference_count": len(candidate_refs),
                    "resolved_item_count": len(resolved_items),
                    "rows": report_rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    _log_progress(
        logger,
        f"[PROPOSAL] KB2 seeded from user references: resolved {len(resolved_items)}/{len(candidate_refs)}.",
    )
    return resolved_items, next_id


def _extract_arxiv_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"/(abs|pdf)/([^?#]+)", url)
    if not match:
        return ""
    cleaned = match.group(2).replace(".pdf", "").strip()
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", cleaned):
        return cleaned
    return ""


def _enrich_source_metadata(entry: Dict[str, Any], mcp_papers: Optional[MCPPaperRetriever]) -> bool:
    if mcp_papers is None or not mcp_papers.is_available():
        return False
    title = str(entry.get("title") or "").strip()
    doi = str(entry.get("doi") or "").strip()
    arxiv_id = str(entry.get("arxiv_id") or "").strip()
    url = str(entry.get("url") or "").strip()
    if not arxiv_id and url:
        arxiv_id = _extract_arxiv_id_from_url(url)
    query = doi or arxiv_id or title
    if not query:
        return False
    items = mcp_papers.retrieve_all(query, k=5)
    if not items:
        return False
    item = _pick_best_item(items, entry)
    if item is None:
        return False
    if not entry.get("title") and item.title:
        entry["title"] = item.title
    if not entry.get("authors") and item.authors:
        entry["authors"] = item.authors
    if entry.get("year") is None and item.year:
        entry["year"] = item.year
    if not entry.get("doi") and item.doi:
        entry["doi"] = item.doi
    if not entry.get("venue") and item.venue:
        entry["venue"] = item.venue
    if not entry.get("url") and item.url:
        entry["url"] = item.url
        entry["accessed_date"] = datetime.now(timezone.utc).date().isoformat()
    if not entry.get("arxiv_id"):
        entry["arxiv_id"] = _extract_arxiv_id_from_url(entry.get("url") or "")
    return True


def _rebuild_sources_index(
    sources: list[Dict[str, Any]],
) -> Tuple[list[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, str], int]:
    dedupe_map: Dict[str, Dict[str, Any]] = {}
    key_map: Dict[str, str] = {}
    next_id = 1
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        dedupe_map[_source_dedupe_key(entry)] = entry
        source_id = str(entry.get("source_id", ""))
        if source_id:
            key_map[source_id] = source_id
        for key in entry.get("source_keys", []) or []:
            key_map[str(key)] = source_id
        match = re.search(r"SRC-(\d+)$", source_id)
        if match:
            next_id = max(next_id, int(match.group(1)) + 1)
    return sources, dedupe_map, key_map, next_id


def _split_final_review_output(text: str) -> Tuple[str, str, Optional[str]]:
    if FINAL_REVIEW_DELIM not in text:
        return text.strip(), "", "missing_final_review_delimiter"
    before, after = text.split(FINAL_REVIEW_DELIM, 1)
    md = before.strip()
    report = after.strip()
    if not report:
        return md, report, "missing_final_review_report"
    return md, report, None


def _inject_cite_latex(markdown: str) -> str:
    pattern = re.compile(r"\[([^\]]+)\]")

    def _repl(match: re.Match) -> str:
        content = match.group(1)
        tokens = re.findall(r"SRC:[A-Za-z0-9._:-]+|SRC-\d+", content)
        if not tokens:
            return match.group(0)
        remainder = re.sub(r"SRC:[A-Za-z0-9._:-]+|SRC-\d+|[;,\s]+", "", content)
        if remainder.strip():
            return match.group(0)
        joined = ",".join(tokens)
        return f"\\cite{{{joined}}}"

    return pattern.sub(_repl, markdown)


def _build_audit_tex(markdown: str, out_dir: Path) -> Optional[Path]:
    if not shutil.which("pandoc"):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "proposal_audit.md"
    tex_path = out_dir / "proposal_audit.tex"
    md_text = _inject_cite_latex(markdown)
    md_path.write_text(md_text, encoding="utf-8")
    proc = subprocess.run(
        ["pandoc", str(md_path), "-o", str(tex_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return tex_path


def _normalize_language_tag(language: str) -> str:
    lowered = language.strip().lower()
    if lowered in {"cz", "cs", "czech", "cesky", "čeština", "ceska"}:
        return "czech"
    if lowered in {"de", "german", "deutsch"}:
        return "german"
    if lowered in {"en", "english"}:
        return "english"
    return lowered


def _is_supported_language(language: str) -> bool:
    normalized = _normalize_language_tag(language)
    return normalized in {"czech", "english", "german"}


def _language_matches(detected: str, expected: str) -> bool:
    if not expected:
        return True
    norm_expected = _normalize_language_tag(expected)
    norm_detected = _normalize_language_tag(detected)
    return norm_expected in norm_detected or norm_detected in norm_expected


def _normalize_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized.lower()


def _review_report_indicates_done(report: str) -> bool:
    if not report:
        return False
    normalized = _normalize_for_match(report)
    if "not compliant" in normalized or "non-compliant" in normalized:
        return False
    if "not compliant" in normalized or "non compliant" in normalized:
        return False
    if "nevyhov" in normalized or "nespln" in normalized:
        return False
    positives = [
        "compliant",
        "fully compliant",
        "no changes needed",
        "bez pripominek",
        "bez zmen",
        "splneno",
        "vyhovuje",
    ]
    return any(token in normalized for token in positives)


def _apply_bibliography(markdown: str, bibliography_md: str) -> str:
    pattern = re.compile(r"(?is)\n#{1,6}\s+(Použitá|Pouzita)\s+literatura\b.*$")
    match = pattern.search(markdown)
    bib = bibliography_md.strip()
    if match:
        prefix = markdown[: match.start()].rstrip()
        return (prefix + "\n\n" + bib + "\n").strip() + "\n"
    return (markdown.rstrip() + "\n\n" + bib + "\n").strip() + "\n"


def _strip_bibliography_section(markdown: str) -> str:
    pattern = re.compile(r"(?is)\n#{1,6}\s+(Použitá|Pouzita)\s+literatura\b.*$")
    match = pattern.search(markdown)
    if match:
        return markdown[: match.start()]
    return markdown


def _normalize_authors(authors: Any) -> list[str]:
    if isinstance(authors, list):
        return [str(a).strip() for a in authors if str(a).strip()]
    if authors is None:
        return []
    text = str(authors).strip()
    if not text:
        return []
    return [text]


def _normalize_used_source(used: Dict[str, Any]) -> Dict[str, Any]:
    source_key = str(used.get("source_key", "")).strip()
    source_type = str(used.get("type", "unknown")).strip().lower()
    title = str(used.get("title", "")).strip() or "Untitled"
    authors = _normalize_authors(used.get("authors"))
    year = used.get("year")
    year_val = None
    if isinstance(year, int):
        year_val = year
    elif isinstance(year, str) and year.strip().isdigit():
        year_val = int(year.strip())
    url_or_path = str(used.get("url_or_path", "")).strip()
    additional = used.get("additional") or {}
    doi = str(additional.get("doi") or "").strip() or None
    arxiv_id = str(additional.get("arxiv_id") or "").strip() or None
    accessed = str(additional.get("accessed") or "").strip() or None
    publisher = str(additional.get("publisher") or "").strip() or None
    venue = str(additional.get("venue") or "").strip() or None
    support_note = str(used.get("support_note") or "").strip() or None

    url = url_or_path if url_or_path.startswith("http") else None
    kb_path = url_or_path if url is None and url_or_path else None
    if url and not accessed:
        accessed = datetime.now(timezone.utc).date().isoformat()

    return {
        "source_key": source_key,
        "source_type": source_type,
        "title": title,
        "authors": authors,
        "year": year_val,
        "url": url,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "accessed_date": accessed,
        "publisher": publisher,
        "venue": venue,
        "kb_source_path": kb_path,
        "notes": support_note,
    }


def _source_dedupe_key(entry: Dict[str, Any]) -> str:
    doi = (entry.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    arxiv_id = (entry.get("arxiv_id") or "").strip().lower()
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    url = (entry.get("url") or "").strip().lower()
    if url:
        return f"url:{url}"
    path = (entry.get("kb_source_path") or "").strip().lower()
    if path:
        return f"path:{path}"
    title = (entry.get("title") or "").strip().lower()
    return f"title:{title}"


def _load_sources_store(path: Path) -> Tuple[list[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, str], int]:
    sources: list[Dict[str, Any]] = []
    dedupe_map: Dict[str, Dict[str, Any]] = {}
    key_map: Dict[str, str] = {}
    next_id = 1
    if not path.exists():
        return sources, dedupe_map, key_map, next_id
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return sources, dedupe_map, key_map, next_id
    for entry in data.get("sources", []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        sources.append(entry)
        dedupe_map[_source_dedupe_key(entry)] = entry
        for key in entry.get("source_keys", []) or []:
            key_map[str(key)] = str(entry.get("source_id", ""))
        source_id = str(entry.get("source_id", ""))
        if source_id:
            key_map[source_id] = source_id
        match = re.search(r"SRC-(\d+)$", source_id)
        if match:
            next_id = max(next_id, int(match.group(1)) + 1)
    return sources, dedupe_map, key_map, next_id


def _merge_source_entry(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    if incoming.get("source_key"):
        existing.setdefault("source_keys", [])
        if incoming["source_key"] not in existing["source_keys"]:
            existing["source_keys"].append(incoming["source_key"])
    for field in ("title", "year", "url", "doi", "arxiv_id", "accessed_date", "kb_source_path"):
        if not existing.get(field) and incoming.get(field):
            existing[field] = incoming[field]
    if incoming.get("source_type") and existing.get("source_type") in {None, "", "unknown"}:
        existing["source_type"] = incoming["source_type"]
    if incoming.get("authors"):
        existing.setdefault("authors", [])
        for author in incoming["authors"]:
            if author not in existing["authors"]:
                existing["authors"].append(author)
    if incoming.get("notes"):
        existing_notes = existing.get("notes") or ""
        notes = (existing_notes + "\n" + incoming["notes"]).strip()
        existing["notes"] = notes


def _merge_sources(
    sources: list[Dict[str, Any]],
    dedupe_map: Dict[str, Dict[str, Any]],
    key_map: Dict[str, str],
    next_id: int,
    incoming: Dict[str, Any],
) -> int:
    dedupe_key = _source_dedupe_key(incoming)
    existing = dedupe_map.get(dedupe_key)
    if existing is None:
        source_id = f"SRC-{next_id:04d}"
        entry = {
            "source_id": source_id,
            "source_keys": [incoming.get("source_key")] if incoming.get("source_key") else [],
            "source_type": incoming.get("source_type") or "unknown",
            "title": incoming.get("title") or "Untitled",
            "authors": incoming.get("authors") or [],
            "year": incoming.get("year"),
            "url": incoming.get("url"),
            "doi": incoming.get("doi"),
            "arxiv_id": incoming.get("arxiv_id"),
            "accessed_date": incoming.get("accessed_date"),
            "publisher": incoming.get("publisher"),
            "venue": incoming.get("venue"),
            "kb_source_path": incoming.get("kb_source_path"),
            "kb_loc": incoming.get("kb_loc"),
            "evidence_rids": incoming.get("evidence_rids", []),
            "notes": incoming.get("notes"),
        }
        sources.append(entry)
        dedupe_map[dedupe_key] = entry
        for key in entry.get("source_keys", []) or []:
            key_map[str(key)] = source_id
        key_map[source_id] = source_id
        return next_id + 1
    _merge_source_entry(existing, incoming)
    for key in existing.get("source_keys", []) or []:
        key_map[str(key)] = str(existing.get("source_id", ""))
    source_id = str(existing.get("source_id", ""))
    if source_id:
        key_map[source_id] = source_id
    return next_id


def _write_sources_store(path: Path, sources: list[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sources": sources}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _strip_missing_citations(markdown: str, missing: set[str], language: str) -> str:
    if not missing:
        return markdown
    cite_pattern = re.compile(r"\[([^\]]+)\]")

    def _repl(match: re.Match) -> str:
        content = match.group(1)
        tokens = re.findall(r"SRC:[A-Za-z0-9._:-]+|SRC-\d+", content)
        if not tokens:
            return match.group(0)
        remainder = re.sub(r"SRC:[A-Za-z0-9._:-]+|SRC-\d+|[;,\s]+", "", content)
        if remainder.strip():
            return match.group(0)
        keep = [token for token in tokens if token not in missing]
        if not keep:
            return ""
        return "[" + "; ".join(keep) + "]"

    markdown = cite_pattern.sub(_repl, markdown)
    note = "Note: Some claims could not be linked to verified sources and should be treated as background."
    lowered = language.lower()
    if "czech" in lowered or "češt" in lowered or lowered in {"cz", "cs"}:
        note = (
            "Poznámka: Některá tvrzení nelze doložit dostupnými zdroji; "
            "berte je jako obecné pozadí."
        )
    markdown = markdown.rstrip() + "\n\n" + note + "\n"
    return markdown


def _format_iso690_entry(entry: Dict[str, Any], language: str) -> str:
    authors = entry.get("authors") or []
    author_str = "; ".join(authors) if authors else "Unknown"
    title = entry.get("title") or "Untitled"
    year = entry.get("year")
    venue = entry.get("venue") or entry.get("publisher") or ""
    doi = entry.get("doi")
    url = entry.get("url") or entry.get("kb_source_path")
    accessed = entry.get("accessed_date")

    parts = [f"{author_str}. {title}."]
    if venue:
        parts.append(f"{venue}.")
    if year:
        parts.append(f"{year}.")
    if doi:
        parts.append(f"DOI: {doi}.")
    if url:
        lowered = language.lower()
        if "czech" in lowered or "češt" in lowered or lowered in {"cz", "cs"}:
            parts.append(f"Dostupné z: {url}.")
            if accessed:
                parts.append(f"[cit. {accessed}].")
        else:
            parts.append(f"Available at: {url}.")
            if accessed:
                parts.append(f"Accessed {accessed}.")
    return " ".join(parts).replace("  ", " ").strip()


def _write_bibliography(path: Path, sources: list[Dict[str, Any]], language: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["## Použitá literatura", ""]
    for idx, entry in enumerate(sources, start=1):
        source_id = entry.get("source_id") or f"SRC-{idx:04d}"
        lines.append(f"{idx}. [{source_id}] {_format_iso690_entry(entry, language)}")
    content = "\n".join(lines).strip() + "\n"
    path.write_text(content, encoding="utf-8")
    return content


def _convert_with_pandoc(
    md_path: Path,
    out_dir: Path,
    formats: list[str],
    basename: str = "proposal",
) -> None:
    if not shutil.which("pandoc"):
        print("[WARN] pandoc not available; skipping format conversions.")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    sanitize_text = None
    sanitize_issues: list[str] = []
    sanitize_path: Optional[Path] = None
    for fmt in formats:
        fmt_clean = fmt.lower().strip()
        if fmt_clean in {"pdf", "tex"}:
            try:
                raw_md = md_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                raw_md = ""
            sanitize_text, sanitize_issues = _sanitize_markdown_for_latex(raw_md)
            if sanitize_issues:
                sanitize_path = out_dir / f"{basename}_latex_sanitized.md"
                sanitize_path.write_text(sanitize_text, encoding="utf-8")
                print(
                    "[WARN] pandoc conversion: stripped unsupported symbols for LaTeX compatibility."
                )
            break
    for fmt in formats:
        fmt_clean = fmt.lower().strip()
        if fmt_clean in {"markdown", "md"}:
            continue
        out_path = out_dir / f"{basename}.{fmt_clean}"
        in_path = sanitize_path if sanitize_path and fmt_clean in {"pdf", "tex"} else md_path
        cmd = ["pandoc", str(in_path), "-o", str(out_path)]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            print(f"[WARN] pandoc conversion failed for {fmt_clean}: {stderr}")


def _sanitize_markdown_for_latex(text: str) -> tuple[str, list[str]]:
    if not text:
        return text, []
    issues: list[str] = []
    replacements = {
        "\u26a0": "(warning)",
        "\u2714": "[OK]",
        "\u2705": "[OK]",
        "\u274c": "[X]",
        "\u26a1": "(lightning)",
    }
    for src, dst in replacements.items():
        if src in text:
            text = text.replace(src, dst)
            issues.append(f"replaced:{src}")
    cleaned_chars: list[str] = []
    removed = 0
    for ch in text:
        if ch in {"\n", "\r", "\t"}:
            cleaned_chars.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in {"So", "Co", "Cs"}:
            removed += 1
            continue
        if cat == "Cc":
            removed += 1
            continue
        cleaned_chars.append(ch)
    if removed:
        issues.append("removed_symbols")
    return "".join(cleaned_chars), issues


def _generate_section_markdown(
    *,
    llm: OpenRouterLLM,
    system_prompt: str,
    user_prompt: str,
    language: str,
    allow_tools: bool = True,
    allowed_keys: Optional[set[str]] = None,
    allowed_ids: Optional[set[str]] = None,
    enforce_sources_match: bool = True,
    model: Optional[str] = None,
    max_attempts: int = 1,
    out_dir: Optional[Path] = None,
    label: str = "llm4",
    usage_extra: Optional[Dict[str, Any]] = None,
) -> Tuple[str, list[Dict[str, Any]], set[str]]:
    last_md = ""
    last_sources: list[Dict[str, Any]] = []
    last_missing: set[str] = set()
    last_error = ""

    for attempt in range(max_attempts + 1):
        prompt = user_prompt
        if attempt > 0:
            fixes = []
            if enforce_sources_match:
                fixes.append("Ensure every [SRC:...] citation key appears in SOURCES_JSON used_sources.")
            fixes.append("Include a valid SOURCES_JSON block after the delimiter.")
            if allowed_keys or allowed_ids:
                fixes.append(
                    "Use ONLY the allowed citation keys provided; remove or rewrite unsupported claims."
                )
            prompt += "\n\nFIX REQUIRED:\n" + "\n".join(f"- {line}" for line in fixes) + "\n"
            if last_error:
                prompt += f"- Previous issue: {last_error}\n"
        content = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            allow_tools=allow_tools,
            model=model,
        )
        if out_dir:
            extra = dict(usage_extra or {})
            extra["attempt"] = attempt
            _log_llm_usage(out_dir, llm, label, extra=extra)
        md, sources_obj, err = _split_sources_block(content)
        last_md = md
        if err:
            last_error = err
            if attempt >= max_attempts:
                # Fall back to markdown-only output; downstream will validate citations via MCP.
                return (md or content.strip()), [], set()
            continue
        used_sources = sources_obj.get("used_sources") if isinstance(sources_obj, dict) else None
        if not isinstance(used_sources, list):
            last_error = "missing_used_sources"
            continue
        normalized_sources = []
        source_keys = set()
        for entry in used_sources:
            if not isinstance(entry, dict):
                continue
            normalized = _normalize_used_source(entry)
            if normalized.get("source_key"):
                source_keys.add(normalized["source_key"])
            normalized_sources.append(normalized)
        citations = _extract_citation_keys(md)
        missing: set[str] = set()
        invalid: set[str] = set()
        allowed_keys = allowed_keys or set()
        allowed_ids = allowed_ids or set()
        enforce_allowed = bool(allowed_keys) or bool(allowed_ids)
        if enforce_sources_match:
            missing |= citations - source_keys
        if enforce_allowed:
            for key in citations:
                if key.startswith("SRC-"):
                    if key not in allowed_ids:
                        invalid.add(key)
                elif key.startswith("SRC:"):
                    if key not in allowed_keys:
                        invalid.add(key)
            for key in source_keys:
                if key.startswith("SRC-") and key not in allowed_ids:
                    invalid.add(key)
                if key.startswith("SRC:") and allowed_keys and key not in allowed_keys:
                    invalid.add(key)
        if invalid:
            missing |= invalid
        last_sources = normalized_sources
        last_missing = missing
        if missing:
            last_error = "missing_source_keys: " + ", ".join(sorted(missing))
            continue
        return md, normalized_sources, missing
    raise RuntimeError(
        "Missing citations after forced rewrite: " + ", ".join(sorted(last_missing))
    )


def run_proposal(args: Any, run_ctx: Optional[RunContext], logger: Any) -> int:
    started_at = datetime.now(timezone.utc)
    status = "ok"
    error: Optional[str] = None
    llm_default: Optional[OpenRouterLLM] = None
    llm1: Optional[OpenRouterLLM] = None
    llm2: Optional[OpenRouterLLM] = None
    llm3: Optional[OpenRouterLLM] = None
    llm4: Optional[OpenRouterLLM] = None
    llm5: Optional[OpenRouterLLM] = None
    proposal_models: Dict[str, Optional[str]] = {}

    try:
        out_dir = Path(args.out_dir).expanduser().resolve()
        if run_ctx is None:
            run_ctx = RunContext.create(out_dir=out_dir, mode="proposal")

        overall_start = time.time()
        out_dir.mkdir(parents=True, exist_ok=True)
        outline_dir = out_dir / "outline"
        outline_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "sections").mkdir(parents=True, exist_ok=True)
        (out_dir / "agent_logs").mkdir(parents=True, exist_ok=True)

        resume_sections = bool(getattr(args, "resume", False))
        proposal_input_source = Path(getattr(args, "proposal_input", "") or "proposal_input.txt")
        proposal_input_source = proposal_input_source.expanduser().resolve()
        if not proposal_input_source.exists():
            print(f"Error: proposal input not found: {proposal_input_source}", file=sys.stderr)
            return 2
        source_text = _read_text(proposal_input_source)
        sanitized_source_text = _strip_user_inputs_block(source_text).rstrip() + "\n"
        proposal_input = out_dir / "proposal_input.runtime.txt"
        proposal_input.write_text(sanitized_source_text, encoding="utf-8")
        (out_dir / "proposal_input.original.txt").write_text(source_text, encoding="utf-8")
        if source_text != sanitized_source_text:
            _log_progress(
                logger,
                "[PROPOSAL] Removed existing PROPOSAL_USER_INPUTS block from runtime copy to avoid stale context.",
            )

        kb1_dir = _require_dir(
            Path(getattr(args, "kb1_dir", "")).expanduser().resolve()
            if getattr(args, "kb1_dir", None)
            else None,
            "--kb1-dir",
        )
        if kb1_dir is None:
            return 2

        kb2_dir = _require_dir(
            Path(getattr(args, "kb2_dir", "")).expanduser().resolve()
            if getattr(args, "kb2_dir", None)
            else None,
            "--kb2-dir",
        )
        if kb2_dir is None:
            return 2

        prompts = load_proposal_prompts()
        proposal_text = _read_text(proposal_input)
        proposal_user_refs = _extract_user_reference_entries(_read_text(proposal_input_source))
        if proposal_user_refs:
            _log_progress(
                logger,
                f"[PROPOSAL] Detected {len(proposal_user_refs)} user-provided references in proposal_input.",
            )

        model_llm1 = _resolve_model_override(args, "proposal_llm1_model", "AUTOGENBOOK_PROPOSAL_LLM1_MODEL")
        model_llm2 = _resolve_model_override(args, "proposal_llm2_model", "AUTOGENBOOK_PROPOSAL_LLM2_MODEL")
        model_llm3 = _resolve_model_override(args, "proposal_llm3_model", "AUTOGENBOOK_PROPOSAL_LLM3_MODEL")
        model_llm4 = _resolve_model_override(args, "proposal_llm4_model", "AUTOGENBOOK_PROPOSAL_LLM4_MODEL")
        model_llm5 = _resolve_model_override(args, "proposal_llm5_model", "AUTOGENBOOK_PROPOSAL_LLM5_MODEL")
        base_llm1 = _resolve_value_override(args, "proposal_llm1_base_url", "AUTOGENBOOK_PROPOSAL_LLM1_BASE_URL")
        base_llm2 = _resolve_value_override(args, "proposal_llm2_base_url", "AUTOGENBOOK_PROPOSAL_LLM2_BASE_URL")
        base_llm3 = _resolve_value_override(args, "proposal_llm3_base_url", "AUTOGENBOOK_PROPOSAL_LLM3_BASE_URL")
        base_llm4 = _resolve_value_override(args, "proposal_llm4_base_url", "AUTOGENBOOK_PROPOSAL_LLM4_BASE_URL")
        base_llm5 = _resolve_value_override(args, "proposal_llm5_base_url", "AUTOGENBOOK_PROPOSAL_LLM5_BASE_URL")

        llm_default = OpenRouterLLM()

        def _build_role_llm(base_url: Optional[str], model_override: Optional[str]) -> OpenRouterLLM:
            if base_url:
                base = LLMConfig()
                return OpenRouterLLM(
                    LLMConfig(
                        model=model_override or base.model,
                        temperature=base.temperature,
                        max_tokens=base.max_tokens,
                        input_cost_per_million=base.input_cost_per_million,
                        output_cost_per_million=base.output_cost_per_million,
                        base_url=base_url,
                    )
                )
            return llm_default

        llm1 = _build_role_llm(base_llm1, model_llm1)
        llm2 = _build_role_llm(base_llm2, model_llm2)
        llm3 = _build_role_llm(base_llm3, model_llm3)
        llm4 = _build_role_llm(base_llm4, model_llm4)
        llm5 = _build_role_llm(base_llm5, model_llm5)
        metadata_cache: Dict[str, Dict[str, Any]] = {}

        def _metadata_for_text(text: str) -> Dict[str, Any]:
            digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
            cached = metadata_cache.get(digest)
            if cached is not None:
                return cached
            try:
                metadata = _extract_metadata_with_llm(
                    llm1,
                    text,
                    system_prompt=prompts.get("metadata_extractor_system", ""),
                    model=model_llm1,
                    out_dir=out_dir,
                )
            except Exception:
                metadata = {}
            metadata_cache[digest] = metadata
            return metadata

        proposal_metadata = _metadata_for_text(proposal_text)
        if proposal_metadata:
            (out_dir / "proposal_metadata.json").write_text(
                json.dumps(proposal_metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        explicit_language = proposal_metadata.get("language") or _extract_explicit_language(proposal_text)
        if not explicit_language:
            print(
                "Error: proposal_input.txt must include an explicit language line (e.g., 'Language:' or 'Jazyk:').",
                file=sys.stderr,
            )
            return 2
        if not _is_supported_language(explicit_language):
            print(
                f"Error: Unsupported language tag '{explicit_language}'. "
                "Use Czech/English/German (e.g., Language: cs/en/de).",
                file=sys.stderr,
            )
            return 2
        language = explicit_language
        language_explicit = True
        prompts = _apply_language(prompts, language)
        set_prompt_registry("proposal", prompts)

        if logger is not None:
            logger.info("Loaded proposal prompt pack: %s", ", ".join(sorted(prompts.keys())))
        else:
            print("[INFO] Loaded proposal prompt pack.")
        proposal_models = {
            "llm1": model_llm1,
            "llm2": model_llm2,
            "llm3": model_llm3,
            "llm4": model_llm4,
            "llm5": model_llm5,
        }

        _log_progress(logger, "[PROPOSAL] Building KB1/KB2 (local RAG index)...")
        kb_cache = out_dir / ".kb_cache"
        kb1 = KnowledgeBase.build_from_directory(
            kb1_dir,
            cache_dir=kb_cache / "kb1",
            force_rebuild=bool(getattr(args, "rebuild_kb", False)),
        )
        kb2 = KnowledgeBase.build_from_directory(
            kb2_dir,
            cache_dir=kb_cache / "kb2",
            force_rebuild=bool(getattr(args, "rebuild_kb", False)),
        )
        _log_progress(logger, "[PROPOSAL] KB build complete.")

        (out_dir / "kb1_sources.json").write_text(
            json.dumps(build_kb_index(kb1), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "kb2_sources.json").write_text(
            json.dumps(build_kb_index(kb2), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        enable_web = bool(getattr(args, "enable_web_rag", False))
        if not enable_web:
            raise RuntimeError(
                "Proposal mode requires MCP paper tools for citations. "
                "Re-run with --enable-web-rag and a configured MCP gateway."
            )
        dont_ask = bool(getattr(args, "dont_ask", False))
        if dont_ask:
            _log_progress(logger, "[PROPOSAL] User prompting disabled (--dont-ask).")
        web_k = int(getattr(args, "web_rag_k", 5) or 5)
        tavily = None
        mcp_papers = None
        if enable_web:
            mcp_papers = MCPPaperRetriever()
            if not mcp_papers.is_available():
                raise RuntimeError("MCP paper tools unavailable; cannot proceed with proposal mode.")

        kb1_retrieval = RetrievalManager(
            local_kb=kb1,
            mcp_papers=mcp_papers,
            tavily=tavily,
            enable_web=False,
            default_k=6,
            max_chars_total=6000,
        )
        kb2_retrieval = RetrievalManager(
            local_kb=kb2,
            mcp_papers=mcp_papers,
            tavily=tavily,
            enable_web=False,
            default_k=6,
            max_chars_total=6000,
        )
        web_retrieval = None
        if enable_web:
            web_retrieval = RetrievalManager(
                local_kb=None,
                mcp_papers=mcp_papers,
                tavily=None,
                enable_web=True,
                default_k=web_k,
                max_chars_total=6000,
            )

        mcp_index: Dict[str, RetrievalItem] = {}
        kb2_seed_items: list[RetrievalItem] = []
        seed_sources_dir = out_dir / "sources"
        seed_sources_dir.mkdir(parents=True, exist_ok=True)
        seed_sources_path = seed_sources_dir / "sources.json"
        seed_sources, seed_dedupe, seed_key_map, seed_next_id = _load_sources_store(seed_sources_path)
        seed_sources = [s for s in seed_sources if s.get("source_type") in {"tool", "user"}]
        seed_sources, seed_dedupe, seed_key_map, seed_next_id = _rebuild_sources_index(seed_sources)
        if proposal_user_refs:
            kb2_seed_items, seed_next_id = _seed_kb2_from_user_references(
                user_references=proposal_user_refs,
                mcp_papers=mcp_papers,
                mcp_index=mcp_index,
                sources=seed_sources,
                dedupe_map=seed_dedupe,
                key_map=seed_key_map,
                next_id=seed_next_id,
                out_dir=out_dir,
                logger=logger,
            )
            if kb2_seed_items:
                _write_sources_store(seed_sources_path, seed_sources)

        max_iters = int(getattr(args, "max_iters", 3) or 3)
        section_retries = int(getattr(args, "section_retries", 3) or 3)
        min_section_citations = int(getattr(args, "min_section_citations", 1) or 0)
        prev_raw = os.environ.get("AUTOGENBOOK_PROPOSAL_PREV_SECTIONS", "1") or "1"
        try:
            prev_sections_limit = max(0, int(prev_raw))
        except Exception:
            prev_sections_limit = 1

        llm1_feedback = ""
        llm2_feedback = ""
        extra_retrieval_notes = ""
        final_outline: Optional[Dict[str, Any]] = None
        best_outline: Optional[Dict[str, Any]] = None
        best_outline_score = -10**9
        best_outline_iter: Optional[int] = None
        last_llm1_output: Optional[Dict[str, Any]] = None
        llm1_max_rounds = _parse_int_env("AUTOGENBOOK_LLM1_MAX_ROUNDS", 2)

        _log_progress(
            logger,
            f"[PROPOSAL] Phase 1/4: Outline loop (max {max_iters} iterations).",
        )
        outline_times: list[float] = []
        for iter_idx in range(max_iters):
            iter_start = time.time()
            proposal_text = _read_text(proposal_input)
            proposal_metadata = _metadata_for_text(proposal_text)
            user_page_limit = proposal_metadata.get("page_limit") or _extract_page_limit_from_text(
                proposal_text
            )
            answered_ids, refused_ids = _parse_user_inputs(proposal_text)
            iter_dir = outline_dir / f"iter_{iter_idx}"
            iter_dir.mkdir(parents=True, exist_ok=True)
            _log_progress(
                logger,
                f"[PROPOSAL] Outline iteration {iter_idx + 1}/{max_iters}...",
            )

            kb1_context = kb1.format_context(proposal_text, k=6, max_chars_total=6000)
            llm1_user_prompt = (
                "PROPOSAL_INPUT:\n"
                f"{proposal_text}\n\n"
                f"USER_PAGE_LIMIT: {user_page_limit if user_page_limit else '(not provided)'}\n\n"
                "KB1_CONTEXT:\n"
                f"{_format_kb_context('KB1', kb1_context)}\n\n"
            )
            if llm1_feedback:
                llm1_user_prompt += f"FEEDBACK_FROM_OPPONENT:\n{llm1_feedback}\n\n"
            if extra_retrieval_notes:
                llm1_user_prompt += f"EXTRA_RETRIEVAL_NOTES:\n{extra_retrieval_notes}\n\n"

            llm1_round = 0
            llm1_output: Dict[str, Any]
            while True:
                llm1_output = _chat_json_with_validation(
                    llm=llm1,
                    system_prompt=prompts["llm1_architect_system"],
                    user_prompt=llm1_user_prompt,
                    model_cls=ProposalLLM1Output,
                    model=model_llm1,
                    normalize_fn=lambda out: _normalize_llm1_output(
                        out,
                        language_default=language,
                        user_page_limit=user_page_limit,
                    ),
                    out_dir=out_dir,
                    label="llm1",
                    usage_extra={"stage": "outline", "iter": iter_idx, "round": llm1_round},
                )
                last_llm1_output = llm1_output
                (iter_dir / "llm1.json").write_text(
                    json.dumps(llm1_output, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if llm1_round:
                    (iter_dir / f"llm1_round_{llm1_round}.json").write_text(
                        json.dumps(llm1_output, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                llm1_unknowns = llm1_output.get("unknown_or_ambiguous", []) or []
                if not llm1_unknowns:
                    break
                used_ids = set(answered_ids) | set(refused_ids)
                llm1_records = []
                new_lines = []
                answered_any = False
                noninteractive = dont_ask or _is_noninteractive()
                llm1_auto_notes: list[str] = []
                auto_notes_added = False
                for item in llm1_unknowns:
                    if not isinstance(item, dict):
                        continue
                    topic = str(item.get("topic") or "").strip()
                    if not topic:
                        continue
                    qid = _alloc_llm1_question_id(topic, used_ids)
                    if qid in answered_ids or qid in refused_ids:
                        continue
                    used_ids.add(qid)
                    why = str(item.get("why_unknown") or "").strip()
                    hint = str(item.get("what_to_look_for_in_kb1") or "").strip()
                    if noninteractive:
                        query = " ".join([topic, why, hint]).strip()
                        kb1_items = kb1_retrieval.retrieve(query, k=4, allow_web=False)
                        web_items = []
                        if web_retrieval is not None:
                            web_items = web_retrieval.retrieve(query, k=web_k, allow_web=True)
                        if web_items:
                            _register_mcp_items(web_items, mcp_index, mcp_papers)
                        kb1_ctx = kb1_retrieval.format_context(kb1_items) if kb1_items else ""
                        web_ctx = ""
                        if web_retrieval is not None and web_items:
                            web_ctx = web_retrieval.format_context(web_items)
                        if kb1_ctx or web_ctx:
                            note = (
                                f"LLM1 auto-retrieval for {qid} ({topic}):\n"
                                f"{_format_kb_context('KB1', kb1_ctx)}\n\n"
                            )
                            if web_ctx:
                                note += f"{_format_kb_context('WEB', web_ctx)}\n\n"
                            llm1_auto_notes.append(note)
                            auto_notes_added = True
                        refusal_text = f"AUTO_SKIPPED: {topic}"
                        new_lines.append(f"{qid}_REFUSAL: {refusal_text}")
                        refused_ids.add(qid)
                        llm1_records.append(
                            {
                                "round": llm1_round,
                                "id": qid,
                                "topic": topic,
                                "status": "auto_skipped",
                                "reason": why or None,
                                "kb1_hint": hint or None,
                            }
                        )
                        continue
                    print(f"\n[LLM1 QUESTION {qid}] {topic}")
                    if why:
                        print(f"Reason: {why}")
                    if hint:
                        print(f"KB1 hint: {hint}")
                    if noninteractive:
                        answer = ""
                    else:
                        answer = input("Answer (leave blank to refuse): ").strip()
                    if not answer or answer.lower() in {"refuse", "skip", "n/a"}:
                        refusal_text = f"REFUSED: {topic}"
                        if why:
                            refusal_text += f" | {why}"
                        new_lines.append(f"{qid}_REFUSAL: {refusal_text}")
                        refused_ids.add(qid)
                        llm1_records.append(
                            {
                                "round": llm1_round,
                                "id": qid,
                                "topic": topic,
                                "status": "refused",
                                "reason": why or None,
                                "kb1_hint": hint or None,
                            }
                        )
                    else:
                        new_lines.append(f"{qid}: {topic} -> {answer}")
                        answered_ids.add(qid)
                        answered_any = True
                        llm1_records.append(
                            {
                                "round": llm1_round,
                                "id": qid,
                                "topic": topic,
                                "status": "answered",
                                "answer": answer,
                                "reason": why or None,
                                "kb1_hint": hint or None,
                            }
                        )
                if llm1_records:
                    log_path = iter_dir / "llm1_missing_info.jsonl"
                    with log_path.open("a", encoding="utf-8") as handle:
                        for record in llm1_records:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    (iter_dir / "llm1_missing_info.json").write_text(
                        json.dumps(llm1_records, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                if llm1_auto_notes:
                    extra_retrieval_notes = "\n".join(
                        [s for s in [extra_retrieval_notes, "\n".join(llm1_auto_notes)] if s]
                    ).strip()
                if new_lines:
                    _append_user_inputs(proposal_input, new_lines)
                    proposal_text = _read_text(proposal_input)
                    proposal_metadata = _metadata_for_text(proposal_text)
                    user_page_limit = proposal_metadata.get("page_limit") or _extract_page_limit_from_text(
                        proposal_text
                    )
                    kb1_context = kb1.format_context(proposal_text, k=6, max_chars_total=6000)
                    llm1_user_prompt = (
                        "PROPOSAL_INPUT:\n"
                        f"{proposal_text}\n\n"
                        f"USER_PAGE_LIMIT: {user_page_limit if user_page_limit else '(not provided)'}\n\n"
                        "KB1_CONTEXT:\n"
                        f"{_format_kb_context('KB1', kb1_context)}\n\n"
                    )
                    if llm1_feedback:
                        llm1_user_prompt += f"FEEDBACK_FROM_OPPONENT:\n{llm1_feedback}\n\n"
                    if extra_retrieval_notes:
                        llm1_user_prompt += f"EXTRA_RETRIEVAL_NOTES:\n{extra_retrieval_notes}\n\n"
                if (answered_any or auto_notes_added) and llm1_round + 1 < llm1_max_rounds:
                    llm1_round += 1
                    continue
                break

            kb2_context = kb2.format_context(proposal_text, k=6, max_chars_total=6000)
            kb2_seed_for_outline = _select_seed_items_for_query(
                proposal_text,
                kb2_seed_items,
                k=min(8, max(0, len(kb2_seed_items))),
            )
            kb2_seed_context = (
                kb2_retrieval.format_context(kb2_seed_for_outline, max_chars_total=3000)
                if kb2_seed_for_outline
                else ""
            )
            if kb2_seed_context:
                kb2_context = "\n\n".join([s for s in [kb2_context, kb2_seed_context] if s])
            llm2_system_prompt = (
                prompts["llm2_researcher_outline_system"]
                + "\n\n"
                + str(llm1_output.get("meta_prompt_for_llm2", ""))
                + "\n\n"
                + "STRICT_SCHEMA_GUARD:\n"
                  "Return only the root keys: language, project_metadata, outline, annexes, "
                  "open_questions_for_opponent. Do not add any other top-level keys."
            ).strip()
            llm2_user_prompt = (
                "PROPOSAL_INPUT:\n"
                f"{proposal_text}\n\n"
                "LLM1_OUTPUT_JSON:\n"
                f"{json.dumps(llm1_output, ensure_ascii=False, indent=2)}\n\n"
                "KB_CONTEXT:\n"
                f"{_format_kb_context('KB1', kb1_context)}\n\n"
                f"{_format_kb_context('KB2', kb2_context)}\n\n"
            )
            if llm2_feedback:
                llm2_user_prompt += (
                    "FEEDBACK_FROM_OPPONENT (apply only if compatible with required JSON schema):\n"
                    f"{llm2_feedback}\n\n"
                )
            if extra_retrieval_notes:
                llm2_user_prompt += f"EXTRA_RETRIEVAL_NOTES:\n{extra_retrieval_notes}\n\n"

            llm2_output = _chat_json_with_validation(
                llm=llm2,
                system_prompt=llm2_system_prompt,
                user_prompt=llm2_user_prompt,
                model_cls=ProposalOutlineOutput,
                model=model_llm2,
                normalize_fn=lambda out: _normalize_outline_output(out, language_default=language),
                out_dir=out_dir,
                label="llm2",
                usage_extra={"stage": "outline", "iter": iter_idx},
            )
            (iter_dir / "llm2_outline.json").write_text(
                json.dumps(llm2_output, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            outline_score, score_meta = _outline_quality_score(llm2_output)
            (iter_dir / "llm2_outline_quality.json").write_text(
                json.dumps(
                    {
                        "score": outline_score,
                        "metrics": score_meta,
                        "is_selected": outline_score >= best_outline_score,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if outline_score >= best_outline_score:
                best_outline = llm2_output
                best_outline_score = outline_score
                best_outline_iter = iter_idx
                final_outline = llm2_output
                _log_progress(
                    logger,
                    f"[PROPOSAL] Outline candidate accepted from iteration {iter_idx + 1} "
                    f"(score={outline_score}, sections={score_meta.get('total_sections', 0)}).",
                )
            else:
                _log_progress(
                    logger,
                    f"[PROPOSAL] Outline candidate from iteration {iter_idx + 1} rejected "
                    f"(score={outline_score} < best={best_outline_score}).",
                )

            llm3_user_prompt = (
                "PROPOSAL_INPUT:\n"
                f"{proposal_text}\n\n"
                "LLM1_OUTPUT_JSON:\n"
                f"{json.dumps(llm1_output, ensure_ascii=False, indent=2)}\n\n"
                "LLM2_OUTPUT_JSON:\n"
                f"{json.dumps(llm2_output, ensure_ascii=False, indent=2)}\n\n"
                "KB_CONTEXT:\n"
                f"{_format_kb_context('KB1', kb1_context)}\n\n"
                f"{_format_kb_context('KB2', kb2_context)}\n\n"
            )

            llm3_output = _chat_json_with_validation(
                llm=llm3,
                system_prompt=prompts["llm3_opponent_outline_system"],
                user_prompt=llm3_user_prompt,
                model_cls=ProposalReviewOutput,
                model=model_llm3,
                normalize_fn=lambda out: _normalize_review_output(out, language_default=language),
                out_dir=out_dir,
                label="llm3",
                usage_extra={"stage": "outline_review", "iter": iter_idx},
            )
            (iter_dir / "llm3_review.json").write_text(
                json.dumps(llm3_output, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            llm1_feedback = "\n".join(llm3_output.get("instructions_to_llm1", []) or [])
            llm2_feedback = "\n".join(llm3_output.get("instructions_to_llm2", []) or [])

            extra_retrieval_notes = ""
            pending_questions = []
            question_retrievals = []
            for q in llm3_output.get("user_questions", []) or []:
                qid = str(q.get("id", "")).strip().upper()
                if not qid:
                    continue
                if qid in answered_ids or qid in refused_ids:
                    continue
                query = f"{q.get('question','')}\n{q.get('why_needed','')}"
                kb1_items = kb1_retrieval.retrieve(query, k=6, allow_web=False)
                kb2_items = kb2_retrieval.retrieve(query, k=6, allow_web=False)
                web_items = []
                if web_retrieval is not None:
                    web_items = web_retrieval.retrieve(query, k=web_k, allow_web=True)
                if web_items:
                    _register_mcp_items(web_items, mcp_index, mcp_papers)
                kb1_ctx = kb1_retrieval.format_context(kb1_items) if kb1_items else ""
                kb2_ctx = kb2_retrieval.format_context(kb2_items) if kb2_items else ""
                web_ctx = ""
                if web_retrieval is not None and web_items:
                    web_ctx = web_retrieval.format_context(web_items)
                if kb1_ctx or kb2_ctx or web_ctx:
                    note = (
                        f"Question {qid} retrieval context:\n"
                        f"{_format_kb_context('KB1', kb1_ctx)}\n\n"
                        f"{_format_kb_context('KB2', kb2_ctx)}\n\n"
                    )
                    if web_ctx:
                        note += f"{_format_kb_context('WEB', web_ctx)}\n\n"
                    question_retrievals.append({"id": qid, "context": note})
                    continue
                pending_questions.append(q)

            if question_retrievals:
                extra_retrieval_notes = "\n".join([entry["context"] for entry in question_retrievals])
                (iter_dir / "question_retrieval.json").write_text(
                    json.dumps(question_retrievals, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            if pending_questions:
                new_lines = []
                llm3_records = []
                noninteractive = dont_ask or _is_noninteractive()
                for q in pending_questions:
                    qid = str(q.get("id", "")).strip().upper()
                    if not qid:
                        continue
                    question = str(q.get("question", ""))
                    why = str(q.get("why_needed", ""))
                    print(f"\n[QUESTION {qid}] {question}")
                    if why:
                        print(f"Reason: {why}")
                    if noninteractive:
                        answer = ""
                    else:
                        answer = input("Answer (leave blank to refuse): ").strip()
                    if not answer or answer.lower() in {"refuse", "skip", "n/a"}:
                        refusal_text = str(q.get("if_user_refuses_then_write", "REFUSED"))
                        new_lines.append(f"{qid}_REFUSAL: {refusal_text}")
                        refused_ids.add(qid)
                        llm3_records.append(
                            {
                                "id": qid,
                                "question": question,
                                "status": "refused",
                                "reason": why or None,
                                "refusal_text": refusal_text,
                            }
                        )
                    else:
                        new_lines.append(f"{qid}: {answer}")
                        answered_ids.add(qid)
                        llm3_records.append(
                            {
                                "id": qid,
                                "question": question,
                                "status": "answered",
                                "answer": answer,
                                "reason": why or None,
                            }
                        )
                if new_lines:
                    _append_user_inputs(proposal_input, new_lines)
                    proposal_text = _read_text(proposal_input)
                if llm3_records:
                    log_path = iter_dir / "llm3_user_inputs.jsonl"
                    with log_path.open("a", encoding="utf-8") as handle:
                        for record in llm3_records:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    (iter_dir / "llm3_user_inputs.json").write_text(
                        json.dumps(llm3_records, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

            compliance = False
            compliance_assessment = llm3_output.get("compliance_assessment", {}) or {}
            if isinstance(compliance_assessment, dict):
                compliance = bool(compliance_assessment.get("is_compliant", False))

            user_questions = llm3_output.get("user_questions", []) or []
            pending_after = [
                q for q in user_questions
                if str(q.get("id", "")).strip().upper() not in answered_ids
                and str(q.get("id", "")).strip().upper() not in refused_ids
            ]
            all_refused = bool(user_questions) and all(
                str(q.get("id", "")).strip().upper() in refused_ids for q in user_questions
            )

            iter_elapsed = time.time() - iter_start
            outline_times.append(iter_elapsed)
            avg_iter = sum(outline_times) / len(outline_times) if outline_times else 0.0
            remaining_iters = max(0, max_iters - (iter_idx + 1))
            eta = remaining_iters * avg_iter
            _log_progress(
                logger,
                f"[PROPOSAL] Outline iter {iter_idx + 1} done. "
                f"Elapsed {_format_duration(iter_elapsed)}, ETA {_format_duration(eta)}.",
            )

            if compliance and not pending_after:
                break
            if all_refused:
                break
            if iter_idx >= max_iters - 1:
                break

        if best_outline is not None:
            final_outline = best_outline
        if final_outline is not None:
            _log_progress(logger, "[PROPOSAL] Outline finalized.")
            if best_outline_iter is not None:
                _log_progress(
                    logger,
                    f"[PROPOSAL] Using outline from iteration {best_outline_iter + 1} "
                    f"(score={best_outline_score}).",
                )
            (outline_dir / "final_outline.json").write_text(
                json.dumps(final_outline, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not _is_noninteractive():
                print("\n[PROPOSAL] Outline is finalized.")
                confirmation = input(
                    "Continue with full proposal generation (draft + final review)? [y/N]: "
                ).strip().lower()
                if confirmation not in {"y", "yes", "a", "ano"}:
                    _log_progress(
                        logger,
                        "[PROPOSAL] Stopped after outline phase by user confirmation prompt.",
                    )
                    return 0
            else:
                _log_progress(
                    logger,
                    "[PROPOSAL] Non-interactive mode detected; continuing automatically after outline.",
                )

        if final_outline is None:
            raise RuntimeError("Final outline missing; cannot proceed to drafting.")

        proposal_metadata = _metadata_for_text(_read_text(proposal_input))
        draft_dir = out_dir / "draft"
        sources_dir = out_dir / "sources"
        final_dir = out_dir / "final"
        draft_dir.mkdir(parents=True, exist_ok=True)
        sources_dir.mkdir(parents=True, exist_ok=True)
        final_dir.mkdir(parents=True, exist_ok=True)

        draft_path = draft_dir / "proposal_draft.md"
        if draft_path.exists():
            draft_path.unlink()

        sources_path = sources_dir / "sources.json"
        sources, dedupe_map, key_map, next_id = _load_sources_store(sources_path)
        sources = [s for s in sources if s.get("source_type") in {"tool", "user"}]
        sources, dedupe_map, key_map, next_id = _rebuild_sources_index(sources)
        if proposal_user_refs:
            for ref_idx, ref_entry in enumerate(proposal_user_refs, start=1):
                normalized_ref = _normalize_user_reference_entry(ref_entry, ref_idx)
                next_id = _merge_sources(sources, dedupe_map, key_map, next_id, normalized_ref)
            _write_sources_store(sources_path, sources)
            sources, dedupe_map, key_map, next_id = _rebuild_sources_index(sources)
            _log_progress(
                logger,
                f"[PROPOSAL] Added {len(proposal_user_refs)} references from proposal_input to bibliography store.",
            )

        project_meta = final_outline.get("project_metadata", {}) if isinstance(final_outline, dict) else {}
        header_lines = []
        if isinstance(project_meta, dict):
            title = str(project_meta.get("title", "")).strip()
            author = str(project_meta.get("author", "")).strip()
            if not title and proposal_metadata.get("title"):
                title = str(proposal_metadata.get("title") or "").strip()
            if not author and proposal_metadata.get("author"):
                author = str(proposal_metadata.get("author") or "").strip()
            if title:
                header_lines.append(f"# {title}")
            if author:
                header_lines.append(f"**Author:** {author}")
        if header_lines:
            draft_path.write_text("\n\n".join(header_lines).strip() + "\n\n", encoding="utf-8")
        else:
            draft_path.write_text("", encoding="utf-8")

        _log_progress(logger, "[PROPOSAL] Phase 2/4: Drafting sections...")
        outline_sections = final_outline.get("outline", []) if isinstance(final_outline, dict) else []
        leaf_sections: list[Tuple[Dict[str, Any], int, Tuple[str, ...]]] = []
        for sec, depth, path_tuple in _iter_outline_sections(outline_sections, depth=2):
            if not (sec.get("subsections") or []):
                leaf_sections.append((sec, depth, path_tuple))
        total_subsections = len(leaf_sections)
        leaf_index = {
            (str(sec.get("id") or ""), path_tuple): idx
            for idx, (sec, _, path_tuple) in enumerate(leaf_sections, start=1)
        }
        section_times: list[float] = []
        prev_sections: list[Tuple[str, str]] = []
        with draft_path.open("a", encoding="utf-8") as handle:
            for sec, depth, path_tuple in _iter_outline_sections(outline_sections, depth=2):
                title = str(sec.get("title", "")).strip()
                if not title:
                    continue
                heading = "#" * min(6, depth) + " " + title
                handle.write(heading + "\n\n")

                subs = sec.get("subsections") or []
                if subs:
                    continue
                section_dir = _section_dir(out_dir, sec, path_tuple)
                section_dir.mkdir(parents=True, exist_ok=True)
                section_start = time.time()
                subsection_no = leaf_index.get((str(sec.get("id") or ""), path_tuple), len(section_times) + 1)
                path_label = " > ".join(path_tuple)
                if resume_sections:
                    _migrate_legacy_section_dir(out_dir, sec, path_tuple)
                section_path = section_dir / "section.md"
                if resume_sections and section_path.exists():
                    existing_md = section_path.read_text(encoding="utf-8", errors="ignore").strip()
                    citations = _extract_citation_keys(existing_md)
                    if min_section_citations > 0 and len(citations) < min_section_citations:
                        existing_md = ""
                    else:
                        next_id, missing_citations = _merge_cited_mcp_sources(
                            citations, mcp_index, sources, dedupe_map, key_map, next_id
                        )
                        if missing_citations:
                            existing_md = ""
                        else:
                            missing_meta = []
                            for key in citations:
                                source_id = key_map.get(key, key)
                                entry = next(
                                    (s for s in sources if s.get("source_id") == source_id),
                                    None,
                                )
                                if entry is None:
                                    continue
                                if not _source_metadata_complete(entry):
                                    _enrich_source_metadata(entry, mcp_papers)
                                if not _source_metadata_complete(entry):
                                    missing_meta.append(source_id)
                            if missing_meta:
                                existing_md = ""
                    if existing_md:
                        handle.write(existing_md.strip() + "\n\n")
                        if prev_sections_limit > 0:
                            prev_sections.append((title, existing_md.strip()))
                            if len(prev_sections) > prev_sections_limit:
                                prev_sections = prev_sections[-prev_sections_limit:]
                        section_times.append(time.time() - section_start)
                        completed = len(section_times)
                        avg = sum(section_times) / completed if completed else 0.0
                        remaining = max(0, total_subsections - completed)
                        eta = remaining * avg
                        _log_progress(
                            logger,
                            f"[PROPOSAL] Subsection {subsection_no}/{total_subsections} reused: {path_label} "
                            f"(elapsed {_format_duration(section_times[-1])}, ETA {_format_duration(eta)}).",
                        )
                        continue

                query = _build_section_query(sec, proposal_text)
                kb1_items = kb1_retrieval.retrieve(query, k=6, allow_web=False)
                kb2_items = kb2_retrieval.retrieve(query, k=6, allow_web=False)
                kb2_seed_for_section = _select_seed_items_for_query(
                    query,
                    kb2_seed_items,
                    k=min(4, max(0, len(kb2_seed_items))),
                )
                if kb2_seed_for_section:
                    kb2_items = _merge_retrieval_items(kb2_items, kb2_seed_for_section)
                web_items = []
                if web_retrieval is not None:
                    web_items = web_retrieval.retrieve(query, k=web_k, allow_web=True)
                if web_items:
                    _register_mcp_items(web_items, mcp_index, mcp_papers)

                kb1_ctx = kb1_retrieval.format_context(kb1_items) if kb1_items else ""
                kb2_ctx = kb2_retrieval.format_context(kb2_items) if kb2_items else ""
                kb1_ctx = _strip_kb_cite_keys(kb1_ctx)
                kb2_ctx = _strip_kb_cite_keys(kb2_ctx)
                web_ctx = web_retrieval.format_context(web_items) if web_retrieval and web_items else ""

                expected = sec.get("expected_evidence", [])
                compliance_map = sec.get("compliance_mapping", [])
                target_length = sec.get("target_length", {})
                purpose = sec.get("purpose", "")
                what_to_write = sec.get("what_to_write", [])
                section_constraint = _match_section_constraint(
                    title,
                    path_tuple,
                    proposal_metadata.get("section_constraints", []) if isinstance(proposal_metadata, dict) else [],
                )
                if section_constraint:
                    target_length = _apply_section_constraint(target_length, section_constraint)
                target_words, min_words, max_words = _target_word_bounds(target_length)
                target_chars, min_chars, max_chars = _target_char_bounds(target_length)
                section_chunk_chars = int(
                    os.environ.get("AUTOGENBOOK_PROPOSAL_SECTION_CHUNK_CHARS", "2500") or "2500"
                )
                section_max_chunks = int(
                    os.environ.get("AUTOGENBOOK_PROPOSAL_SECTION_MAX_CHUNKS", "6") or "6"
                )
                previous_text = ""
                if prev_sections and prev_sections_limit > 0:
                    slice_sections = prev_sections[-prev_sections_limit:]
                    previous_text = "\n\n".join(
                        f"[{i + 1}] {title}\n{content}"
                        for i, (title, content) in enumerate(slice_sections)
                    )

                user_prompt = (
                    "SECTION_CONTEXT:\\n"
                    f"- section_id: {sec.get('id','')}\\n"
                    f"- title: {title}\\n"
                    f"- purpose: {purpose}\\n"
                    f"- what_to_write: {what_to_write}\\n"
                    f"- compliance_mapping: {compliance_map}\\n"
                    f"- expected_evidence: {expected}\\n"
                    f"- target_length: {target_length}\\n"
                    f"- outline_path: {' > '.join(path_tuple)}\\n\\n"
                    "PROPOSAL_INPUT:\\n"
                    f"{proposal_text}\\n\\n"
                )
                if target_words:
                    user_prompt += (
                        f"TARGET_LENGTH_WORDS:\\n- target: {target_words}\\n"
                        f"- min: {min_words}\\n- max: {max_words}\\n\\n"
                    )
                if target_chars:
                    user_prompt += (
                        f"TARGET_LENGTH_CHARS:\\n- target: {target_chars}\\n"
                        f"- min: {min_chars}\\n- max: {max_chars}\\n\\n"
                    )
                if section_constraint:
                    constraint_lines = []
                    if section_constraint.get("requirement"):
                        constraint_lines.append(f"requirement: {section_constraint.get('requirement')}")
                    for key in ("page_limit", "word_limit", "char_limit"):
                        if section_constraint.get(key):
                            constraint_lines.append(f"{key}: {section_constraint.get(key)}")
                    if constraint_lines:
                        user_prompt += "SECTION_CONSTRAINTS:\\n- " + "\\n- ".join(constraint_lines) + "\\n\\n"
                if previous_text:
                    user_prompt += f"PREVIOUS_SECTIONS:\\n{previous_text}\\n\\n"
                user_prompt += (
                    "RETRIEVED_CONTEXT:\\n"
                    f"{_format_kb_context('KB1', kb1_ctx)}\\n\\n"
                    f"{_format_kb_context('KB2', kb2_ctx)}\\n\\n"
                )
                if web_ctx:
                    user_prompt += f"{_format_kb_context('WEB', web_ctx)}\\n\\n"
                available_keys = {
                    f"SRC:{item.cite_key}"
                    for item in (web_items or [])
                    if getattr(item, "cite_key", None)
                }
                if mcp_index:
                    available_keys |= {f"SRC:{key}" for key in mcp_index.keys()}
                existing_ids = {entry.get("source_id") for entry in sources if entry.get("source_id")}
                existing_keys = {
                    key
                    for entry in sources
                    for key in (entry.get("source_keys") or [])
                    if key
                }
                if existing_ids:
                    available_keys |= {str(sid) for sid in existing_ids if sid}
                if existing_keys:
                    available_keys |= {str(key) for key in existing_keys}
                available_keys = {
                    f"SRC:{key}"
                    for key, item in mcp_index.items()
                    if _item_metadata_complete(item)
                }
                available_keys_list = sorted(available_keys)
                if available_keys:
                    keys_block = "\n".join(f"- {key}" for key in sorted(available_keys))
                    user_prompt += (
                        f"AVAILABLE_CITATION_KEYS:\\n{keys_block}\\n"
                        "Use ONLY the keys above or keys produced by your MCP tool calls in this response. "
                        "Do NOT cite KB sources.\\n\\n"
                    )
                else:
                    user_prompt += (
                        "AVAILABLE_CITATION_KEYS:\\n(none)\\n"
                        "You MUST call MCP tools to retrieve sources and then cite them using "
                        "[SRC:<cite_key>] in the text. Do NOT cite KB sources.\\n\\n"
                    )
                user_prompt += (
                    "IMPORTANT: Do not include a section heading in your markdown; it will be added by the orchestrator."
                )

                section_markdown = ""
                section_feedback = ""
                section_ok = False
                last_failure: Dict[str, Any] = {}
                section_draft = ""
                max_section_attempts = section_retries + 1
                min_length_relax_raw = os.environ.get(
                    "AUTOGENBOOK_PROPOSAL_SECTION_MIN_RELAX", "0.40"
                ).strip() or "0.40"
                try:
                    min_length_relax_ratio = float(min_length_relax_raw)
                except Exception:
                    min_length_relax_ratio = 0.40
                min_length_relax_ratio = max(0.0, min(1.0, min_length_relax_ratio))
                max_length_relax_raw = os.environ.get(
                    "AUTOGENBOOK_PROPOSAL_SECTION_MAX_RELAX", "2.00"
                ).strip() or "2.00"
                try:
                    max_length_relax_ratio = float(max_length_relax_raw)
                except Exception:
                    max_length_relax_ratio = 2.00
                max_length_relax_ratio = max(1.0, min(4.0, max_length_relax_ratio))
                for attempt in range(max_section_attempts):
                    prompt = user_prompt
                    if section_draft:
                        prompt += f"\n\nSECTION_DRAFT:\n{section_draft}\n"
                    if section_feedback:
                        prompt += f"\n\nFEEDBACK_FROM_ORCHESTRATOR:\n{section_feedback}\n"
                    if attempt >= section_retries:
                        prompt += (
                            "\n\nLENGTH_REWRITE:\n"
                            "Rewrite the SECTION_DRAFT to meet the target length constraints "
                            "while preserving all citations and academic style.\n"
                        )
                    try:
                        _used_sources = []
                        if target_chars and min_chars and target_chars > section_chunk_chars:
                            remaining_target = target_chars
                            chunks_needed = int(
                                math.ceil(float(target_chars) / max(1, section_chunk_chars))
                            )
                            chunk_limit = max(1, min(section_max_chunks, chunks_needed))
                            chunk_texts: list[str] = []
                            for chunk_idx in range(chunk_limit):
                                chunk_target = min(section_chunk_chars, remaining_target)
                                chunk_prompt = prompt
                                if chunk_texts:
                                    chunk_prompt += (
                                        "\n\nSECTION_DRAFT:\n" + "\n\n".join(chunk_texts) + "\n"
                                    )
                                if chunk_texts:
                                    chunk_prompt += (
                                        "\n\nCHUNK_MODE: append_only\n"
                                        f"CHUNK_INDEX: {chunk_idx + 1}/{chunk_limit}\n"
                                        f"CHUNK_TARGET_CHARS: {chunk_target}\n"
                                        "Return ONLY the next chunk (no headings), "
                                        "then SOURCES_JSON for this chunk."
                                    )
                                else:
                                    chunk_prompt += (
                                        "\n\nCHUNK_MODE: append_only\n"
                                        f"CHUNK_INDEX: {chunk_idx + 1}/{chunk_limit}\n"
                                        f"CHUNK_TARGET_CHARS: {chunk_target}\n"
                                        "Return ONLY the next chunk (no headings), "
                                        "then SOURCES_JSON for this chunk."
                                    )
                                chunk_md, chunk_sources, _missing_keys = _generate_section_markdown(
                                    llm=llm4,
                                    system_prompt=prompts["llm4_researcher_writer_system"],
                                    user_prompt=chunk_prompt,
                                    language=language,
                                    allow_tools=enable_web,
                                    max_attempts=2,
                                    enforce_sources_match=False,
                                    model=model_llm4,
                                    out_dir=out_dir,
                                    label="llm4",
                                    usage_extra={
                                        "stage": "draft_chunk",
                                        "iter": iter_idx,
                                        "section": sec.get("id") or title,
                                        "chunk": chunk_idx + 1,
                                    },
                                )
                                chunk_md = chunk_md.strip()
                                if chunk_md:
                                    chunk_texts.append(chunk_md)
                                if isinstance(chunk_sources, list):
                                    _used_sources.extend(chunk_sources)
                                if min_chars:
                                    current_chars = _count_chars("\n\n".join(chunk_texts))
                                    remaining_target = max(0, target_chars - current_chars)
                                    if current_chars >= min_chars:
                                        break
                            section_markdown = "\n\n".join(chunk_texts).strip()
                        else:
                            section_markdown, _used_sources, _missing_keys = _generate_section_markdown(
                                llm=llm4,
                                system_prompt=prompts["llm4_researcher_writer_system"],
                                user_prompt=prompt,
                                language=language,
                                allow_tools=enable_web,
                                max_attempts=2,
                                enforce_sources_match=False,
                                model=model_llm4,
                                out_dir=out_dir,
                                label="llm4",
                                usage_extra={
                                    "stage": "draft",
                                    "iter": iter_idx,
                                    "section": sec.get("id") or title,
                                },
                            )
                    except Exception as exc:
                        section_feedback = str(exc)
                        last_failure = {
                            "reason": "exception",
                            "detail": str(exc),
                            "attempt": attempt,
                        }
                        continue

                    section_draft = section_markdown
                    tool_items: list[RetrievalItem] = []
                    for entry in getattr(llm4, "last_tool_results", []) or []:
                        payload = entry.get("result") if isinstance(entry, dict) else None
                        tool_items.extend(items_from_tool_result(payload))
                    _register_mcp_items(tool_items, mcp_index, mcp_papers)

                    bullet_ratio = _bullet_ratio(section_markdown)
                    if bullet_ratio > 0.25:
                        collapsed = _collapse_bullets_to_paragraphs(section_markdown)
                        collapsed_ratio = _bullet_ratio(collapsed)
                        if collapsed_ratio <= 0.25:
                            section_markdown = collapsed
                        else:
                            section_feedback = (
                                "Rewrite the section as cohesive paragraphs (minimal bullet lists). "
                                "Keep all citations and maintain scientific academic style."
                            )
                            last_failure = {
                                "reason": "bullet_heavy",
                                "ratio": bullet_ratio,
                                "attempt": attempt,
                            }
                            continue
                    char_count = _count_chars(section_markdown)
                    if target_chars and min_chars and max_chars:
                        if char_count < min_chars:
                            if attempt >= max_section_attempts - 1:
                                fallback_key = available_keys_list[0] if available_keys_list else None
                                if not section_markdown.strip():
                                    section_markdown = _build_minimal_section_fallback(
                                        title=title,
                                        purpose=purpose,
                                        what_to_write=what_to_write,
                                        language=language,
                                        cite_key=fallback_key,
                                    )
                                    char_count = _count_chars(section_markdown)
                                relaxed_min_chars = max(400, int(round(min_chars * min_length_relax_ratio)))
                                if char_count >= relaxed_min_chars:
                                    _log_progress(
                                        logger,
                                        f"[PROPOSAL] Section short but accepted after retries: {title} "
                                        f"({char_count} chars, target min {min_chars}, relaxed min {relaxed_min_chars}).",
                                    )
                                else:
                                    section_feedback = (
                                        f"Section too short ({char_count} chars). "
                                        f"Expand to {target_chars} chars (min {min_chars}, max {max_chars}) "
                                        "while keeping citations and academic style."
                                    )
                                    last_failure = {
                                        "reason": "too_short",
                                        "target_chars": target_chars,
                                        "found_chars": char_count,
                                        "relaxed_min_chars": relaxed_min_chars,
                                        "attempt": attempt,
                                    }
                                    continue
                            else:
                                section_feedback = (
                                    f"Section too short ({char_count} chars). "
                                    f"Expand to {target_chars} chars (min {min_chars}, max {max_chars}) "
                                    "while keeping citations and academic style."
                                )
                                last_failure = {
                                    "reason": "too_short",
                                    "target_chars": target_chars,
                                    "found_chars": char_count,
                                    "attempt": attempt,
                                }
                                continue
                        if char_count > max_chars:
                            if attempt >= max_section_attempts - 1:
                                condense_prompt = (
                                    user_prompt
                                    + "\n\nSECTION_DRAFT:\n"
                                    + section_markdown
                                    + "\n\nFEEDBACK_FROM_ORCHESTRATOR:\n"
                                    + f"Condense to {target_chars} chars "
                                      f"(min {min_chars}, max {max_chars}) while keeping all citations.\n"
                                    + "Rewrite into cohesive paragraphs and preserve academic style."
                                    "\n"
                                )
                                try:
                                    condensed_md, condensed_sources, _ = _generate_section_markdown(
                                        llm=llm4,
                                        system_prompt=prompts["llm4_researcher_writer_system"],
                                        user_prompt=condense_prompt,
                                        language=language,
                                        allow_tools=False,
                                        max_attempts=2,
                                        enforce_sources_match=False,
                                        model=model_llm4,
                                        out_dir=out_dir,
                                        label="llm4",
                                        usage_extra={
                                            "stage": "condense",
                                            "iter": iter_idx,
                                            "section": sec.get("id") or title,
                                        },
                                    )
                                    section_markdown = condensed_md
                                    if isinstance(condensed_sources, list):
                                        _used_sources.extend(condensed_sources)
                                    if _bullet_ratio(section_markdown) > 0.25:
                                        collapsed = _collapse_bullets_to_paragraphs(section_markdown)
                                        if _bullet_ratio(collapsed) <= 0.25:
                                            section_markdown = collapsed
                                    char_count = _count_chars(section_markdown)
                                    if char_count > max_chars:
                                        relaxed_max_chars = max(
                                            max_chars + 1,
                                            int(round(max_chars * max_length_relax_ratio)),
                                        )
                                        if char_count <= relaxed_max_chars:
                                            _log_progress(
                                                logger,
                                                f"[PROPOSAL] Section long but accepted after retries: {title} "
                                                f"({char_count} chars, target max {max_chars}, "
                                                f"relaxed max {relaxed_max_chars}).",
                                            )
                                        else:
                                            section_feedback = (
                                                f"Section too long ({char_count} chars). "
                                                f"Shorten to {target_chars} chars (min {min_chars}, max {max_chars})."
                                            )
                                            last_failure = {
                                                "reason": "too_long",
                                                "target_chars": target_chars,
                                                "found_chars": char_count,
                                                "relaxed_max_chars": relaxed_max_chars,
                                                "attempt": attempt,
                                            }
                                            continue
                                except Exception as exc:
                                    relaxed_max_chars = max(
                                        max_chars + 1,
                                        int(round(max_chars * max_length_relax_ratio)),
                                    )
                                    if char_count <= relaxed_max_chars:
                                        _log_progress(
                                            logger,
                                            f"[PROPOSAL] Condense step failed, but section accepted within relaxed max: "
                                            f"{title} ({char_count} chars, target max {max_chars}, "
                                            f"relaxed max {relaxed_max_chars}).",
                                        )
                                    else:
                                        section_feedback = str(exc)
                                        last_failure = {
                                            "reason": "too_long",
                                            "target_chars": target_chars,
                                            "found_chars": char_count,
                                            "relaxed_max_chars": relaxed_max_chars,
                                            "attempt": attempt,
                                        }
                                        continue
                            else:
                                section_feedback = (
                                    f"Section too long ({char_count} chars). "
                                    f"Shorten to {target_chars} chars (min {min_chars}, max {max_chars}) "
                                    "while keeping citations and academic style."
                                )
                                last_failure = {
                                    "reason": "too_long",
                                    "target_chars": target_chars,
                                    "found_chars": char_count,
                                    "attempt": attempt,
                                }
                                continue
                    else:
                        word_count = _count_words(section_markdown)
                        if target_words and min_words and max_words:
                            if word_count < min_words:
                                if attempt >= max_section_attempts - 1:
                                    fallback_key = available_keys_list[0] if available_keys_list else None
                                    if not section_markdown.strip():
                                        section_markdown = _build_minimal_section_fallback(
                                            title=title,
                                            purpose=purpose,
                                            what_to_write=what_to_write,
                                            language=language,
                                            cite_key=fallback_key,
                                        )
                                        word_count = _count_words(section_markdown)
                                    relaxed_min_words = max(80, int(round(min_words * min_length_relax_ratio)))
                                    if word_count >= relaxed_min_words:
                                        _log_progress(
                                            logger,
                                            f"[PROPOSAL] Section short but accepted after retries: {title} "
                                            f"({word_count} words, target min {min_words}, relaxed min {relaxed_min_words}).",
                                        )
                                    else:
                                        section_feedback = (
                                            f"Section too short ({word_count} words). "
                                            f"Expand to {target_words} words (min {min_words}, max {max_words}) "
                                            "while keeping citations and academic style."
                                        )
                                        last_failure = {
                                            "reason": "too_short",
                                            "target_words": target_words,
                                            "found_words": word_count,
                                            "relaxed_min_words": relaxed_min_words,
                                            "attempt": attempt,
                                        }
                                        continue
                                else:
                                    section_feedback = (
                                        f"Section too short ({word_count} words). "
                                        f"Expand to {target_words} words (min {min_words}, max {max_words}) "
                                        "while keeping citations and academic style."
                                    )
                                    last_failure = {
                                        "reason": "too_short",
                                        "target_words": target_words,
                                        "found_words": word_count,
                                        "attempt": attempt,
                                    }
                                    continue
                            if word_count > max_words:
                                if attempt >= max_section_attempts - 1:
                                    condense_prompt = (
                                        user_prompt
                                        + "\n\nSECTION_DRAFT:\n"
                                        + section_markdown
                                        + "\n\nFEEDBACK_FROM_ORCHESTRATOR:\n"
                                        + f"Condense to {target_words} words "
                                          f"(min {min_words}, max {max_words}) while keeping all citations.\n"
                                        + "Rewrite into cohesive paragraphs and preserve academic style."
                                        "\n"
                                    )
                                    try:
                                        condensed_md, condensed_sources, _ = _generate_section_markdown(
                                            llm=llm4,
                                            system_prompt=prompts["llm4_researcher_writer_system"],
                                            user_prompt=condense_prompt,
                                            language=language,
                                            allow_tools=False,
                                            max_attempts=2,
                                            enforce_sources_match=False,
                                            model=model_llm4,
                                            out_dir=out_dir,
                                            label="llm4",
                                            usage_extra={
                                                "stage": "condense",
                                                "iter": iter_idx,
                                                "section": sec.get("id") or title,
                                            },
                                        )
                                        section_markdown = condensed_md
                                        if isinstance(condensed_sources, list):
                                            _used_sources.extend(condensed_sources)
                                        if _bullet_ratio(section_markdown) > 0.25:
                                            collapsed = _collapse_bullets_to_paragraphs(section_markdown)
                                            if _bullet_ratio(collapsed) <= 0.25:
                                                section_markdown = collapsed
                                        word_count = _count_words(section_markdown)
                                        if word_count > max_words:
                                            relaxed_max_words = max(
                                                max_words + 1,
                                                int(round(max_words * max_length_relax_ratio)),
                                            )
                                            if word_count <= relaxed_max_words:
                                                _log_progress(
                                                    logger,
                                                    f"[PROPOSAL] Section long but accepted after retries: {title} "
                                                    f"({word_count} words, target max {max_words}, "
                                                    f"relaxed max {relaxed_max_words}).",
                                                )
                                            else:
                                                section_feedback = (
                                                    f"Section too long ({word_count} words). "
                                                    f"Shorten to {target_words} words (min {min_words}, max {max_words})."
                                                )
                                                last_failure = {
                                                    "reason": "too_long",
                                                    "target_words": target_words,
                                                    "found_words": word_count,
                                                    "relaxed_max_words": relaxed_max_words,
                                                    "attempt": attempt,
                                                }
                                                continue
                                    except Exception as exc:
                                        relaxed_max_words = max(
                                            max_words + 1,
                                            int(round(max_words * max_length_relax_ratio)),
                                        )
                                        if word_count <= relaxed_max_words:
                                            _log_progress(
                                                logger,
                                                f"[PROPOSAL] Condense step failed, but section accepted within relaxed max: "
                                                f"{title} ({word_count} words, target max {max_words}, "
                                                f"relaxed max {relaxed_max_words}).",
                                            )
                                        else:
                                            section_feedback = str(exc)
                                            last_failure = {
                                                "reason": "too_long",
                                                "target_words": target_words,
                                                "found_words": word_count,
                                                "relaxed_max_words": relaxed_max_words,
                                                "attempt": attempt,
                                            }
                                            continue
                                else:
                                    section_feedback = (
                                        f"Section too long ({word_count} words). "
                                        f"Shorten to {target_words} words (min {min_words}, max {max_words}) "
                                        "while keeping citations and academic style."
                                    )
                                    last_failure = {
                                        "reason": "too_long",
                                        "target_words": target_words,
                                        "found_words": word_count,
                                        "attempt": attempt,
                                    }
                                    continue

                    citations = _extract_citation_keys(section_markdown)
                    section_min_citations = min_section_citations
                    if section_min_citations > 0 and not available_keys_list:
                        section_min_citations = 0
                    if section_min_citations > 0 and len(citations) < section_min_citations:
                        if attempt >= max_section_attempts - 1 and available_keys_list:
                            salvage_key = available_keys_list[0]
                            section_markdown = (
                                section_markdown.rstrip()
                                + "\n\n"
                                + _salvage_citation_sentence(language, salvage_key)
                            )
                            citations = _extract_citation_keys(section_markdown)
                            if len(citations) >= section_min_citations:
                                pass
                            else:
                                section_feedback = (
                                    f"Section must contain at least {section_min_citations} citations. "
                                    f"Found {len(citations)}."
                                )
                                last_failure = {
                                    "reason": "min_section_citations",
                                    "required": section_min_citations,
                                    "found": len(citations),
                                    "attempt": attempt,
                                }
                                continue
                        else:
                            section_feedback = (
                                f"Section must contain at least {section_min_citations} citations. "
                                f"Found {len(citations)}."
                            )
                            last_failure = {
                                "reason": "min_section_citations",
                                "required": section_min_citations,
                                "found": len(citations),
                                "attempt": attempt,
                            }
                            continue
                    used_sources = _used_sources or []
                    used_by_key = {
                        str(item.get("source_key")).strip(): item
                        for item in used_sources
                        if isinstance(item, dict) and str(item.get("source_key", "")).strip()
                    }
                    missing_used: set[str] = set()
                    for key in citations:
                        if key in key_map:
                            continue
                        used_entry = used_by_key.get(key)
                        if not used_entry:
                            continue
                        if key.startswith("SRC:"):
                            raw_key = key.split(":", 1)[1]
                            if raw_key in mcp_index:
                                continue
                        used_type = str(
                            used_entry.get("source_type") or used_entry.get("type") or ""
                        ).strip().lower()
                        if used_type in {"kb1", "kb2"}:
                            resolved = _resolve_used_source(used_entry, mcp_papers, mcp_index)
                            if resolved is None:
                                missing_used.add(key)
                                continue
                            normalized = _normalized_source_from_item(resolved, source_key=key)
                            next_id = _merge_sources(sources, dedupe_map, key_map, next_id, normalized)
                            continue
                        resolved = _resolve_used_source(used_entry, mcp_papers, mcp_index)
                        if resolved is None:
                            missing_used.add(key)
                            continue
                        normalized = _normalized_source_from_item(resolved, source_key=key)
                        next_id = _merge_sources(sources, dedupe_map, key_map, next_id, normalized)
                    recovered_sources: list[Dict[str, Any]] = []
                    if missing_used:
                        for key in list(missing_used):
                            if not key.startswith("SRC:"):
                                continue
                            raw_key = key.split(":", 1)[1]
                            item = mcp_index.get(raw_key)
                            if item is None or not _item_metadata_complete(item):
                                continue
                            normalized = _normalized_source_from_item(item, source_key=key)
                            next_id = _merge_sources(
                                sources, dedupe_map, key_map, next_id, normalized
                            )
                            recovered_sources.append(normalized)
                            missing_used.discard(key)
                        if missing_used:
                            resolvable = set()
                            for key in missing_used:
                                if key in key_map:
                                    resolvable.add(key)
                                    continue
                                if key.startswith("SRC:"):
                                    raw_key = key.split(":", 1)[1]
                                    item = mcp_index.get(raw_key)
                                    if item is not None and _item_metadata_complete(item):
                                        resolvable.add(key)
                            if resolvable and resolvable == missing_used:
                                missing_used = set()
                    if missing_used:
                        kb_keys = {key for key in missing_used if re.search(r"KB[12]", key, re.IGNORECASE)}
                        kb_note = ""
                        if kb_keys:
                            kb_note = (
                                " You cited KB sources (not allowed). Remove these KB citations "
                                f"and replace with MCP tool citations: {', '.join(sorted(kb_keys))}."
                            )
                        section_feedback = (
                            "Unable to validate sources via MCP for: "
                            + ", ".join(sorted(missing_used))
                            + ". Use MCP tools to retrieve those sources and cite only returned keys "
                              "with full metadata (authors/title/year or DOI/arXiv)."
                            + kb_note
                        )
                        last_failure = {
                            "reason": "missing_used_sources",
                            "keys": sorted(missing_used),
                            "attempt": attempt,
                        }
                        continue
                    next_id, missing_citations = _merge_cited_mcp_sources(
                        citations, mcp_index, sources, dedupe_map, key_map, next_id
                    )
                    if missing_citations:
                        section_feedback = (
                            "Missing MCP sources for citations: "
                            + ", ".join(sorted(missing_citations))
                            + ". Cite only MCP tool results and include them in SOURCES_JSON."
                        )
                        last_failure = {
                            "reason": "missing_citations",
                            "keys": sorted(missing_citations),
                            "attempt": attempt,
                        }
                        continue

                    missing_meta = []
                    for key in citations:
                        source_id = key_map.get(key, key)
                        entry = next((s for s in sources if s.get("source_id") == source_id), None)
                        if entry is None:
                            continue
                        if not _source_metadata_complete(entry):
                            _enrich_source_metadata(entry, mcp_papers)
                        if not _source_metadata_complete(entry):
                            missing_meta.append(source_id)
                    if missing_meta:
                        section_feedback = (
                            "Missing source metadata (authors/title/year or DOI/arXiv): "
                            + ", ".join(sorted(set(missing_meta)))
                            + ". Retrieve a better MCP source with complete metadata or rewrite "
                              "the section to use only sources with complete metadata."
                        )
                        last_failure = {
                            "reason": "missing_metadata",
                            "sources": sorted(set(missing_meta)),
                            "attempt": attempt,
                        }
                        continue

                    tool_calls = list(getattr(llm4, "last_tool_results", []) or [])
                    merged_used_sources = list(used_sources)
                    if recovered_sources:
                        merged_used_sources.extend(recovered_sources)
                    _write_sources_store(sources_path, sources)
                    sources, dedupe_map, key_map, next_id = _rebuild_sources_index(sources)
                    _write_section_artifacts(
                        section_dir,
                        markdown=section_markdown,
                        citations=citations,
                        used_sources=merged_used_sources,
                        tool_calls=tool_calls,
                    )
                    section_ok = True
                    break

                if not section_ok:
                    failure_payload = {
                        "section_id": str(sec.get("id") or ""),
                        "title": title,
                        "outline_path": " > ".join(path_tuple),
                        "attempts": section_retries,
                        "last_failure": last_failure or {"reason": "unknown"},
                    }
                    section_dir.mkdir(parents=True, exist_ok=True)
                    failure_path = section_dir / "failure.json"
                    failure_path.write_text(
                        json.dumps(failure_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    _log_progress(
                        logger,
                        f"[PROPOSAL] Section failed: {title} "
                        f"(see {failure_path.as_posix()}).",
                    )
                    raise RuntimeError(
                        "Failed to generate section with valid MCP citations and metadata. "
                        f"Last failure: {failure_payload['last_failure'].get('reason','unknown')}."
                    )

                handle.write(section_markdown.strip() + "\n\n")
                if prev_sections_limit > 0:
                    prev_sections.append((title, section_markdown.strip()))
                    if len(prev_sections) > prev_sections_limit:
                        prev_sections = prev_sections[-prev_sections_limit:]
                section_times.append(time.time() - section_start)
                completed = len(section_times)
                avg = sum(section_times) / completed if completed else 0.0
                remaining = max(0, total_subsections - completed)
                eta = remaining * avg
                _log_progress(
                    logger,
                    f"[PROPOSAL] Subsection {subsection_no}/{total_subsections} done: {path_label} "
                    f"(elapsed {_format_duration(section_times[-1])}, ETA {_format_duration(eta)}).",
                )

        draft_text = draft_path.read_text(encoding="utf-8", errors="ignore")
        normalized_draft = _normalize_citation_keys(draft_text, key_map)
        normalized_draft = _sanitize_markdown_headings(normalized_draft)
        known_source_ids = {entry.get("source_id") for entry in sources if entry.get("source_id")}
        missing_global = _extract_citation_keys(normalized_draft) - known_source_ids
        if missing_global:
            raise RuntimeError(
                "Missing citations after drafting: " + ", ".join(sorted(missing_global))
            )
        if normalized_draft != draft_text:
            draft_path.write_text(normalized_draft, encoding="utf-8")

        pouzita_path = draft_dir / "pouzita_literatura.md"
        bibliography_md = _write_bibliography(pouzita_path, sources, language)

        final_path = final_dir / "proposal.md"
        final_content = normalized_draft.rstrip() + "\n\n" + bibliography_md
        final_path.write_text(final_content, encoding="utf-8")

        required_formats = ["markdown"]
        if last_llm1_output and isinstance(last_llm1_output, dict):
            format_spec = last_llm1_output.get("format_spec", {})
            formats = format_spec.get("required_output_formats") if isinstance(format_spec, dict) else None
            if isinstance(formats, list) and formats:
                required_formats = [str(f) for f in formats if str(f).strip()]
        if bool(getattr(args, "docx", False)):
            normalized_formats = [str(f).strip().lower() for f in required_formats if str(f).strip()]
            if "docx" not in normalized_formats:
                required_formats.append("docx")
        (
            doc_target_words,
            doc_min_words,
            doc_max_words,
            doc_target_chars,
            doc_min_chars,
            doc_max_chars,
            doc_target_pages,
        ) = _target_doc_word_bounds(
            last_llm1_output.get("format_spec", {}) if isinstance(last_llm1_output, dict) else {}
        )
        doc_char_limit = None
        if isinstance(last_llm1_output, dict):
            limits = last_llm1_output.get("format_spec", {}).get("limits", {})
            if isinstance(limits, dict):
                doc_char_limit = limits.get("char_limit")

        _log_progress(
            logger,
            f"[PROPOSAL] Phase 3/4: Final review loop (max {max_iters} iterations).",
        )
        review_dir = out_dir / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        kb1_digest = []
        if isinstance(last_llm1_output, dict):
            kb1_digest = last_llm1_output.get("kb1_requirement_digest", []) or []
        current_md = final_content
        current_report = ""
        llm5_feedback = ""
        review_times: list[float] = []

        for j in range(max_iters):
            review_start = time.time()
            stop_early = False
            _log_progress(
                logger,
                f"[PROPOSAL] Review iteration {j + 1}/{max_iters}...",
            )
            try:
                iter_dir = review_dir / f"iter_{j}"
                iter_dir.mkdir(parents=True, exist_ok=True)

                review_query = (proposal_text + "\n\n" + current_md[:2000]).strip()
                kb1_items = kb1_retrieval.retrieve(review_query, k=6, allow_web=False)
                kb2_items = kb2_retrieval.retrieve(review_query, k=6, allow_web=False)
                kb1_ctx = kb1_retrieval.format_context(kb1_items) if kb1_items else ""
                kb2_ctx = kb2_retrieval.format_context(kb2_items) if kb2_items else ""

                allowed_keys = {f"SRC:{key}" for key in mcp_index.keys()}
                allowed_ids = {entry.get("source_id") for entry in sources if entry.get("source_id")}
                allowed_ids = {key for key in allowed_ids if key}
                existing_keys = {
                    key
                    for entry in sources
                    for key in (entry.get("source_keys") or [])
                    if key
                }
                allowed_keys |= existing_keys
                allowed_keys_block = "\n".join(
                    f"- {key}" for key in sorted(allowed_keys | allowed_ids)
                ) if (allowed_keys or allowed_ids) else "(none)"
                sources_payload = {"sources": sources}
                review_chunk_chars = int(
                    os.environ.get("AUTOGENBOOK_PROPOSAL_REVIEW_CHUNK_CHARS", "12000") or "12000"
                )
                def _build_llm5_prompt(draft_md: str, feedback: str) -> tuple[str, bool, str]:
                    body = _strip_bibliography_section(draft_md)
                    use_chunk = len(body) > review_chunk_chars
                    prompt = (
                        ("SECTION_MODE: true\nSECTION_MARKDOWN:\n" + body + "\n\n")
                        if use_chunk
                        else ("PROPOSAL_DRAFT:\n" + f"{draft_md}\n\n")
                    )
                    prompt += (
                        "KB1_REQUIREMENTS_DIGEST_JSON:\n"
                        f"{json.dumps(kb1_digest, ensure_ascii=False, indent=2)}\n\n"
                        "KB_CONTEXT:\n"
                        f"{_format_kb_context('KB1', kb1_ctx)}\n\n"
                        f"{_format_kb_context('KB2', kb2_ctx)}\n\n"
                        "SOURCES_JSON:\n"
                        f"{json.dumps(sources_payload, ensure_ascii=False, indent=2)}\n\n"
                        "ALLOWED_CITATION_KEYS:\n"
                        f"{allowed_keys_block}\n\n"
                    )
                    if doc_target_words or doc_target_chars:
                        prompt += (
                            "TARGET_DOCUMENT_LENGTH:\n"
                            f"- target_words: {doc_target_words}\n"
                            f"- min_words: {doc_min_words}\n"
                            f"- max_words: {doc_max_words}\n"
                            f"- target_chars: {doc_target_chars}\n"
                            f"- min_chars: {doc_min_chars}\n"
                            f"- max_chars: {doc_max_chars}\n"
                            f"- target_pages: {doc_target_pages}\n"
                            f"- char_limit: {doc_char_limit}\n\n"
                        )
                    prompt += (
                        "STYLE_REQUIREMENTS:\n"
                        "- Use cohesive paragraphs; bullet lists only when strictly required.\n"
                        "- Maintain academic, research-report tone.\n"
                        "- Keep citations unchanged and present.\n\n"
                    )
                    if feedback:
                        prompt += f"FEEDBACK_FROM_ORCHESTRATOR:\n{feedback}\n\n"
                    return prompt, use_chunk, body

                llm5_user_prompt, use_chunk_review, review_body = _build_llm5_prompt(
                    current_md, llm5_feedback
                )

                iter_report_path = iter_dir / "final_review_report.md"
                iter_proposal_path = iter_dir / "proposal.md"

                if use_chunk_review:
                    chunks = _split_markdown_sections(review_body)
                    corrected_chunks: list[str] = []
                    reports: list[str] = []
                    for idx, chunk in enumerate(chunks, start=1):
                        chunk_prompt = llm5_user_prompt.replace(review_body, chunk)
                        llm5_output = llm5.chat(
                            [
                                {"role": "system", "content": prompts["llm5_opponent_final_system"]},
                                {"role": "user", "content": chunk_prompt},
                            ],
                            allow_tools=enable_web,
                            model=model_llm5,
                        )
                        _log_llm_usage(
                            out_dir,
                            llm5,
                            "llm5",
                            extra={"stage": "final_review_chunk", "iter": j, "chunk": idx},
                        )
                        review_md, review_report, err = _split_final_review_output(llm5_output)
                        if err:
                            llm5_feedback = (
                                "Output must include the markdown section followed by "
                                f"the delimiter {FINAL_REVIEW_DELIM} and the review report."
                            )
                            iter_proposal_path.write_text(llm5_output, encoding="utf-8")
                            iter_report_path.write_text(f"ERROR: {err}\n", encoding="utf-8")
                            if j < max_iters - 1:
                                continue
                            raise RuntimeError(err)
                        corrected_chunks.append(review_md.strip())
                        if review_report:
                            reports.append(review_report.strip())
                    review_md = "\n\n".join(corrected_chunks).strip()
                    review_report = "\n".join(reports).strip()
                else:
                    llm5_output = llm5.chat(
                        [
                            {"role": "system", "content": prompts["llm5_opponent_final_system"]},
                            {"role": "user", "content": llm5_user_prompt},
                        ],
                        allow_tools=enable_web,
                        model=model_llm5,
                    )
                    _log_llm_usage(
                        out_dir,
                        llm5,
                        "llm5",
                        extra={"stage": "final_review", "iter": j},
                    )

                    review_md, review_report, err = _split_final_review_output(llm5_output)
                    if err:
                        llm5_feedback = (
                            "Output must include the full markdown document followed by "
                            f"the delimiter {FINAL_REVIEW_DELIM} and the review report."
                        )
                        iter_proposal_path.write_text(llm5_output, encoding="utf-8")
                        iter_report_path.write_text(f"ERROR: {err}\n", encoding="utf-8")
                        if j < max_iters - 1:
                            continue
                        raise RuntimeError(err)

                tool_items: list[RetrievalItem] = []
                for entry in getattr(llm5, "last_tool_results", []) or []:
                    payload = entry.get("result") if isinstance(entry, dict) else None
                    tool_items.extend(items_from_tool_result(payload))
                _register_mcp_items(tool_items, mcp_index, mcp_papers)

                review_md = _normalize_citation_keys(review_md, key_map)
                review_md = _sanitize_markdown_headings(review_md)
                citations = _extract_citation_keys(review_md)
                next_id, missing = _merge_cited_mcp_sources(
                    citations, mcp_index, sources, dedupe_map, key_map, next_id
                )
                if missing:
                    llm5_feedback = (
                        "Remove or replace citations not present in MCP sources: "
                        + ", ".join(sorted(missing))
                    )
                    iter_proposal_path.write_text(review_md, encoding="utf-8")
                    iter_report_path.write_text(review_report or "(no report)", encoding="utf-8")
                    if j < max_iters - 1:
                        continue
                    repaired = False
                    repair_attempts = int(
                        os.environ.get("AUTOGENBOOK_PROPOSAL_FINAL_REVIEW_REPAIRS", "2") or "2"
                    )
                    repair_attempts = max(1, repair_attempts)
                    for repair_idx in range(repair_attempts):
                        repair_prompt, repair_chunk_review, repair_body = _build_llm5_prompt(
                            review_md, llm5_feedback
                        )
                        if repair_chunk_review:
                            chunks = _split_markdown_sections(repair_body)
                            corrected_chunks = []
                            reports = []
                            repair_failed = False
                            for idx, chunk in enumerate(chunks, start=1):
                                chunk_prompt = repair_prompt.replace(repair_body, chunk)
                                llm5_output = llm5.chat(
                                    [
                                        {
                                            "role": "system",
                                            "content": prompts["llm5_opponent_final_system"],
                                        },
                                        {"role": "user", "content": chunk_prompt},
                                    ],
                                    allow_tools=False,
                                    model=model_llm5,
                                )
                                _log_llm_usage(
                                    out_dir,
                                    llm5,
                                    "llm5",
                                    extra={
                                        "stage": "final_review_repair_chunk",
                                        "iter": j,
                                        "chunk": idx,
                                        "repair": repair_idx + 1,
                                    },
                                )
                                chunk_md, chunk_report, err = _split_final_review_output(llm5_output)
                                if err:
                                    repair_failed = True
                                    break
                                corrected_chunks.append(chunk_md.strip())
                                if chunk_report:
                                    reports.append(chunk_report.strip())
                            if repair_failed:
                                continue
                            review_md = "\n\n".join(corrected_chunks).strip()
                            review_report = "\n".join(reports).strip()
                        else:
                            llm5_output = llm5.chat(
                                [
                                    {
                                        "role": "system",
                                        "content": prompts["llm5_opponent_final_system"],
                                    },
                                    {"role": "user", "content": repair_prompt},
                                ],
                                allow_tools=False,
                                model=model_llm5,
                            )
                            _log_llm_usage(
                                out_dir,
                                llm5,
                                "llm5",
                                extra={
                                    "stage": "final_review_repair",
                                    "iter": j,
                                    "repair": repair_idx + 1,
                                },
                            )
                            review_md, review_report, err = _split_final_review_output(llm5_output)
                            if err:
                                continue
                        review_md = _normalize_citation_keys(review_md, key_map)
                        review_md = _sanitize_markdown_headings(review_md)
                        citations = _extract_citation_keys(review_md)
                        next_id, missing = _merge_cited_mcp_sources(
                            citations, mcp_index, sources, dedupe_map, key_map, next_id
                        )
                        if not missing:
                            repaired = True
                            break
                        llm5_feedback = (
                            "Remove or replace citations not present in MCP sources: "
                            + ", ".join(sorted(missing))
                        )
                    if not repaired:
                        raise RuntimeError(
                            "Missing MCP citations after final review: " + ", ".join(sorted(missing))
                        )
                review_md = _normalize_citation_keys(review_md, key_map)
                review_md = _sanitize_markdown_headings(review_md)
                review_citations = _extract_citation_keys(review_md)
                review_missing_meta = []
                for key in review_citations:
                    source_id = key_map.get(key, key)
                    entry = next((s for s in sources if s.get("source_id") == source_id), None)
                    if entry is None:
                        continue
                    if not _source_metadata_complete(entry):
                        _enrich_source_metadata(entry, mcp_papers)
                    if not _source_metadata_complete(entry):
                        review_missing_meta.append(source_id)
                if review_missing_meta:
                    llm5_feedback = (
                        "Missing source metadata (authors/title/year or DOI/arXiv): "
                        + ", ".join(sorted(set(review_missing_meta)))
                    )
                    iter_proposal_path.write_text(review_md, encoding="utf-8")
                    iter_report_path.write_text(review_report or "(no report)", encoding="utf-8")
                    if j < max_iters - 1:
                        continue
                    raise RuntimeError(
                        "Missing metadata after final review: " + ", ".join(sorted(set(review_missing_meta)))
                    )
                _write_sources_store(sources_path, sources)
                bibliography_md = _write_bibliography(pouzita_path, sources, language)

                detected_lang = ""
                language_ok = True
                if not language_explicit:
                    detected_lang = _detect_language(_strip_bibliography_section(review_md))
                    language_ok = _language_matches(detected_lang, language)
                if not language_ok:
                    llm5_feedback = (
                        f"Language mismatch: expected {language}, detected {detected_lang}. "
                        "Rewrite in the required language."
                    )
                    iter_proposal_path.write_text(review_md, encoding="utf-8")
                    iter_report_path.write_text(review_report or "(no report)", encoding="utf-8")
                    if j < max_iters - 1:
                        continue
                    raise RuntimeError(
                        f"Language mismatch after final review: expected {language}, detected {detected_lang}."
                    )

                review_md = _apply_bibliography(review_md, bibliography_md)
                review_text = _strip_bibliography_section(review_md)
                review_word_count = _count_words(review_text)
                review_char_count = _count_chars(review_text)
                review_bullet_ratio = _bullet_ratio(review_text)
                if doc_target_chars and doc_min_chars and doc_max_chars:
                    if review_char_count < doc_min_chars or review_char_count > doc_max_chars:
                        llm5_feedback = (
                            f"Document length is {review_char_count} chars. "
                            f"Rewrite to fit target {doc_target_chars} chars "
                            f"(min {doc_min_chars}, max {doc_max_chars}). "
                            "Keep headings and citations intact."
                        )
                        iter_proposal_path.write_text(review_md, encoding="utf-8")
                        iter_report_path.write_text(review_report or "(no report)", encoding="utf-8")
                        if j < max_iters - 1:
                            continue
                        length_warning = (
                            f"Length check warning: {review_char_count} chars "
                            f"(target {doc_target_chars}, min {doc_min_chars}, max {doc_max_chars})."
                        )
                        _log_progress(logger, f"[PROPOSAL] {length_warning}")
                        if review_report:
                            review_report = review_report.rstrip() + "\n- " + length_warning
                        else:
                            review_report = "- " + length_warning
                elif doc_target_words and doc_min_words and doc_max_words:
                    if review_word_count < doc_min_words or review_word_count > doc_max_words:
                        llm5_feedback = (
                            f"Document length is {review_word_count} words. "
                            f"Rewrite to fit target {doc_target_words} words "
                            f"(min {doc_min_words}, max {doc_max_words}). "
                            "Keep headings and citations intact."
                        )
                        iter_proposal_path.write_text(review_md, encoding="utf-8")
                        iter_report_path.write_text(review_report or "(no report)", encoding="utf-8")
                        if j < max_iters - 1:
                            continue
                        length_warning = (
                            f"Length check warning: {review_word_count} words "
                            f"(target {doc_target_words}, min {doc_min_words}, max {doc_max_words})."
                        )
                        _log_progress(logger, f"[PROPOSAL] {length_warning}")
                        if review_report:
                            review_report = review_report.rstrip() + "\n- " + length_warning
                        else:
                            review_report = "- " + length_warning
                if review_bullet_ratio > 0.25:
                    collapsed_md = _collapse_bullets_to_paragraphs(review_md)
                    collapsed_text = _strip_bibliography_section(collapsed_md)
                    collapsed_ratio = _bullet_ratio(collapsed_text)
                    if collapsed_ratio <= 0.25:
                        review_md = collapsed_md
                        review_text = collapsed_text
                        review_bullet_ratio = collapsed_ratio
                    else:
                        llm5_feedback = (
                            f"Bullet list ratio is too high ({review_bullet_ratio:.2f}). "
                            "Rewrite into cohesive paragraphs; keep citations and headings unchanged."
                        )
                        iter_proposal_path.write_text(review_md, encoding="utf-8")
                        iter_report_path.write_text(review_report or "(no report)", encoding="utf-8")
                        if j < max_iters - 1:
                            continue
                        style_warning = (
                            f"Paragraph style warning: bullet ratio {review_bullet_ratio:.2f} exceeds limit."
                        )
                        _log_progress(logger, f"[PROPOSAL] {style_warning}")
                        if review_report:
                            review_report = review_report.rstrip() + "\n- " + style_warning
                        else:
                            review_report = "- " + style_warning

                iter_proposal_path.write_text(review_md, encoding="utf-8")
                iter_report_path.write_text(review_report or "(no report)", encoding="utf-8")

                current_md = review_md
                current_report = review_report
                llm5_feedback = ""

                if _review_report_indicates_done(review_report) and language_ok:
                    stop_early = True
                    break
            finally:
                review_elapsed = time.time() - review_start
                review_times.append(review_elapsed)
                avg_review = sum(review_times) / len(review_times) if review_times else 0.0
                remaining_reviews = 0 if stop_early else max(0, max_iters - (j + 1))
                eta_review = remaining_reviews * avg_review
                _log_progress(
                    logger,
                    f"[PROPOSAL] Review iter {j + 1} done. "
                    f"Elapsed {_format_duration(review_elapsed)}, ETA {_format_duration(eta_review)}.",
                )

        final_proposal_path = final_dir / "proposal_final.md"
        final_report_path = final_dir / "final_review_report.md"
        final_eval_path = final_dir / "opponent_evaluation.md"

        current_md = _sanitize_markdown_headings(current_md)
        final_proposal_path.write_text(current_md, encoding="utf-8")
        final_report_path.write_text(current_report or "(no report)", encoding="utf-8")
        final_eval_path.write_text(current_report or "(no report)", encoding="utf-8")

        audit_enabled = bool(getattr(args, "audit", False))
        audit_mode = str(getattr(args, "audit_mode", "warn") or "warn")
        audit_window = int(getattr(args, "audit_window_chars", 600) or 600)
        if audit_mode == "off":
            audit_enabled = False
        if audit_enabled:
            audit_tex = _build_audit_tex(current_md, final_dir)
            if audit_tex is None or not audit_tex.exists():
                raise RuntimeError(
                    "Proposal audit requested, but pandoc is unavailable or audit LaTeX could not be generated."
                )
            tex_text = audit_tex.read_text(encoding="utf-8", errors="ignore")
            known_cite_keys = {
                str(entry.get("source_id"))
                for entry in sources
                if entry.get("source_id")
            }
            report = audit_latex(
                tex_path=audit_tex,
                tex_text=tex_text,
                doc_kind="proposal",
                known_cite_keys=known_cite_keys,
                known_rids=set(),
                project_root=Path.cwd(),
                config=AuditorConfig(
                    enabled=True,
                    mode=audit_mode,
                    evidence_window_chars=audit_window,
                ),
            )
            report.dump(out_dir / "audit_report.json")
            if audit_mode == "strict" and report.counts_by_severity.get(AuditSeverity.ERROR.value, 0) > 0:
                status = "error"
                error = "Proposal audit failed in strict mode. See audit_report.json for details."
                return 4

        _log_progress(
            logger,
            "[PROPOSAL] Phase 4/4: Converting outputs with pandoc...",
        )
        _convert_with_pandoc(final_proposal_path, final_dir, required_formats, basename="proposal_final")

        overall_elapsed = time.time() - overall_start
        _log_progress(logger, f"[PROPOSAL] Completed in {_format_duration(overall_elapsed)}.")

        return 0
    except Exception as exc:
        status = "error"
        error = str(exc)
        raise
    finally:
        finished_at = datetime.now(timezone.utc)
        if run_ctx is not None:
            models = {}
            if proposal_models:
                for key, value in proposal_models.items():
                    if value:
                        models[key] = value
            for role, role_llm in (
                ("llm1", llm1),
                ("llm2", llm2),
                ("llm3", llm3),
                ("llm4", llm4),
                ("llm5", llm5),
            ):
                if role_llm is not None:
                    models.setdefault(role, role_llm.config.model)

            unique_llms: list[OpenRouterLLM] = []
            seen = set()
            for role_llm in (llm_default, llm1, llm2, llm3, llm4, llm5):
                if role_llm is None:
                    continue
                llm_id = id(role_llm)
                if llm_id in seen:
                    continue
                seen.add(llm_id)
                unique_llms.append(role_llm)

            total_tokens = sum(llm_item.get_total_tokens() for llm_item in unique_llms)
            total_cost = 0.0
            cost_known = False
            for llm_item in unique_llms:
                cost_val = llm_item.get_total_cost_usd()
                if cost_val is not None:
                    total_cost += float(cost_val)
                    cost_known = True
            token_totals = {"primary": total_tokens}
            cost_totals = {"primary": total_cost if cost_known else None}
            _write_run_meta(
                run_ctx,
                args,
                started_at,
                finished_at,
                status,
                error,
                models=models,
                token_totals=token_totals,
                cost_totals_usd=cost_totals,
            )
