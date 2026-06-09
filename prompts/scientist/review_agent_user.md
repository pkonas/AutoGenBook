TASK
Review the draft paper and return a structured review.

VENUE CRITERIA
- Emphasize correctness, evidence-grounding, experimental validity, and clarity.
- Penalize hallucinated citations or unsupported claims heavily.

INPUTS
- Paper LaTeX (compiled draft text or LaTeX source):
{paper_latex}

- Plan and evidence:
plan: {plan_json}
analysis: {analysis_json}
literature: {literature_json}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

OUTPUT CONTRACT (JSON ONLY)
{
  "overall_summary": "2-4 sentences",
  "scores": {
    "novelty": 1-10,
    "technical_quality": 1-10,
    "clarity": 1-10,
    "evidence_grounding": 1-10,
    "reproducibility": 1-10
  },
  "strengths": ["..."],
  "weaknesses": ["..."],
  "major_issues": [
    {
      "issue": "...",
      "why_it_matters": "...",
      "where": "section/title or line hint",
      "required_change": "...",
      "severity": "major|minor"
    }
  ],
  "missing_experiments": ["..."],
  "citation_problems": ["..."],
  "suggested_revisions": [
    {
      "target": "section name",
      "instruction": "what to change",
      "priority": 1-5
    }
  ],
  "verdict": "reject|weak reject|borderline|weak accept|accept"
}

RULES
- If you find unsupported claims, list them under citation_problems and major_issues.
- Do not invent new references; request retrieval instead.
