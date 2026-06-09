from __future__ import annotations

PAPER_JSON_FROM_TXT = r"""
You will receive a paper specification written by the user in plain text.
Your task: convert it into a single JSON object that contains ALL parameters needed to generate the paper.

IMPORTANT:
- Output MUST be valid JSON (no trailing comments).
- Output MUST be ONLY the JSON object (no extra text).
- Use the SAME language as the user's specification.

The JSON schema you MUST produce:

{{
  "title": string,
  "abstract": string,                 // 150-300 words
  "keywords": [string, ...],          // 3-8 items
  "target_venue": string,
  "contributions": [string, ...],     // 2-6 items
  "citation_style": "bibtex" | "footnote",
  "max_depth": integer,               // default 3
  "max_output_pages": number,         // default 1.5
  "sections": [
    {{
      "title": string,
      "summary": string,
      "n_pages": number,
      "needsSubdivision": boolean
    }}
  ]
}}

If some parameters are not explicitly specified, choose sensible defaults.
Prefer an IMRaD structure (Introduction, Related Work, Methods, Results, Discussion, Conclusion) unless the user specifies otherwise.

Knowledge Base Excerpts (highest priority, may be empty):
{kb_context}

User paper specification (plain text):
{txt_spec}
""".strip()


PAPER_SECTION_LIST_CREATION = r"""
You are designing the internal structure of a scientific paper.

Context (about the whole paper):
Title: {paper_title}
Abstract: {paper_abstract}
Target venue: {target_venue}
Contributions: {contributions}

Now subdivide the following part into a list of subparts.

Parent section title: {target}
Parent section summary: {section_summary}
Allocated pages for the parent section: {n_pages}

Rules:
- Use the SAME language as the paper.
- Create an ordered list of subparts in JSON ARRAY format.
- Each item: {"title": "...", "summary": "...", "n_pages": 0.0, "needsSubdivision": true/false}
- Page counts should be in 0.1 increments and sum approximately to {n_pages} (±0.2).
- Do NOT number titles (no "1.", "Section", etc.).
- Set needsSubdivision=true if the subpart is still broad or > {max_output_pages} pages.

Optional Knowledge Base Excerpts (highest priority, may be empty):
{kb_context}

Output ONLY a JSON array, nothing else.
""".strip()


PAPER_SECTION_CONTENT_CREATION = r"""
You are writing a scientific paper section in LaTeX.

GLOBAL PAPER CONTEXT:
Title: {paper_title}
Abstract: {paper_abstract}
Target venue: {target_venue}
Contributions: {contributions}

STRUCTURE CONTEXT (outline + summaries):
{toc_and_summary}

PREVIOUS SECTIONS (to maintain continuity, may be empty):
{previous_sections}

KNOWLEDGE BASE EXCERPTS (highest priority ground truth; may be empty):
{kb_context}

RETRIEVED EXCERPTS (KB + web; may be empty):
{retrieved_excerpts}

RELATED WORK CONTEXT (if any; only for Related Work section):
{related_work_context}

STRICT RULES:
- Use the Knowledge Base Excerpts as the PRIMARY source of facts, definitions, formulas, and examples.
- Do NOT contradict the Knowledge Base Excerpts.
- If a claim is not supported by the Knowledge Base Excerpts, either omit it or mark it as general background knowledge.
- Use the SAME language as the paper.
- Output ONLY the main content WITHOUT headings.
- Use equation or align environments for math; do not nest equations.
- For code, use lstlisting environment (e.g., \begin{lstlisting}[language=Python] ...).
- Use ONLY the provided citation keys (if any). Do NOT invent citations.
- If a claim relies on retrieved excerpts, cite the exact cite_key provided.
- If this section is "Related Work", cite ONLY the provided related work bibtex keys.

Citation guidance:
{citation_instructions}

SECTION TO WRITE NOW:
Title: {target}
Section summary: {section_summary}
Length target: {n_pages} pages (~{n_pages}*40 lines)

Output format MUST be exactly:

```tex
...LaTeX content...
```
""".strip()
