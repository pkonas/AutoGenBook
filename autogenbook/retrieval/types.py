from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RetrievalItem:
    rid: str
    kind: str
    source: str
    loc: str
    score: float
    cite_key: str
    text: str
    url: Optional[str] = None
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None

    @property
    def source_id(self) -> str:
        return self.cite_key or self.rid
