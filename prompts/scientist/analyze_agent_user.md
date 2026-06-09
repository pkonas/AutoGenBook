TASK
Analyze the latest experiment run artifacts and produce evidence-backed claims and a figure/table plan.

RUN ARTIFACTS (authoritative)
- metrics_json: {metrics_json}
- optional history_csv_head: {history_csv_head}
- stdout_tail: {stdout_tail}
- stderr_tail: {stderr_tail}
- produced_files: {produced_files_list}

CONTEXT
- Research plan: {plan_json}
- Prior iteration summary (if any): {prior_iteration_summary}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT CONTRACT (JSON ONLY)
{
  "summary": "2-4 sentences grounded in run artifacts",
  "claims": [
    {
      "claim_id": "C1",
      "text": "Precise claim with numbers if available",
      "type": "result|comparison|observation|limitation",
      "evidence_rids": ["RID:run:...", "RID:kb:..."],
      "confidence": "high|medium|low"
    }
  ],
  "comparisons": [
    {
      "baseline": "E1",
      "proposed": "E2",
      "metric": "{metric_to_optimize}",
      "baseline_value": 0.0,
      "proposed_value": 0.0,
      "delta": 0.0,
      "evidence_rids": ["RID:run:..."]
    }
  ],
  "figure_plan": [
    {
      "figure_id": "F1",
      "filename": "fig_accuracy.png",
      "plot_type": "line|bar|scatter|table",
      "data_source": "metrics.json|history.csv",
      "caption": "Caption text with cite placeholders if needed",
      "evidence_rids": ["RID:run:..."]
    }
  ],
  "latex_table_snippets": [
    {
      "table_id": "T1",
      "caption": "...",
      "latex": "\\begin{tabular}...\\end{tabular}",
      "evidence_rids": ["RID:run:..."]
    }
  ],
  "limitations": [
    {
      "text": "Limitation grounded in artifacts or constraints",
      "evidence_rids": ["RID:run:..."],
      "severity": "low|medium|high"
    }
  ],
  "next_actions": [
    "If improvement uncertain: propose what evidence is missing"
  ],
  "retrieval_queries": ["... if you need context to interpret results responsibly"]
}

RULES
- Do not do math you cannot justify from artifacts. If uncertain, mark confidence low.
- No invented baselines or datasets.
