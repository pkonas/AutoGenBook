from __future__ import annotations

import re
from typing import Set, Tuple


_CITE_PATTERN = re.compile(r"\\cite[a-zA-Z*]*(?:\[[^\]]*\]\s*)*\{([^}]+)\}")
_FOOTNOTE_PATTERN = re.compile(r"\\footnote\{(.*?)\}", re.S)
_RID_PATTERN = re.compile(r"RID:[A-Za-z0-9:_\-\.]+")
_SOURCE_TOKEN_PATTERN = re.compile(r"Source:\s*([^\s}]+)")


def extract_citations(latex: str) -> Tuple[Set[str], Set[str]]:
    cite_keys: Set[str] = set()
    rids: Set[str] = set()

    for match in _CITE_PATTERN.finditer(latex):
        keys = match.group(1)
        for key in re.split(r"\s*[,;]\s*", keys):
            k = key.strip()
            if k:
                cite_keys.add(k)

    for match in _FOOTNOTE_PATTERN.finditer(latex):
        body = match.group(1)
        for rid in _RID_PATTERN.findall(body):
            rids.add(rid)
        for token in _SOURCE_TOKEN_PATTERN.findall(body):
            if token.startswith("RID:"):
                rids.add(token)
            else:
                cite_keys.add(token)

    return cite_keys, rids
