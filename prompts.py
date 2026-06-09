
from __future__ import annotations

BOOK_JSON_FROM_TXT = r"""
You will receive a book specification written by the user in plain text.
Your task: convert it into a single JSON object that contains ALL parameters needed to generate the book.

IMPORTANT:
- Output MUST be valid JSON (no trailing comments).
- Output MUST be ONLY the JSON object (no extra text).
- Use the SAME language as the user's specification.
- Do NOT number chapter titles (no "Chapter 1: ...").

If some parameters are not explicitly specified, choose sensible defaults.

The JSON schema you MUST produce:

{{
  "title": string,                    // book title
  "summary": string,                  // 5-10 detailed sentences
  "n_pages": number,                  // total approximate pages (float or int)
  "target_readers": string,           // may be empty
  "equation_frequency_level": integer,// 1..5
  "do_consider_outline": boolean,
  "do_consider_previous_sections": boolean,
  "additional_requirements": string,  // may be empty
  "max_depth": integer,               // default 5
  "max_output_pages": number,         // default 1.5
  "childs": [                         // chapters
    {{
      "title": string,
      "summary": string,
      "n_pages": number,              // allocate pages; sum approx equals book n_pages (±0.5)
      "needsSubdivision": boolean
    }}
  ]
}}

You also have access to an OPTIONAL Knowledge Base excerpt block.
If provided, treat it as highest-priority reference material to shape the outline and summaries.

Knowledge Base Excerpts (highest priority, may be empty):
{kb_context}

User book specification (plain text):
{txt_spec}
""".strip()


SECTION_LIST_CREATION = r"""
You are designing the internal structure of a textbook.

Context (about the whole book):
Title: {book_title}
Book summary: {book_summary}
Target readers: {target_readers}

Now subdivide the following part into a list of subparts.

Parent part title: {target}
Parent part summary: {section_summary}
Allocated pages for the parent part: {n_pages}

Rules:
- Use the SAME language as the book.
- Create an ordered list of subparts in JSON ARRAY format.
- Each item: {{"title": "...", "summary": "...", "n_pages": 0.0, "needsSubdivision": true/false}}
- Page counts should be in 0.1 increments and sum approximately to {n_pages} (±0.2).
- Do NOT number titles (no "1.", "Chapter", etc.).
- Set needsSubdivision=true if the subpart is still broad or > {max_output_pages} pages.

Optional Knowledge Base Excerpts (highest priority, may be empty):
{kb_context}

Output ONLY a JSON array, nothing else.
""".strip()


SECTION_CONTENT_CREATION = r"""
You are writing a textbook section in LaTeX.

GLOBAL BOOK CONTEXT:
Book title: {book_title}
Book summary: {book_summary}
Total pages (approx): {book_pages} (estimate ~40 lines per page)
Intended readers: {target_readers}
Additional requirements: {additional_requirements}

STRUCTURE CONTEXT (outline + summaries):
{toc_and_summary}

PREVIOUS SECTIONS (to maintain continuity, may be empty):
{previous_sections}

CONTEXT MEMORY (terms/citations to stay consistent, may be empty):
{context_memory}

KNOWLEDGE BASE EXCERPTS (highest priority ground truth; may be empty):
{kb_context}

STRICT RULES:
- Use the Knowledge Base Excerpts as the PRIMARY source of facts, definitions, formulas, and examples.
- If you rely on a specific excerpt for an important claim/definition/formula, add a short LaTeX footnote like \\footnote{{Source: <filename> (<loc>)}} near the relevant sentence.
- Reuse definitions and term labels consistently with Context Memory when applicable.
- If something is not supported by the Knowledge Base Excerpts, either omit it or clearly mark it as general background knowledge.
- Do NOT contradict the Knowledge Base Excerpts.
- Do NOT include speculative or unverified information.
- Write in the SAME language as the book specification.
- Output ONLY the main content WITHOUT headings.
- Length target: {n_pages} pages (~{n_pages}×40 lines).
- Use equation or align environments for math; do not nest equations.
- For code, use lstlisting environment (e.g., \\begin{{lstlisting}}[language=Python] ...).
- Escape '#' as '\\#'.

Equation usage guidance:
{equation_frequency}

SECTION TO WRITE NOW:
Title: {target}
Section summary: {section_summary}

Output format MUST be exactly:

```tex
...LaTeX content...
```
""".strip()
