from __future__ import annotations

import json
from pathlib import Path

from .report import AuditReport
from .types import AuditIssue, AuditIssueType, AuditSeverity


def main() -> None:
    report = AuditReport(
        doc_kind="paper",
        file_path=str(Path("out") / "paper.tex"),
        stats={
            "n_cites_found": 2,
            "n_unknown_cites": 1,
            "n_numeric_claims": 3,
            "n_numeric_claims_without_evidence": 1,
            "n_figures_referenced": 1,
            "n_figures_missing": 1,
        },
    )
    report.issues.append(
        AuditIssue(
            issue_type=AuditIssueType.UNKNOWN_CITE_KEY,
            severity=AuditSeverity.WARNING,
            message="Citation key not found in bibliography.",
            where="Section 2",
            snippet="\\cite{missing2024}",
            metadata={"cite_key": "missing2024"},
        )
    )
    report.update_counts()
    print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
