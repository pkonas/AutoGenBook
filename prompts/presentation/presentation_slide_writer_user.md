TASK
Write the Markdown BODY content for ONE slide. Do NOT include the slide heading.

PRESENTATION CONTEXT
- Title: {presentation_title}
- Summary: {presentation_summary}
- Audience: {presentation_audience}
- Duration: {presentation_duration} minutes
- Style guidance: {presentation_style}

OUTLINE (slide graph snapshot)
{outline_text}

PREVIOUS SLIDES (for continuity)
{previous_slides}

RETRIEVED CONTEXT (KB + optional web; no citations unless enabled)
{retrieved_context}

SCIENTIFIC SOURCES (MCP papers; citations only if enabled)
{scientific_sources}

CITATIONS
- enabled: {citations_enabled}
- style: Harvard (Author Year)

GENERAL KNOWLEDGE MARKER
- disable_general_knowledge_citation: {disable_general_knowledge_citation}

SLIDE TO WRITE NOW
- node_key: {node_key}
- Title: {slide_title}
- Summary: {slide_summary}
- Length target: {n_pages} slide(s)

SLIDE DRAFT (optional; if non-empty, use as base text)
{slide_draft}

STRICT RULES
- Use KB excerpts as primary source of facts and definitions.
- If citations are enabled, cite only scientific sources from the MCP list using Harvard style, e.g., "(Novak 2021)" or "(Novak and Lee 2021)" or "(Novak et al. 2021)".
- If citations are disabled, do NOT include any citations or source markers.
- Never invent sources. If no scientific sources are available, omit citations.
- Never mention KB or knowledge base in the slide text (no "KB", "see KB", "(KB)", "viz KB").
- If disable_general_knowledge_citation is true, never output any "General background knowledge" marker or variant.
- If a claim is unsupported, omit it instead of adding a fallback marker.
- Keep the slide concise: 3-6 bullets is typical. Avoid long paragraphs.
- Prefer action-oriented bullets and short phrases.
- Do not repeat information already covered in previous slides; assume the audience remembers it.
- Keep to a maximum of 10 non-empty lines of text.
- The content must be scientific, innovative, and deep; prioritize mathematical, algorithmic, or programming explanations.
- If slide_draft is non-empty, preserve its structure and claims; only refine wording and add grounded sources.
- Output only Markdown body (no fences, no headings).
