from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List

from .types import AuditIssue, AuditIssueType, AuditSeverity


@dataclass
class AuditReport:
    doc_kind: str
    file_path: str
    counts_by_severity: Dict[str, int] = field(default_factory=dict)
    counts_by_type: Dict[str, int] = field(default_factory=dict)
    issues: List[AuditIssue] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    def to_json(self) -> Dict[str, object]:
        payload = {
            "doc_kind": self.doc_kind,
            "file_path": self.file_path,
            "counts_by_severity": dict(self.counts_by_severity),
            "counts_by_type": dict(self.counts_by_type),
            "issues": [self._issue_to_dict(issue) for issue in self.issues],
            "stats": dict(self.stats),
        }
        return payload

    @classmethod
    def from_json(cls, data: Dict[str, object]) -> "AuditReport":
        issues = []
        for raw in data.get("issues", []) if isinstance(data, dict) else []:
            if not isinstance(raw, dict):
                continue
            issues.append(
                AuditIssue(
                    issue_type=AuditIssueType(str(raw.get("issue_type", "unknown"))),
                    severity=AuditSeverity(str(raw.get("severity", "info"))),
                    message=str(raw.get("message", "")),
                    where=raw.get("where"),
                    snippet=raw.get("snippet"),
                    metadata=raw.get("metadata") or {},
                )
            )
        return cls(
            doc_kind=str(data.get("doc_kind", "")),
            file_path=str(data.get("file_path", "")),
            counts_by_severity=dict(data.get("counts_by_severity") or {}),
            counts_by_type=dict(data.get("counts_by_type") or {}),
            issues=issues,
            stats=dict(data.get("stats") or {}),
        )

    def update_counts(self) -> None:
        counts_by_severity: Dict[str, int] = {}
        counts_by_type: Dict[str, int] = {}
        for issue in self.issues:
            sev = issue.severity.value
            counts_by_severity[sev] = counts_by_severity.get(sev, 0) + 1
            typ = issue.issue_type.value
            counts_by_type[typ] = counts_by_type.get(typ, 0) + 1
        self.counts_by_severity = counts_by_severity
        self.counts_by_type = counts_by_type
        self._update_rates()

    def _update_rates(self) -> None:
        total_numeric = int(self.stats.get("n_numeric_claims", 0) or 0)
        missing_numeric = int(self.stats.get("n_numeric_claims_without_evidence", 0) or 0)
        total_cites = int(self.stats.get("n_cites_found", 0) or 0)
        unknown_cites = int(self.stats.get("n_unknown_cites", 0) or 0)
        if total_numeric > 0:
            self.stats["numeric_claim_support_rate"] = 1.0 - (missing_numeric / total_numeric)
        else:
            self.stats["numeric_claim_support_rate"] = 1.0
        if total_cites > 0:
            self.stats["cite_key_coverage_rate"] = 1.0 - (unknown_cites / total_cites)
        else:
            self.stats["cite_key_coverage_rate"] = 1.0

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _issue_to_dict(issue: AuditIssue) -> Dict[str, object]:
        return {
            "issue_type": issue.issue_type.value,
            "severity": issue.severity.value,
            "message": issue.message,
            "where": issue.where,
            "snippet": issue.snippet,
            "metadata": dict(issue.metadata),
        }
