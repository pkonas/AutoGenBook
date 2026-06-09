TASK
Assess novelty for the selected idea and build a related-work scaffold for the eventual paper.

SELECTED IDEA
{selected_idea_json}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT CONTRACT (JSON ONLY)
{
  "related_work": [
    {
      "cite_key": "provided_in_excerpt_header_only",
      "title": "...",
      "year": "...",
      "key_takeaways": ["..."],
      "closest_overlap": "what overlaps with our idea",
      "difference": "how our idea differs (if it does)",
      "evidence_rids": ["RID:..."]
    }
  ],
  "novelty_risk": {
    "level": "low|medium|high",
    "reasons": ["..."],
    "confounders": ["..."],
    "pivot_suggestions": ["..."]
  },
  "positioning_statement": {
    "problem_gap": "...",
    "our_angle": "...",
    "why_now": "optional",
    "evidence_rids": ["RID:..."]
  },
  "must_cite": ["cite_key1", "cite_key2"],
  "retrieval_queries": ["more queries if needed"]
}

RULES
- Use ONLY provided cite_key values. If none exist, do not invent them; ask via retrieval_queries.
- If you cannot establish novelty, say so and propose pivots.
