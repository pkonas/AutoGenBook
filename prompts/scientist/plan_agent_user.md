TASK
Select and refine ONE idea into a concrete research plan.

INPUT
- Candidate ideas JSON:
{ideas_json}

- Literature/novelty analysis (may be empty if web rag disabled):
{literature_json}

Constraints
- compute_budget: {compute_budget}
- max_iterations: {max_iters}
- metric_to_optimize: {metric_to_optimize}
- template_name: {template_name}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT CONTRACT (JSON ONLY)
{
  "selected_idea_id": "I#",
  "title": "...(paper title candidate)...",
  "problem_statement": {
    "text": "...",
    "evidence_rids": ["RID:..."]
  },
  "hypothesis": {
    "text": "...",
    "evidence_rids": ["RID:..."]
  },
  "method_proposal": {
    "name": "...",
    "high_level_description": "...",
    "algorithm_sketch": [
      "Step 1 ...",
      "Step 2 ..."
    ],
    "expected_failure_modes": ["..."],
    "evidence_rids": ["RID:..."]
  },
  "experiment_matrix": [
    {
      "exp_id": "E1",
      "purpose": "baseline",
      "conditions": {"param": "value"},
      "metrics": ["{metric_to_optimize}", "..."],
      "success_criteria": "...",
      "runtime_budget_minutes": 0
    },
    {
      "exp_id": "E2",
      "purpose": "proposed method",
      "conditions": {"param": "value"},
      "metrics": ["{metric_to_optimize}", "..."],
      "success_criteria": "...",
      "runtime_budget_minutes": 0
    }
  ],
  "ablations": [
    {
      "ablation_id": "A1",
      "what_removed": "...",
      "expected_effect": "...",
      "metrics": ["..."]
    }
  ],
  "stopping_rules": [
    "Stop if runtime exceeds budget",
    "Stop if no improvement after N iterations",
    "Stop if metrics regress"
  ],
  "paper_outline_graph": {
    "root": "paper",
    "nodes": [
      {"key": "paper", "title": "...", "summary": "..."},
      {"key": "1", "title": "Introduction", "summary": "..."},
      {"key": "2", "title": "Related Work", "summary": "..."},
      {"key": "3", "title": "Method", "summary": "..."},
      {"key": "4", "title": "Experiments", "summary": "..."},
      {"key": "5", "title": "Results", "summary": "..."},
      {"key": "6", "title": "Limitations", "summary": "..."},
      {"key": "7", "title": "Conclusion", "summary": "..."}
    ],
    "edges": [["paper","1"],["paper","2"],["paper","3"],["paper","4"],["paper","5"],["paper","6"],["paper","7"]]
  },
  "retrieval_queries": ["..."]
}

RULES
- Be conservative: if novelty is uncertain, include a Limitations section emphasis.
- Ensure the plan is executable in the template environment.
