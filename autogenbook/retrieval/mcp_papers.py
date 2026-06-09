from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from mcp_gateway import get_mcp_gateway

from .types import RetrievalItem


_SEARCH_TOOL_SPECS = [
    ("search_papers", "papers"),
    ("search_arxiv", "papers"),
    ("search_semantic", "papers"),
    ("search_pubmed", "papers"),
    ("search_crossref", "papers"),
    ("search_google_scholar", "papers"),
    ("search_iacr", "papers"),
    ("search_biorxiv", "papers"),
    ("search_medrxiv", "papers"),
]

ARXIV_MAX_RESULTS = 50
OPEN_SEARCH_MAX_RESULTS = 10
_VALID_CITE_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass
class MCPPaperRetriever:
    tool_preference: List[str] = field(default_factory=lambda: [name for name, _ in _SEARCH_TOOL_SPECS])
    _tool_names: Optional[set[str]] = None
    cache_dir: Optional[Path] = None
    cache_ttl_s: Optional[float] = None
    cache_max_files: Optional[int] = None

    def __post_init__(self) -> None:
        if self.cache_dir is None:
            env_dir = os.environ.get("AUTOGENBOOK_MCP_CACHE_DIR")
            if env_dir:
                self.cache_dir = Path(env_dir).expanduser().resolve()
            else:
                self.cache_dir = Path.cwd() / ".autogenbook_mcp_cache"
        if self.cache_ttl_s is None:
            raw = os.environ.get("AUTOGENBOOK_MCP_CACHE_TTL_S", "0")
            try:
                self.cache_ttl_s = float(raw)
            except Exception:
                self.cache_ttl_s = 0.0
        if self.cache_max_files is None:
            raw = os.environ.get("AUTOGENBOOK_MCP_CACHE_MAX_FILES", "500")
            try:
                self.cache_max_files = int(raw)
            except Exception:
                self.cache_max_files = 500
            if self.cache_max_files is not None and self.cache_max_files <= 0:
                self.cache_max_files = None
        if self.cache_dir is not None:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.cache_dir = None
        if self.cache_dir is not None and self.cache_max_files:
            _prune_cache(self.cache_dir, self.cache_max_files)

    def _available_tools(self) -> set[str]:
        if self._tool_names is not None:
            return self._tool_names
        gateway = get_mcp_gateway()
        try:
            tools = gateway.list_tools()
        except Exception:
            self._tool_names = set()
            return self._tool_names
        names = set()
        for tool in tools:
            name = tool.get("name") or tool.get("tool") or tool.get("id")
            if name:
                names.add(str(name))
        self._tool_names = names
        return self._tool_names

    def is_available(self) -> bool:
        available = self._available_tools()
        return any(name in available for name in self.tool_preference)

    def retrieve(self, query: str, k: int = 5) -> List[RetrievalItem]:
        items = self.retrieve_all(query, k=max(k, 5))
        ranked = _rank_items(items)
        return ranked[:k]

    def retrieve_all(self, query: str, k: int = 10) -> List[RetrievalItem]:
        gateway = get_mcp_gateway()
        tools = self._available_tools()
        if not tools:
            return []
        items: List[RetrievalItem] = []
        seen = set()
        for name in self.tool_preference:
            if name not in tools:
                continue
            payload = _cached_tool_call(
                gateway,
                name,
                _tool_args(name, query, k),
                cache_dir=self.cache_dir,
                ttl_s=self.cache_ttl_s or 0.0,
                max_files=self.cache_max_files,
            )
            papers = _extract_papers(payload)
            for item in _to_items(papers):
                key = item.cite_key or item.rid
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(item)
        return items


def _tool_max_results(name: str, k: int) -> int:
    if name in {"search_arxiv", "search_papers"}:
        return max(k, ARXIV_MAX_RESULTS)
    return max(k, OPEN_SEARCH_MAX_RESULTS)


def _tool_args(name: str, query: str, k: int) -> Dict[str, Any]:
    max_results = _tool_max_results(name, k)
    if name == "search_papers":
        return {"query": query, "max_results": max_results, "sort_by": "relevance"}
    if name in {"search_arxiv", "search_semantic", "search_pubmed"}:
        return {"query": query, "max_results": max_results}
    if name in {"search_iacr", "search_biorxiv", "search_medrxiv"}:
        return {"query": query, "max_results": max_results}
    if name == "search_google_scholar":
        return {"query": query, "max_results": max_results}
    if name == "search_crossref":
        return {"query": query, "max_results": max_results, "kwargs": ""}
    return {"query": query, "max_results": max_results}


