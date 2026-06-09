from __future__ import annotations

import re
from typing import Dict, Set


_CITATION_TOKEN = re.compile(r"\bSRC:[A-Za-z0-9._:-]+\b|\bSRC-\d+\b")


def extract_markdown_citation_keys(markdown: str) -> Set[str]:
    keys: Set[str] = set()
    if not markdown:
        return keys
    for match in re.findall(r"\[([^\]]+)\]", markdown or ""):
        blob = match.strip()
        if not blob:
            continue
        for token in re.split(r"[\s,;]+", blob):
            token = token.strip()
            if token.startswith("SRC:") or token.startswith("SRC-"):
                keys.add(token)
    return keys


def normalize_markdown_citation_keys(markdown: str, key_map: Dict[str, str]) -> str:
    if not key_map:
        return markdown

    def _repl(match: re.Match) -> str:
        key = match.group(0)
        return key_map.get(key, key)

    return _CITATION_TOKEN.sub(_repl, markdown)
