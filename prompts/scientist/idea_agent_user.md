TASK
Generate {n_ideas} candidate research ideas for the given domain/template.

INPUT CONTEXT
- Domain / Template: {template_name}
- High-level goal from user: {user_goal}
- Constraints:
  - compute_budget: {compute_budget}  (e.g., "CPU-only, <5 minutes per run")
  - max_iterations: {max_iters}
  - allowed_libraries: {allowed_libraries}
  - forbidden_actions: {forbidden_actions}
- Evaluation metric preference: {metric_to_optimize}
- Existing baseline description (if any): {baseline_desc}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT CONTRACT (JSON ONLY)
Return a JSON object:
{
  "ideas": [
    {
      "idea_id": "I1",
      "title": "...",
      "one_liner": "...",
      "hypothesis": "...",
      "core_mechanism": "...",
      "what_is_new": "...",
      "why_it_might_work": "...",
      "minimal_experiment": {
        "design": "...",
        "expected_signal": "...",
        "metric": "{metric_to_optimize}",
        "success_criteria": "..."
      },
      "ablations": ["...", "..."],
      "risks": ["...", "..."],
      "safety_ethics": ["..."],
      "estimated_effort": {
        "time_minutes": 0,
        "complexity": "low|medium|high"
      },
      "retrieval_queries": ["query1", "query2"]
    }
  ],
  "selection_rubric": {
    "novelty_weight": 0.0,
    "testability_weight": 0.0,
    "impact_weight": 0.0,
    "risk_weight": 0.0
  }
}

RULES
- Every idea must be testable in the given template.
- If retrieved excerpts already contain a near-identical idea, mark "what_is_new" as "unclear" and increase risks.
- Include retrieval_queries that would help check novelty or implementation details.
