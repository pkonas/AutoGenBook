TASK
Revise the paper LaTeX to address the review. Keep edits minimal and targeted.

INPUTS
- Original LaTeX:
{paper_latex}

- Review JSON:
{review_json}

- Audit issues (if any):
{audit_issues_json}

- Evidence pack:
plan: {plan_json}
analysis: {analysis_json}
literature: {literature_json}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT FORMAT (LaTeX ONLY)
Return exactly:

```tex
...FULL revised LaTeX source (or section body if you revise section-by-section)...
```

RULES

* Do not add new citations unless cite_key exists in excerpts.
* If a claim is unsupported, remove it or qualify it and move to Limitations.
* Maintain LaTeX correctness (no broken environments).
* If audit issues are provided, address them directly and minimally.
