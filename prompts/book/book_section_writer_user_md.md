TASK
Write the Markdown BODY content for exactly one book section node. Do not include the heading.
Write as a polished academic section, not as notes, not as an outline, and not as a list of talking points.

GLOBAL BOOK CONTEXT
- Book title: {book_title}
- Book summary: {book_summary}
- Target readers: {target_readers}
- Additional requirements: {additional_requirements}
- Equation usage guidance: {equation_frequency}

STRUCTURE CONTEXT
{toc_and_summary}

PREVIOUS SECTIONS
{previous_sections}

CONTEXT MEMORY
{context_memory_excerpt}

RETRIEVED EXCERPTS (highest priority)
{retrieved_context}

SECTION TO WRITE NOW
- node_key: {node_key}
- Title: {section_title}
- Summary: {section_summary}
- Length target: {n_pages} pages (~{n_pages}x40 lines)

SECTION DRAFT (optional; if non-empty, use as base text)
{section_draft}

WRITING EXPECTATION
- Produce continuous university-level prose with a clear internal arc: opening orientation, analytical development, and a short closing synthesis or transition where natural.
- Integrate the section into the surrounding chapter. When useful, begin by linking the topic to the preceding argument and end by preparing the next section.
- Convert outline items, content notes, framework bullets, and source lists into finished prose. Do not echo labels such as "Content:", "Sources:", "Framework:", "Practical note:", or similar scaffolding unless the section genuinely needs such a subheading.
- Prefer paragraphs over bullet lists. Use a list only when the reader benefits from explicit ordered steps, a compact taxonomy, or a concise comparison. Avoid nested bullet lists.
- Explain mechanisms, assumptions, limitations, and implications. Do not merely enumerate phenomena.
- Keep terminology stable across the section and consistent with previous sections. Introduce a term once, then use it consistently.
- Maintain one language throughout the section, matching the book context.

CITATIONS AND EVIDENCE
- Use retrieved excerpts as the primary source of facts, definitions, examples, and literature-supported interpretations.
- Cite important factual claims close to the sentence using \cite{cite_key} when a cite_key is available.
- If a retrieved excerpt is useful but only provides RID and no cite_key, you may use \footnote{Source: RID:...}.
- Do not cite anything that is not present in the retrieved excerpts or run artifacts.
- If evidence is insufficient for a non-trivial claim, omit the claim or rewrite it cautiously as generic domain context without using the literal phrase "general background knowledge".
- When evidence is limited or mixed, state that limitation explicitly in scholarly prose.

STRICT STYLE RULES
- Do not produce chatty explanations, motivational language, or meta commentary.
- Do not write placeholder text, TODO markers, or author instructions.
- Do not hard-wrap prose line by line. Write normal Markdown paragraphs separated by blank lines.
- Do not overuse bullets, sentence fragments, or heading-like labels inside the section body.
- Avoid repeating the section title in the first sentence unless rhetorically necessary.
- If section_draft is non-empty, preserve its supported technical substance, but you may substantially rewrite structure and wording to improve coherence, continuity, tone, and readability.

OUTPUT
- Return only the Markdown body.
- No code fences.
