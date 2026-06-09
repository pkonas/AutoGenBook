from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AuditIssueType(str, Enum):
    UNKNOWN_CITE_KEY = "unknown_cite_key"
    MISSING_BIB_ENTRY = "missing_bib_entry"
    UNKNOWN_RID = "unknown_rid"
    MISSING_RUN_ARTIFACT = "missing_run_artifact"
    NUMERIC_CLAIM_NO_EVIDENCE = "numeric_claim_no_evidence"
    FIGURE_FILE_MISSING = "figure_file_missing"
    TABLE_REF_MISSING = "table_ref_missing"
    LATEX_COMPILE_RISK = "latex_compile_risk"


@dataclass
class AuditIssue:
    issue_type: AuditIssueType
    severity: AuditSeverity
    message: str
    where: Optional[str] = None
    snippet: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)
