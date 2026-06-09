from __future__ import annotations

import re


_INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(ignore|disregard)\b.*\b(previous|above|system|instructions)\b"),
    re.compile(r"(?i)\byou are (chatgpt|an ai|a large language model)\b"),
    re.compile(r"(?i)\b(system prompt|developer message|tool call)\b"),
    re.compile(r"(?i)\bdo not follow\b"),
    re.compile(r"(?i)\bBEGIN (SYSTEM|INSTRUCTIONS)\b"),
]


def sanitize_context_text(text: str) -> str:
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        if any(pattern.search(line) for pattern in _INJECTION_PATTERNS):
            continue
        lines.append(line)
    return "\n".join(lines).strip()
