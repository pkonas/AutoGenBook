TASK
Write the LaTeX BODY content for ONE book section node. Do NOT include the heading.

GLOBAL BOOK CONTEXT
- Book title: {book_title}
- Book summary: {book_summary}
- Target readers: {target_readers}
- Additional requirements: {additional_requirements}
- Equation usage guidance: {equation_frequency}

STRUCTURE CONTEXT (outline + summaries)
{toc_and_summary}

PREVIOUS SECTIONS (for continuity)
{previous_sections}

CONTEXT MEMORY (for global consistency)
{context_memory_excerpt}

KNOWLEDGE BASE EXCERPTS (highest priority)
{retrieved_context}

SECTION TO WRITE NOW
- node_key: {node_key}
- Title: {section_title}
- Summary: {section_summary}
- Length target: {n_pages} pages (~{n_pages}x40 lines)

SECTION DRAFT (optional; if non-empty, use as base text)
{section_draft}

STRICT RULES
- Use KB excerpts as primary source of facts, definitions, examples.
- If you use an excerpt for an important claim, add \cite{<cite_key or RID>} near the sentence.
- If not supported, omit or label as general background.
- Prefer practical, working examples (step-by-step) consistent with earlier terminology.
- If section_draft is non-empty, preserve its structure and claims; only refine wording and add grounded citations.
- Output only LaTeX body.

OUTPUT FORMAT
```tex
...LaTeX content...
```
