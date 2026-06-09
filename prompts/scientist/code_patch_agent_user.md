TASK
Propose a patch for iteration {iter_index} to improve metric "{metric_to_optimize}".

INPUTS
- Research plan:
{plan_json}

- Current results summary (baseline vs proposed if available):
{results_summary}

- Experiment codebase map (summaries):
{code_index}
Where code_index is:
{
  "files": [
    {"path": "run_experiment.py", "summary": "...", "functions": ["main", "..."], "size": 1234},
    ...
  ],
  "entrypoint": "{entrypoint}",
  "constraints": {"forbidden_imports": [...], "allowed_libraries": [...]}
}

- Optional reviewer requests:
{review_required_changes}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT CONTRACT (JSON ONLY)
{
  "patch_format": "unified_diff",
  "target_files": ["relative/path.py", "..."],
  "diff": "<<<UNIFIED DIFF TEXT>>>",
  "rationale": [
    "Why this change should improve metric and what risk it introduces"
  ],
  "expected_effect": {
    "metric": "{metric_to_optimize}",
    "direction": "increase|decrease|stabilize",
    "mechanism": "..."
  },
  "safety_notes": [
    "Affirm no forbidden imports or actions",
    "Affirm file paths are relative and inside workdir"
  ],
  "rollback_plan": "How to revert if regression",
  "retrieval_queries": ["... if needed"]
}

RULES
- Diff must apply cleanly. Use correct unified diff headers.
- Patch only files listed in code_index.
- If no safe improvement is apparent, return an empty diff and explain in rationale.
