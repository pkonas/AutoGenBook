from __future__ import annotations

import re
from typing import Dict, List, Set


_MATH_ENV_NAMES = (
    "equation",
    "equation*",
    "align",
    "align*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "eqnarray",
    "eqnarray*",
)


def extract_cite_keys(tex: str) -> Set[str]:
    keys: Set[str] = set()
    pattern = re.compile(
        r"\\cite[a-zA-Z]*\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}",
        re.MULTILINE,
    )
    for match in pattern.finditer(tex):
        raw = match.group(1)
        for key in raw.split(","):
            cleaned = key.strip()
            if cleaned:
                keys.add(cleaned)
    return keys


def extract_source_rids(tex: str) -> Set[str]:
    rids: Set[str] = set()
    footnote_pattern = re.compile(r"\\footnote\{(.*?)\}", re.DOTALL)
    rid_pattern = re.compile(r"RID:[^\s\}]+")
    for match in footnote_pattern.finditer(tex):
        content = match.group(1)
        if "Source:" not in content:
            continue
        for rid in rid_pattern.findall(content):
            rids.add(rid)
    inline_pattern = re.compile(r"Source:\s*(RID:[^\s\}]+)")
    for rid in inline_pattern.findall(tex):
        rids.add(rid)
    return rids


def extract_includegraphics_files(tex: str) -> List[str]:
    pattern = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
    return [match.group(1).strip() for match in pattern.finditer(tex) if match.group(1).strip()]


def extract_table_and_figure_refs(tex: str) -> Dict[str, Set[str]]:
    refs_fig = set(re.findall(r"\\ref\{(fig:[^}]+)\}", tex))
    refs_tab = set(re.findall(r"\\ref\{(tab:[^}]+)\}", tex))
    labels_fig = set(re.findall(r"\\label\{(fig:[^}]+)\}", tex))
    labels_tab = set(re.findall(r"\\label\{(tab:[^}]+)\}", tex))
    return {
        "refs_fig": refs_fig,
        "labels_fig": labels_fig,
        "refs_tab": refs_tab,
        "labels_tab": labels_tab,
    }


def extract_numeric_claim_spans(tex: str) -> List[Dict[str, object]]:
    masked = _mask_math_regions(tex)
    spans: List[Dict[str, object]] = []
    num_pattern = re.compile(
        r"(?<![A-Za-z\\])(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?%?)",
        re.IGNORECASE,
    )
    for match in num_pattern.finditer(masked):
        start, end = match.span(1)
        number_str = match.group(1)
        if _looks_like_section_number(masked, start):
            continue
        context_start = max(0, start - 40)
        context_end = min(len(tex), end + 40)
        span = {
            "start": start,
            "end": end,
            "number_str": number_str,
            "text_snippet": tex[start:end],
            "context_window": tex[context_start:context_end],
        }
        spans.append(span)
    return spans


def _mask_math_regions(tex: str) -> str:
    masked = tex
    patterns = [
        r"\$\$.*?\$\$",
        r"\$.*?\$",
        r"\\\((.*?)\\\)",
        r"\\\[(.*?)\\\]",
    ]
    for env in _MATH_ENV_NAMES:
        patterns.append(rf"\\begin\{{{re.escape(env)}\}}.*?\\end\{{{re.escape(env)}\}}")
    for pattern in patterns:
        masked = _replace_with_spaces(masked, pattern)
    return masked


def _replace_with_spaces(text: str, pattern: str) -> str:
    regex = re.compile(pattern, re.DOTALL)
    parts = []
    last_end = 0
    for match in regex.finditer(text):
        parts.append(text[last_end:match.start()])
        parts.append(" " * (match.end() - match.start()))
        last_end = match.end()
    parts.append(text[last_end:])
    return "".join(parts)


def _looks_like_section_number(text: str, pos: int) -> bool:
    prefix = text[max(0, pos - 40) : pos]
    for cmd in ("\\section{", "\\subsection{", "\\subsubsection{", "\\chapter{", "\\paragraph{", "\\subparagraph{"):
        idx = prefix.rfind(cmd)
        if idx != -1 and "}" not in prefix[idx:]:
            return True
    for cmd in ("\\label{", "\\ref{", "\\cite{", "\\citep{", "\\citet{"):
        idx = prefix.rfind(cmd)
        if idx != -1 and "}" not in prefix[idx:]:
            return True
    return False
