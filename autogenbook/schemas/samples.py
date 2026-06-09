from __future__ import annotations

from typing import Any, Dict


SAMPLES: Dict[str, Dict[str, Any]] = {
    "evidence_ref": {"rid": "RID:kb:demo:loc:chunk1", "cite_key": "kb_demo_loc_chunk1"},
    "idea": {
        "ideas": [
            {
                "idea_id": "I1",
                "title": "Toy idea",
                "one_liner": "One line",
                "hypothesis": "Hypothesis",
                "core_mechanism": "Mechanism",
                "what_is_new": "Novelty",
                "why_it_might_work": "Rationale",
                "minimal_experiment": {
                    "design": "Design",
                    "expected_signal": "Signal",
                    "metric": "accuracy",
                    "success_criteria": "Improve accuracy",
                },
                "ablations": ["A1"],
                "risks": ["R1"],
                "safety_ethics": ["S1"],
                "estimated_effort": {"time_minutes": 60, "complexity": "low"},
                "retrieval_queries": ["query"],
            }
        ],
        "selection_rubric": {
            "novelty_weight": 0.3,
            "testability_weight": 0.3,
            "impact_weight": 0.2,
            "risk_weight": 0.2,
        },
    },
    "literature": {
        "related_work": [
            {
                "cite_key": "web_demo_2024",
                "title": "Related Paper",
                "year": "2024",
                "key_takeaways": ["Takeaway"],
                "closest_overlap": "Overlap",
                "difference": "Difference",
                "evidence_rids": ["RID:web:demo:paper1"],
            }
        ],
        "novelty_risk": {
            "level": "medium",
            "reasons": ["Reason"],
            "confounders": ["Confounder"],
            "pivot_suggestions": ["Pivot"],
        },
        "positioning_statement": {
            "problem_gap": "Gap",
            "our_angle": "Angle",
            "why_now": "Now",
            "evidence_rids": ["RID:web:demo:paper1"],
        },
        "must_cite": ["web_demo_2024"],
        "retrieval_queries": ["query"],
    },
    "plan": {
        "selected_idea_id": "I1",
        "title": "Paper Title",
        "problem_statement": {"text": "Problem", "evidence_rids": ["RID:kb:demo:loc:chunk1"]},
        "hypothesis": {"text": "Hypothesis", "evidence_rids": ["RID:kb:demo:loc:chunk1"]},
        "method_proposal": {
            "name": "Method",
            "high_level_description": "Desc",
            "algorithm_sketch": ["Step 1"],
            "expected_failure_modes": ["Mode"],
            "evidence_rids": ["RID:kb:demo:loc:chunk1"],
        },
        "experiment_matrix": [
            {
                "exp_id": "E1",
                "purpose": "baseline",
                "conditions": {"param": "value"},
                "metrics": ["accuracy"],
                "success_criteria": "Improve",
                "runtime_budget_minutes": 60,
            }
        ],
        "ablations": [
            {
                "ablation_id": "A1",
                "what_removed": "Feature",
                "expected_effect": "Decrease",
                "metrics": ["accuracy"],
            }
        ],
        "stopping_rules": ["Stop if regress"],
        "paper_outline_graph": {
            "root": "paper",
            "nodes": [
                {"key": "paper", "title": "Paper", "summary": "Root"},
                {"key": "1", "title": "Intro", "summary": "Intro summary"},
            ],
            "edges": [("paper", "1")],
        },
        "retrieval_queries": ["query"],
    },
    "code_patch": {
        "patch_format": "unified_diff",
        "target_files": [],
        "diff": "",
        "rationale": ["No safe change identified."],
        "expected_effect": {"metric": "accuracy", "direction": "stabilize", "mechanism": "No change"},
        "safety_notes": ["No forbidden imports."],
        "rollback_plan": "No changes applied.",
        "retrieval_queries": [],
    },
    "analyze": {
        "summary": "Summary",
        "claims": [
            {
                "claim_id": "C1",
                "text": "Claim",
                "type": "result",
                "evidence_rids": ["RID:run:demo:metrics.json"],
                "confidence": "high",
            }
        ],
        "comparisons": [
            {
                "baseline": "E1",
                "proposed": "E2",
                "metric": "accuracy",
                "baseline_value": 0.8,
                "proposed_value": 0.85,
                "delta": 0.05,
                "evidence_rids": ["RID:run:demo:metrics.json"],
            }
        ],
        "figure_plan": [
            {
                "figure_id": "F1",
                "filename": "fig.png",
                "plot_type": "line",
                "data_source": "metrics.json",
                "caption": "Caption",
                "evidence_rids": ["RID:run:demo:metrics.json"],
            }
        ],
        "latex_table_snippets": [
            {
                "table_id": "T1",
                "caption": "Table",
                "latex": "\\begin{tabular}...\\end{tabular}",
                "evidence_rids": ["RID:run:demo:metrics.json"],
            }
        ],
        "limitations": [
            {
                "text": "Limitation",
                "evidence_rids": ["RID:run:demo:metrics.json"],
                "severity": "low",
            }
        ],
        "next_actions": ["Next action"],
        "retrieval_queries": ["query"],
    },
    "review": {
        "overall_summary": "Summary",
        "scores": {
            "novelty": 5,
            "technical_quality": 5,
            "clarity": 5,
            "evidence_grounding": 5,
            "reproducibility": 5,
        },
        "strengths": ["Strength"],
        "weaknesses": ["Weakness"],
        "major_issues": [
            {
                "issue": "Issue",
                "why_it_matters": "Matters",
                "where": "Section 1",
                "required_change": "Fix",
                "severity": "major",
            }
        ],
        "missing_experiments": ["Exp"],
        "citation_problems": ["Problem"],
        "suggested_revisions": [{"target": "Section 1", "instruction": "Revise", "priority": 3}],
        "verdict": "borderline",
    },
    "book_section_review": {
        "ok_to_keep": True,
        "issues": [
            {
                "type": "clarity",
                "severity": "minor",
                "description": "Desc",
                "required_fix": "Fix",
            }
        ],
        "suggested_edits": [{"target": "Para 1", "edit_instruction": "Edit"}],
        "retrieval_queries": ["query"],
    },
    "context_memory_update": {
        "terms_added": [{"term": "Term", "definition": "Def", "first_seen_node": "1"}],
        "terms_updated": [
            {"term": "Term", "old_definition": "Old", "new_definition": "New", "reason": "Clarify"}
        ],
        "citations_used": [
            {"cite_key_or_rid": "RID:kb:demo:loc:chunk1", "where": "Sentence", "type": "kb"}
        ],
        "open_threads": [{"thread": "Thread", "suggested_future_node": "2"}],
        "consistency_flags": [{"type": "terminology", "description": "Flag", "severity": "low"}],
        "retrieval_queries": ["query"],
    },
    "subdivide": {
        "items": [
            {
                "title": "Part A",
                "summary": "Summary",
                "n_pages": 1.0,
                "needsSubdivision": False,
            }
        ]
    },
}

