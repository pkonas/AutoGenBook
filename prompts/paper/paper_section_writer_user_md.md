TASK
Write the Markdown BODY content for ONE paper section node. Do NOT include a section heading.
Write in a formal academic style.

PAPER CONTEXT
- Title: {paper_title}
- Target venue: {paper_venue}
- Abstract (draft): {paper_abstract}
- Keywords: {paper_keywords}

OUTLINE (paper graph snapshot)
{paper_outline_text}

CURRENT SECTION NODE
- node_key: {node_key}
- title: {section_title}
- summary: {section_summary}
- target_length_pages: {n_pages} (approx ~40 lines/page)

SECTION DRAFT (optional; if non-empty, use as base text)
{section_draft}

EVIDENCE PACK (authoritative)
- Plan: {plan_json}
- Literature: {literature_json}
- Analysis: {analysis_json}
- Figures available: {figures_manifest}
- Tables available: {tables_manifest}

{GLOBAL_EVIDENCE_INSTRUCTIONS}

RETRIEVED EXCERPTS:
{retrieved_context}

STRICT RULES
- Every important claim, definition, or number must be supported by citations to provided cite_key OR run artifacts.
- Use \cite{cite_key} only if cite_key exists in excerpt headers.
- If cite_key is missing but excerpt is used, insert a footnote: \footnote{Source: RID:...}.
- Do not cite anything not provided.
- Avoid overclaiming. If evidence is limited, say so and move it to Limitations.
- If section_draft is non-empty, preserve its structure and claims; only refine wording and add grounded citations.
- Do not use raw LaTeX except \cite{...} or \footnote{...}.
- Output only Markdown body (no fences).
