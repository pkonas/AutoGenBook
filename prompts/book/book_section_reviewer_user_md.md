TASK
Review the new section and decide whether it is acceptable for a serious university-level book chapter.

INPUTS
- Section:
title: {section_title}
node_key: {node_key}
markdown_body: {section_latex}

- Context memory excerpt:
{context_memory_excerpt}

- Previous sections excerpt:
{previous_sections}

- Outline context:
{toc_and_summary}

RETRIEVED EXCERPTS:
{retrieved_context}

REVIEW CRITERIA
1) Grounding: unsupported factual claims, invented specifics, weak evidence usage.
2) Consistency: terminology drift, contradiction with previous sections, mismatch with outline scope.
3) Clarity: fragmented prose, excessive bullets, outline-like writing, raw scaffold labels, poor paragraphing, hard-wrapped text, abrupt topic jumps.
4) Pedagogy: insufficient explanation of mechanisms, assumptions, implications, or limitations for an advanced academic reader.
5) Style quality: language mixing, colloquial tone, repetitive phrasing, meta commentary, or literal phrases such as "general background knowledge" appearing inside the prose.

OUTPUT CONTRACT (JSON ONLY)
{
  "ok_to_keep": true|false,
  "issues": [
    {
      "type": "grounding|consistency|clarity|pedagogy",
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
  "retrieval_queries": ["...if more evidence is needed"]
}

RULES
- Set ok_to_keep=false for any major problem in grounding, coherence, academic tone, or continuity.
- Treat outline-like note writing, excessive bullets, sentence fragments, raw labels such as "Content:" or "Sources:", and literal "general background knowledge" markers as defects, not acceptable final prose.
- If the section is factually grounded but still reads like notes rather than finished scholarship, return ok_to_keep=false and request a rewrite.
- Use "clarity" for prose-quality and language problems, "consistency" for continuity/terminology issues, and "pedagogy" for missing explanation or framing.
- If the section can be fixed without new evidence, leave retrieval_queries empty.
