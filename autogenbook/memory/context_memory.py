from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ContextMemory:
    terms: Dict[str, Dict[str, str]] = field(default_factory=dict)
    citations: List[Dict[str, str]] = field(default_factory=list)
    figures: List[Dict[str, str]] = field(default_factory=list)
    tables: List[Dict[str, str]] = field(default_factory=list)
    open_threads: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "ContextMemory":
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                return cls(
                    terms=data.get("terms", {}),
                    citations=data.get("citations", []),
                    figures=data.get("figures", []),
                    tables=data.get("tables", []),
                    open_threads=data.get("open_threads", []),
                )
            except Exception:
                pass
        return cls()

    def save(self, path: Path) -> None:
        payload = {
            "terms": self.terms,
            "citations": self.citations,
            "figures": self.figures,
            "tables": self.tables,
            "open_threads": self.open_threads,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def compact_summary(self, max_terms: int = 6, max_citations: int = 6, max_threads: int = 4) -> str:
        term_items = list(self.terms.items())[-max_terms:]
        citation_items = self.citations[-max_citations:]
        thread_items = self.open_threads[-max_threads:]

        terms = "\n".join(
            [f"- {label}: {meta.get('definition','')} (first: {meta.get('first_seen_node','')})" for label, meta in term_items]
        )
        citations = "\n".join(
            [f"- {c.get('source_id','')} (node: {c.get('node','')})" for c in citation_items]
        )
        threads = "\n".join(
            [f"- {t.get('issue','')} (node: {t.get('node','')})" for t in thread_items]
        )
        if not terms:
            terms = "(none)"
        if not citations:
            citations = "(none)"
        if not threads:
            threads = "(none)"
        return (
            "TERMS:\n"
            f"{terms}\n\n"
            "CITATIONS:\n"
            f"{citations}\n\n"
            "OPEN THREADS:\n"
            f"{threads}"
        )

    def summarize_excerpt(self, max_chars: int = 2000) -> str:
        summary = self.compact_summary()
        if len(summary) <= max_chars:
            return summary
        return summary[: max(0, max_chars - 3)] + "..."

    def update_from_review(
        self,
        review: Dict[str, Any],
        node_key: str,
        section_title: str,
        section_tex: str,
    ) -> None:
        for term in review.get("terms", []) if isinstance(review, dict) else []:
            label = str(term.get("label", "")).strip()
            definition = str(term.get("definition", "")).strip()
            if not label or not definition:
                continue
            if label not in self.terms:
                self.terms[label] = {"definition": definition, "first_seen_node": node_key}

        for cite in review.get("citations", []) if isinstance(review, dict) else []:
            source_id = str(cite.get("source_id", "")).strip()
            if not source_id:
                continue
            if not any(c.get("source_id") == source_id for c in self.citations):
                self.citations.append({"source_id": source_id, "node": node_key, "section": section_title})

        for fig in review.get("figures", []) if isinstance(review, dict) else []:
            label = str(fig.get("label", "")).strip()
            caption = str(fig.get("caption", "")).strip()
            if label:
                self.figures.append({"label": label, "caption": caption, "node": node_key})

        for tbl in review.get("tables", []) if isinstance(review, dict) else []:
            label = str(tbl.get("label", "")).strip()
            caption = str(tbl.get("caption", "")).strip()
            if label:
                self.tables.append({"label": label, "caption": caption, "node": node_key})

        for issue in review.get("open_threads", []) if isinstance(review, dict) else []:
            issue_text = str(issue).strip()
            if issue_text:
                self.open_threads.append({"issue": issue_text, "node": node_key})

        self._extract_citations_from_tex(section_tex, node_key, section_title)

    def apply_agent_update(self, update: Dict[str, Any], node_key: str, section_title: str) -> None:
        if not isinstance(update, dict):
            return

        for term in update.get("terms_added", []):
            label = str(term.get("term", "")).strip()
            definition = str(term.get("definition", "")).strip()
            if label and definition and label not in self.terms:
                self.terms[label] = {"definition": definition, "first_seen_node": node_key}

        for term in update.get("terms_updated", []):
            label = str(term.get("term", "")).strip()
            new_definition = str(term.get("new_definition", "")).strip()
            if label and new_definition:
                self.terms[label] = {"definition": new_definition, "first_seen_node": node_key}

        for cite in update.get("citations_used", []):
            source_id = str(cite.get("cite_key_or_rid", "")).strip()
            if not source_id:
                continue
            if not any(c.get("source_id") == source_id for c in self.citations):
                self.citations.append({"source_id": source_id, "node": node_key, "section": section_title})

        for thread in update.get("open_threads", []):
            issue = str(thread.get("thread", "")).strip()
            if issue:
                self.open_threads.append({"issue": issue, "node": node_key})

    def _extract_citations_from_tex(self, text: str, node_key: str, section_title: str) -> None:
        matches = re.findall(r"\\footnote\{Source:\s*([^}]+)\}", text)
        for match in matches:
            source_id = match.strip()
            if not source_id:
                continue
            if not any(c.get("source_id") == source_id for c in self.citations):
                self.citations.append({"source_id": source_id, "node": node_key, "section": section_title})
