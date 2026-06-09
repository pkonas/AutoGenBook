from __future__ import annotations

from typing import Literal, Optional

from pydantic import StrictStr

from .base import StrictBaseModel


class TermAdded(StrictBaseModel):
    term: StrictStr
    definition: StrictStr
    first_seen_node: StrictStr


class TermUpdated(StrictBaseModel):
    term: StrictStr
    old_definition: StrictStr
    new_definition: StrictStr
    reason: StrictStr


class CitationUsed(StrictBaseModel):
    cite_key_or_rid: StrictStr
    where: StrictStr
    type: Literal["kb", "web", "run"]


class OpenThread(StrictBaseModel):
    thread: StrictStr
    suggested_future_node: Optional[StrictStr] = None


class ConsistencyFlag(StrictBaseModel):
    type: Literal["terminology", "notation", "claim"]
    description: StrictStr
    severity: Literal["low", "medium", "high"]


class ContextMemoryUpdateOutput(StrictBaseModel):
    terms_added: list[TermAdded]
    terms_updated: list[TermUpdated]
    citations_used: list[CitationUsed]
    open_threads: list[OpenThread]
    consistency_flags: list[ConsistencyFlag]
    retrieval_queries: list[StrictStr]
