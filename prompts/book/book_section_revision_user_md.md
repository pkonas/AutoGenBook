TASK
Revise the section Markdown body so that it satisfies the reviewer feedback and reads like a finished academic monograph section.

INPUTS
- Section title: {section_title}
- Node key: {node_key}

- Original markdown_body:
{section_latex}

- Reviewer feedback JSON:
{review_json}

- Previous sections excerpt:
{previous_sections}

- Outline context:
{toc_and_summary}

- Context memory excerpt:
{context_memory_excerpt}

RETRIEVED EXCERPTS:
{retrieved_context}

REVISION GOALS
- Resolve every major reviewer issue.
- Improve continuity, paragraph structure, and scholarly tone.
- Replace outline-like fragments with continuous prose.
- Remove raw scaffold labels, meta comments, and the literal phrase "general background knowledge".
- Keep the section aligned with the outline and surrounding sections.
- Preserve supported technical claims and existing valid citations; add citations only when supported by the retrieved excerpts.

STRICT RULES
- Do not invent facts or citations.
- Do not add claims that are not supported by the evidence pack.
- If evidence for a claim is insufficient, remove it or rewrite it cautiously.
- Prefer compact, well-structured paragraphs over bullet-heavy formatting.
- Do not hard-wrap prose line by line.

OUTPUT
Return only the revised Markdown body, with no code fences.
