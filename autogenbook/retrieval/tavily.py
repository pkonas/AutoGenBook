from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from .types import RetrievalItem


def search_web(query: str, limit: int = 5, api_key: Optional[str] = None) -> List[dict]:
    if not api_key:
        return []
    try:
        import requests  # type: ignore
    except Exception:
        return []

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    return list(data.get("results", []) or [])


@dataclass
class TavilyRetriever:
    api_key: Optional[str] = None

    def retrieve(self, query: str, k: int = 5) -> List[RetrievalItem]:
        items: List[RetrievalItem] = []
        for result in search_web(query, limit=k, api_key=self.api_key):
            title = result.get("title") or "Untitled"
            url_val = result.get("url") or ""
            content = result.get("content") or result.get("raw_content") or ""
            score = float(result.get("score") or 1.0)
            domain = _domain_from_url(url_val) or "Tavily"
            rid = _make_rid(url_val or title)
            cite_key = _make_cite_key(title)
            items.append(
                RetrievalItem(
                    rid=rid,
                    kind="web",
                    source="Tavily Search",
                    loc=domain,
                    score=score,
                    cite_key=cite_key,
                    text=content,
                    url=url_val or None,
                    title=title,
                )
            )
        return items


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except Exception:
        return ""


def _sanitize_key(text: str) -> str:
    import re

    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    return cleaned.strip("_") or "web_ref"


def _make_rid(seed: str) -> str:
    return f"RID:web:tavily:{_hash_text(seed)}"


def _make_cite_key(title: str) -> str:
    slug = _sanitize_key(title)[:64]
    return f"web_tavily_{slug}" if slug else "web_tavily_ref"


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