def _call_tool(gateway: Any, name: str, args: Dict[str, Any]) -> Any:
    try:
        return gateway.call_tool(name, args)
    except Exception:
        return None


def _cache_key(name: str, args: Dict[str, Any]) -> str:
    payload = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=True)
    import hashlib

    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _cache_path(cache_dir: Optional[Path], name: str, args: Dict[str, Any]) -> Optional[Path]:
    if cache_dir is None:
        return None
    key = _cache_key(name, args)
    return cache_dir / f"{name}_{key}.json"


def _load_cached_payload(path: Path, ttl_s: float) -> Optional[Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    timestamp = raw.get("timestamp")
    if ttl_s > 0 and isinstance(timestamp, (int, float)):
        if (time.time() - float(timestamp)) > ttl_s:
            try:
                path.unlink()
            except Exception:
                pass
            return None
    return raw.get("payload")


def _save_cached_payload(path: Path, payload: Any) -> None:
    try:
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        payload_obj = json.loads(payload_json)
    except Exception:
        payload_obj = str(payload)
    record = {"timestamp": time.time(), "payload": payload_obj}
    try:
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _prune_cache(cache_dir: Path, max_files: int) -> None:
    if max_files <= 0:
        return
    try:
        entries = list(cache_dir.glob("*.json"))
    except Exception:
        return
    if len(entries) <= max_files:
        return
    try:
        entries.sort(key=lambda p: p.stat().st_mtime)
    except Exception:
        return
    for path in entries[: max(0, len(entries) - max_files)]:
        try:
            path.unlink()
        except Exception:
            continue


def _cached_tool_call(
    gateway: Any,
    name: str,
    args: Dict[str, Any],
    *,
    cache_dir: Optional[Path],
    ttl_s: float,
    max_files: Optional[int],
) -> Any:
    path = _cache_path(cache_dir, name, args)
    if path and path.exists():
        cached = _load_cached_payload(path, ttl_s)
        if cached is not None:
            return cached
    payload = _call_tool(gateway, name, args)
    if path is not None:
        _save_cached_payload(path, payload)
        if max_files:
            _prune_cache(path.parent, max_files)
    return payload


def _extract_papers(payload: Any) -> List[Dict[str, Any]]:
    data = _coerce_payload(payload)
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    if isinstance(data, dict):
        for key in ("papers", "results", "data", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return [p for p in val if isinstance(p, dict)]
    return []


def _coerce_payload(payload: Any) -> Any:
    if payload is None:
        return None
    if isinstance(payload, str):
        return _parse_json(payload) or payload
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "result" in payload:
            return _coerce_payload(payload.get("result"))
        if "content" in payload:
            content = payload.get("content")
            if isinstance(content, dict):
                if content.get("type") == "json" and "json" in content:
                    return content.get("json")
                if "json" in content:
                    return content.get("json")
                if "text" in content:
                    text = str(content.get("text") or "")
                    return _parse_json(text) or text
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "json" and "json" in item:
                        return item.get("json")
                    if isinstance(item, dict) and "json" in item:
                        return item.get("json")
                text = _content_text(content)
                return _parse_json(text) or text
            if isinstance(content, str):
                return _parse_json(content) or content
        return payload
    return payload


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
    return ""


def _parse_json(text: str) -> Any:
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    try:
        from utils import extract_first_json_array, extract_first_json_object

        if cleaned.startswith("["):
            return extract_first_json_array(cleaned)
        if cleaned.startswith("{"):
            return extract_first_json_object(cleaned)
    except Exception:
        return None
    return None


def _to_items(papers: List[Dict[str, Any]]) -> List[RetrievalItem]:
    items: List[RetrievalItem] = []
    seen = set()
    for paper in papers:
        title = str(paper.get("title") or paper.get("paper_title") or "").strip()
        if not title:
            continue
        authors = _normalize_authors(paper.get("authors") or paper.get("author"))
        year = _parse_year(paper.get("year") or paper.get("published") or paper.get("publication_year"))
        venue = _first_str(paper, ["venue", "journal", "publication", "booktitle"])
        doi = _first_str(paper, ["doi"])
        url = _first_str(paper, ["url", "pdf_url", "pdf", "abs_url", "link"])
        abstract = _first_str(paper, ["abstract", "summary", "description", "snippet"])
        arxiv_id = _normalize_arxiv_id(
            _first_str(paper, ["arxiv_id", "arxivId", "arxiv_id_v", "arxiv", "id"])
        )
        if not arxiv_id and url:
            arxiv_id = _normalize_arxiv_id(url)
        if not url and arxiv_id:
            url = f"https://arxiv.org/abs/{arxiv_id}"
        if not venue and arxiv_id:
            venue = "arXiv"
        rid = _make_rid(arxiv_id or doi or url or title)
        raw_key = (
            paper.get("cite_key")
            or paper.get("citation_key")
            or paper.get("citationKey")
            or paper.get("id")
            or paper.get("paper_id")
            or paper.get("paperId")
        )
        cite_key = ""
        if isinstance(raw_key, str) and raw_key.strip():
            candidate = raw_key.strip()
            if _VALID_CITE_KEY_RE.match(candidate):
                cite_key = candidate
        if not cite_key:
            cite_key = _make_cite_key(authors, year, title)
        if cite_key in seen:
            continue
        seen.add(cite_key)
        source = _source_from_url(url) or ("arXiv" if arxiv_id else "Paper Search")
        loc = str(venue or (year or "")).strip() or source
        items.append(
            RetrievalItem(
                rid=rid,
                kind="web",
                source=source,
                loc=loc,
                score=float(paper.get("score") or 1.0),
                cite_key=cite_key,
                text=abstract or title,
                url=url or None,
                title=title,
                authors=authors,
                year=year,
                venue=venue or None,
                doi=doi or None,
            )
        )
    return items


def _item_quality_score(item: RetrievalItem) -> float:
    score = float(item.score or 0.0)
    if item.doi:
        score += 0.6
    if item.year:
        score += 0.3
    if item.authors:
        score += 0.2
    if item.title:
        score += 0.1
    return score


def _rank_items(items: List[RetrievalItem]) -> List[RetrievalItem]:
    return sorted(items, key=_item_quality_score, reverse=True)


def items_from_tool_result(payload: Any) -> List[RetrievalItem]:
    """
    Convert raw MCP tool results to RetrievalItem list.
    """
    papers = _extract_papers(payload)
    return _to_items(papers)


def _normalize_authors(raw: Any) -> List[str]:
    if isinstance(raw, list):
        names = []
        for entry in raw:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("full_name") or entry.get("author")
                if not name:
                    given = entry.get("given") or entry.get("first") or entry.get("first_name")
                    family = entry.get("family") or entry.get("last") or entry.get("last_name")
                    parts = [str(p).strip() for p in [given, family] if p]
                    if parts:
                        name = " ".join(parts)
                if name:
                    names.append(str(name))
        return [n for n in names if n]
    if isinstance(raw, dict):
        name = raw.get("name") or raw.get("full_name") or raw.get("author")
        if not name:
            given = raw.get("given") or raw.get("first") or raw.get("first_name")
            family = raw.get("family") or raw.get("last") or raw.get("last_name")
            parts = [str(p).strip() for p in [given, family] if p]
            if parts:
                name = " ".join(parts)
        return [str(name)] if name else []
    if isinstance(raw, str):
        parts = [p.strip() for p in re.split(r",|;|\band\b", raw) if p.strip()]
        return parts
    return []


def _parse_year(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = re.findall(r"\d{4}", value)
        if digits:
            return int(digits[0])
    return None


def _first_str(data: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _looks_like_arxiv_id(value: str) -> bool:
    return bool(re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", value.strip()))


def _normalize_arxiv_id(value: str) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    lowered = cleaned.lower()
    if lowered.startswith("arxiv:"):
        cleaned = cleaned.split(":", 1)[1].strip()
    if "arxiv.org" in lowered:
        match = re.search(r"/(abs|pdf)/([^?#]+)", cleaned)
        if match:
            cleaned = match.group(2)
    cleaned = cleaned.replace(".pdf", "").strip()
    if _looks_like_arxiv_id(cleaned):
        return cleaned
    return ""


def _source_from_url(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc
    except Exception:
        return ""
    if "arxiv.org" in netloc:
        return "arXiv"
    return netloc


def _make_rid(seed: str) -> str:
    return f"RID:web:paper:{_hash_text(seed)}"


def _sanitize_key(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    return cleaned.strip("_") or "web_ref"


def _make_cite_key(authors: List[str], year: Optional[int], title: str) -> str:
    first_author = authors[0].split()[-1] if authors else "unknown"
    year_part = str(year) if year else "nd"
    slug = _sanitize_key(title)[:48]
    return f"web_{_sanitize_key(first_author)}_{year_part}_{slug}"


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
