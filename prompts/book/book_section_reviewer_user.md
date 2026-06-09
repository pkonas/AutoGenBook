TASK
Review the new section and propose required fixes.

INPUTS
- Section:
title: {section_title}
node_key: {node_key}
latex_body: {section_latex}

- Context memory excerpt:
{context_memory_excerpt}

- Previous sections excerpt:
{previous_sections}

- Outline context:
{toc_and_summary}

RETRIEVED EXCERPTS:
{retrieved_context}

OUTPUT CONTRACT (JSON ONLY)
{
  "ok_to_keep": true|false,
  "issues": [
    {
      "type": "grounding|consistency|latex|clarity|pedagogy",
      "severity": "major|minor",
      "description": "...",
      "required_fix": "..."
    }
  ],
  "suggested_edits": [
    {
      "target": "paragraph hint",
      "edit_instruction": "..."
    }
  ],
  "retrieval_queries": ["...if you need more evidence to fix grounding"]
}

RULES
- If there are unsupported claims, set ok_to_keep=false unless they can be trivially qualified/removed.
