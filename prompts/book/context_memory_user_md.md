TASK
Update context memory from a new section.

INPUTS
- Existing context_memory.json:
{context_memory_json}

- New section:
title: {section_title}
node_key: {node_key}
markdown_body: {section_latex}

- Outline snapshot:
{book_outline_text}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

OUTPUT CONTRACT (JSON ONLY)
{
  "terms_added": [
    {
      "term": "...",
      "definition": "... (from section only)",
      "first_seen_node": "{node_key}"
    }
  ],
  "terms_updated": [
    {
      "term": "...",
      "old_definition": "...",
      "new_definition": "...",
      "reason": "clarification or correction"
    }
  ],
  "citations_used": [
    {
      "cite_key_or_rid": "...",
      "where": "short hint",
      "type": "kb|web|run"
    }
  ],
  "open_threads": [
    {
      "thread": "something promised but not resolved",
      "suggested_future_node": "node key or section title"
    }
  ],
  "consistency_flags": [
    {
      "type": "terminology|notation|claim",
      "description": "possible inconsistency to watch",
      "severity": "low|medium|high"
    }
  ],
  "retrieval_queries": ["... if you need more evidence to resolve flagged issues"]
}

RULES
- Only extract what is in the section.
- Do not hallucinate definitions.
