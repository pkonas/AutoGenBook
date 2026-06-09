from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Set

from .evidence_rules import EvidenceWindowConfig, numeric_claim_has_evidence
from .latex_extract import (
    extract_cite_keys,
    extract_includegraphics_files,
    extract_numeric_claim_spans,
    extract_source_rids,
    extract_table_and_figure_refs,
)
from .report import AuditReport
from .types import AuditIssue, AuditIssueType, AuditSeverity


@dataclass
class AuditorConfig:
    enabled: bool = True
    mode: str = "warn"
    check_unknown_cites: bool = True
    check_missing_fig_files: bool = True
    check_numeric_claims: bool = True
    evidence_window_chars: int = 600
    allowlist_cite_keys: List[str] = field(default_factory=list)
    allowlist_rids: List[str] = field(default_factory=list)


def audit_latex(
    tex_path: Path,
    tex_text: str,
    *,
    doc_kind: str,
    known_cite_keys: Set[str],
    known_rids: Set[str],
    project_root: Path,
    config: AuditorConfig,
    run_artifact_paths: dict[str, Path] | None = None,
) -> AuditReport:
    report = AuditReport(doc_kind=doc_kind, file_path=str(tex_path))
    if not config.enabled or config.mode == "off":
        return report

    allow_cites = set(config.allowlist_cite_keys)
    allow_rids = set(config.allowlist_rids)

    cite_keys = extract_cite_keys(tex_text)
    source_rids = extract_source_rids(tex_text)
    figures = extract_includegraphics_files(tex_text)
    refs = extract_table_and_figure_refs(tex_text)
    numeric_spans = extract_numeric_claim_spans(tex_text)

    report.stats.update(
        {
            "n_cites_found": len(cite_keys),
            "n_unknown_cites": 0,
            "n_numeric_claims": len(numeric_spans),
            "n_numeric_claims_without_evidence": 0,
            "n_figures_referenced": len(figures),
            "n_figures_missing": 0,
        }
    )

    if config.check_unknown_cites:
        unknown_cites = {k for k in cite_keys if k not in known_cite_keys and k not in allow_cites}
        for key in sorted(unknown_cites):
            report.issues.append(
                AuditIssue(
                    issue_type=AuditIssueType.UNKNOWN_CITE_KEY,
                    severity=AuditSeverity.ERROR,
                    message=f"Citation key '{key}' not present in known citations.",
                    snippet=f"\\cite{{{key}}}",
                    metadata={"cite_key": key},
                )
            )
        report.stats["n_unknown_cites"] = len(unknown_cites)

    if source_rids:
        unknown_rids = {r for r in source_rids if r not in known_rids and r not in allow_rids}
        if unknown_rids:
            severity = AuditSeverity.ERROR if config.mode == "strict" else AuditSeverity.WARNING
            for rid in sorted(unknown_rids):
                report.issues.append(
                    AuditIssue(
                        issue_type=AuditIssueType.UNKNOWN_RID,
                        severity=severity,
                        message=f"RID '{rid}' not present in known RIDs.",
                        snippet=rid,
                        metadata={"rid": rid},
                    )
                )

    if run_artifact_paths:
        for rid in sorted(source_rids):
            path = run_artifact_paths.get(rid)
            if path is None:
                continue
            if not path.exists():
                severity = AuditSeverity.ERROR if config.mode == "strict" else AuditSeverity.WARNING
                report.issues.append(
                    AuditIssue(
                        issue_type=AuditIssueType.MISSING_RUN_ARTIFACT,
                        severity=severity,
                        message=f"Run artifact missing for {rid}.",
                        snippet=str(path),
                        metadata={"rid": rid, "path": str(path)},
                    )
                )

    if config.check_missing_fig_files and figures:
        missing = []
        tex_dir = tex_path.parent
        for fig in figures:
            candidate = (tex_dir / fig).resolve()
            if candidate.exists():
                continue
            candidate = (project_root / fig).resolve()
            if not candidate.exists():
                missing.append(fig)
        for fig in missing:
            report.issues.append(
                AuditIssue(
                    issue_type=AuditIssueType.FIGURE_FILE_MISSING,
                    severity=AuditSeverity.ERROR,
                    message=f"Figure file not found: {fig}",
                    snippet=fig,
                    metadata={"figure_path": fig},
                )
            )
        report.stats["n_figures_missing"] = len(missing)

    if refs["refs_fig"]:
        missing_fig_labels = sorted(refs["refs_fig"] - refs["labels_fig"])
        for label in missing_fig_labels:
            report.issues.append(
                AuditIssue(
                    issue_type=AuditIssueType.TABLE_REF_MISSING,
                    severity=AuditSeverity.WARNING,
                    message=f"Reference to '{label}' has no matching label.",
                    snippet=f"\\ref{{{label}}}",
                    metadata={"label": label},
                )
            )

    if refs["refs_tab"]:
        missing_tab_labels = sorted(refs["refs_tab"] - refs["labels_tab"])
        for label in missing_tab_labels:
            report.issues.append(
                AuditIssue(
                    issue_type=AuditIssueType.TABLE_REF_MISSING,
                    severity=AuditSeverity.WARNING,
                    message=f"Reference to '{label}' has no matching label.",
                    snippet=f"\\ref{{{label}}}",
                    metadata={"label": label},
                )
            )

    if config.check_numeric_claims and numeric_spans:
        ev_cfg = EvidenceWindowConfig(window_chars=config.evidence_window_chars)
        missing_count = 0
        for span in numeric_spans:
            if numeric_claim_has_evidence(tex_text, span, ev_cfg):
                continue
            missing_count += 1
            line = _line_for_offset(tex_text, int(span.get("start", 0)))
            severity = AuditSeverity.ERROR if config.mode == "strict" else AuditSeverity.WARNING
            report.issues.append(
                AuditIssue(
                    issue_type=AuditIssueType.NUMERIC_CLAIM_NO_EVIDENCE,
                    severity=severity,
                    message="Numeric claim without nearby evidence marker.",
                    where=f"line {line}",
                    snippet=str(span.get("context_window", "")),
                    metadata={"number": str(span.get("number_str", ""))},
                )
            )
        report.stats["n_numeric_claims_without_evidence"] = missing_count

    report.update_counts()
    return report


def _line_for_offset(text: str, offset: int) -> int:
    if offset <= 0:
        return 1
    return text.count("\n", 0, min(offset, len(text))) + 1
